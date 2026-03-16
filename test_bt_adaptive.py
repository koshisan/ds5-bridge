"""Adaptive timing: measure actual write duration and adjust."""
import sys, time, struct, zlib, math, hid

DS5_VID = 0x054C
DS5_PIDS = {0x0CE6, 0x0DF2}
dev_info = None
for info in hid.enumerate(DS5_VID):
    if info['product_id'] in DS5_PIDS:
        dev_info = info
        break
dev = hid.device()
dev.open_path(dev_info['path'])
test = dev.read(128, 500)
if len(test) <= 64:
    print("Not BT!"); sys.exit(1)

def ds5_bt_crc32(data):
    return zlib.crc32(bytes([0xA2]) + data) & 0xFFFFFFFF

seq = 0
def send_haptic(audio):
    global seq
    REPORT_ID = 0x32
    pkt_0x11 = bytes([(0x11&0x3F)|(1<<7), 7, 0b11111110, 0, 0, 0, 0, seq&0xFF, 0])
    pkt_0x12 = bytes([(0x12&0x3F)|(1<<7), 64])
    payload = (pkt_0x11 + pkt_0x12 + audio).ljust(136, b'\x00')
    tag_seq = (seq & 0x0F) << 4
    body = bytes([tag_seq]) + payload
    crc = ds5_bt_crc32(bytes([REPORT_ID]) + body)
    report = bytes([REPORT_ID]) + body + struct.pack('<I', crc)
    dev.write(report)
    seq = (seq + 1) & 0x0F

import wave
import numpy as np
from scipy.signal import resample_poly

wavfile = sys.argv[1] if len(sys.argv) > 1 else 'haptics_test.wav'
gain = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0

TARGET_INTERVAL = 1.0 / 93.75  # 10.67ms

wf = wave.open(wavfile, 'rb')
rate = wf.getframerate()
channels = wf.getnchannels()
raw = wf.readframes(wf.getnframes())
wf.close()

samples = np.frombuffer(raw, dtype=np.int16).reshape(-1, channels)
left = samples[:, 0].astype(np.float64)
right = samples[:, 1].astype(np.float64) if channels >= 2 else left.copy()

# Resample to 3kHz
target_len = int(len(left) * 3000 / rate)
left_3k = resample_poly(left, 3000, rate)[:target_len]
right_3k = resample_poly(right, 3000, rate)[:target_len]

# S16 -> S8 with gain
all_s8 = bytearray(target_len * 2)
for i in range(target_len):
    l = int(np.clip(left_3k[i] * gain / 256.0, -128, 127))
    r = int(np.clip(right_3k[i] * gain / 256.0, -128, 127))
    all_s8[i*2] = l & 0xFF
    all_s8[i*2+1] = r & 0xFF

duration = target_len / 3000.0
print(f"WAV: {wavfile}, {rate}Hz -> 3kHz, {target_len} samples ({duration:.1f}s), gain={gain}")

# Phase 1: measure average write time
print("Measuring write timing...")
times = []
for i in range(30):
    t0 = time.perf_counter()
    send_haptic(bytes(all_s8[i*64:(i+1)*64]))
    t1 = time.perf_counter()
    times.append(t1 - t0)

avg_write = sum(times) / len(times)
max_write = max(times)
min_write = min(times)
print(f"Write time: avg={avg_write*1000:.1f}ms min={min_write*1000:.1f}ms max={max_write*1000:.1f}ms")

# Phase 2: send with adaptive sleep
# If write takes less than TARGET, sleep the remaining time
# If write takes more, send immediately
offset = 30 * 64  # skip the measurement packets
packets = 30
start = time.perf_counter()

print("Playing with adaptive timing...")
while offset + 64 <= len(all_s8):
    t0 = time.perf_counter()
    send_haptic(bytes(all_s8[offset:offset+64]))
    t1 = time.perf_counter()
    write_time = t1 - t0

    # Sleep remaining time to hit TARGET_INTERVAL
    remaining = TARGET_INTERVAL - write_time
    if remaining > 0.001:
        time.sleep(remaining - 0.0005)
        # Spin for last 0.5ms
        target = t0 + TARGET_INTERVAL
        while time.perf_counter() < target:
            pass

    offset += 64
    packets += 1

elapsed = time.perf_counter() - start
print(f"{packets} packets in {elapsed:.2f}s = {packets/elapsed:.0f} Hz")

send_haptic(bytes(64))
dev.close()
