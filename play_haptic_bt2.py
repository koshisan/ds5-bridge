"""Play WAV as BT haptics - no sleep, continuous stream."""
import sys
import time
import struct
import zlib
import numpy as np
from scipy.signal import resample
import hid

if len(sys.argv) < 2:
    print("Usage: python play_haptic_bt2.py <file.wav> [gain]")
    sys.exit(1)

DS5_VID = 0x054C
DS5_PIDS = {0x0CE6, 0x0DF2}
gain = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0

import wave
wf = wave.open(sys.argv[1], 'rb')
rate = wf.getframerate()
channels = wf.getnchannels()
sampwidth = wf.getsampwidth()
raw = wf.readframes(wf.getnframes())
wf.close()

samples = np.frombuffer(raw, dtype=np.int16).reshape(-1, channels)
if channels >= 2:
    left = samples[:, 0].astype(np.float64)
    right = samples[:, 1].astype(np.float64)
else:
    left = samples[:, 0].astype(np.float64)
    right = left.copy()

# Resample to 3kHz
target_len = int(len(left) * 3000 / rate)
left_3k = resample(left, target_len)
right_3k = resample(right, target_len)

# s16 -> u8
u8_data = bytearray()
for i in range(target_len):
    l = int(np.clip(left_3k[i] * gain, -32768, 32767))
    r = int(np.clip(right_3k[i] * gain, -32768, 32767))
    u8_data.append(((l >> 8) + 128) & 0xFF)
    u8_data.append(((r >> 8) + 128) & 0xFF)

print(f"WAV: {rate}Hz {channels}ch -> 3kHz, {target_len} samples, gain={gain}")

# Find DS5
dev_info = None
for info in hid.enumerate(DS5_VID):
    if info['product_id'] in DS5_PIDS:
        dev_info = info
        break
dev = hid.device()
dev.open_path(dev_info['path'])
test = dev.read(128, 500)
is_bt = len(test) > 64
if not is_bt:
    print("Not BT!")
    sys.exit(1)

def ds5_bt_crc32(data):
    return zlib.crc32(bytes([0xA2]) + data) & 0xFFFFFFFF

# Use sounddevice as precision clock
import sounddevice as sd

REPORT_ID = 0x32
PAYLOAD_SIZE = 136
seq = [0]
offset = [0]
packets = [0]
done = [False]

# 3kHz mono clock: callback fires every ~10.67ms with 32 frames
def clock_callback(outdata, frames, time_info, status):
    if offset[0] + 64 > len(u8_data):
        done[0] = True
        raise sd.CallbackAbort

    audio = bytes(u8_data[offset[0]:offset[0]+64])
    offset[0] += 64

    s = seq[0]
    pkt_0x11 = bytes([
        (0x11 & 0x3F) | (1 << 7), 7,
        0b11111110, 0, 0, 0, 0, s & 0xFF, 0
    ])
    pkt_0x12_header = bytes([(0x12 & 0x3F) | (1 << 7), 64])
    packet_data = pkt_0x11 + pkt_0x12_header + audio
    payload = packet_data.ljust(PAYLOAD_SIZE, b'\x00')
    tag_seq = (s & 0x0F) << 4
    report_body = bytes([tag_seq]) + payload
    crc = ds5_bt_crc32(bytes([REPORT_ID]) + report_body)
    report = bytes([REPORT_ID]) + report_body + struct.pack('<I', crc)
    dev.write(report)
    seq[0] = (s + 1) & 0x0F
    packets[0] += 1

    outdata[:] = 0  # silent output

# Open a dummy output stream at 3kHz to get precise 10.67ms callbacks
print("Playing...")
stream = sd.OutputStream(
    samplerate=3000, channels=1, dtype='int16',
    blocksize=32, callback=clock_callback)
stream.start()

while not done[0]:
    time.sleep(0.1)

stream.stop()
stream.close()
dev.close()
print(f"Done. {packets[0]} packets.")
