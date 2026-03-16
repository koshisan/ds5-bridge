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

freq = float(sys.argv[1]) if len(sys.argv) > 1 else 200.0
duration = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
amplitude = int(sys.argv[3]) if len(sys.argv) > 3 else 127

TARGET_INTERVAL = 1.0 / 93.75  # 10.67ms
total_samples = int(3000 * duration)
all_s8 = bytearray(total_samples * 2)
for i in range(total_samples):
    val = int(amplitude * math.sin(2 * math.pi * freq * i / 3000.0))
    val = max(-128, min(127, val))
    all_s8[i*2] = val & 0xFF
    all_s8[i*2+1] = val & 0xFF

print(f"Sine {freq}Hz S8, {duration}s, adaptive timing")

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
