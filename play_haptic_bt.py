"""Play a WAV file as BT haptics via Report 0x32 to DS5."""
import sys
import time
import struct
import zlib
import numpy as np
from scipy.signal import resample

try:
    import hid
except ImportError:
    print("pip install hidapi")
    sys.exit(1)

if len(sys.argv) < 2:
    print("Usage: python play_haptic_bt.py <file.wav> [gain]")
    sys.exit(1)

DS5_VID = 0x054C
DS5_PIDS = {0x0CE6, 0x0DF2}
gain = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0

# Read WAV
import wave
wf = wave.open(sys.argv[1], 'rb')
rate = wf.getframerate()
channels = wf.getnchannels()
sampwidth = wf.getsampwidth()
n_frames = wf.getnframes()
raw = wf.readframes(n_frames)
wf.close()

if sampwidth == 2:
    samples = np.frombuffer(raw, dtype=np.int16).reshape(-1, channels)
elif sampwidth == 4:
    samples = np.frombuffer(raw, dtype=np.int32).reshape(-1, channels)
    samples = (samples >> 16).astype(np.int16)
else:
    print(f"Unsupported sample width: {sampwidth}")
    sys.exit(1)

print(f"WAV: {rate}Hz, {channels}ch, {sampwidth*8}bit, {n_frames} frames ({n_frames/rate:.1f}s)")

# Use channels 3+4 if available, otherwise 1+2
if channels >= 4:
    left = samples[:, 2].astype(np.float64)
    right = samples[:, 3].astype(np.float64)
elif channels >= 2:
    left = samples[:, 0].astype(np.float64)
    right = samples[:, 1].astype(np.float64)
else:
    left = samples[:, 0].astype(np.float64)
    right = left.copy()

# Resample to 3kHz
target_samples = int(len(left) * 3000 / rate)
print(f"Resample: {rate}Hz -> 3kHz ({len(left)} -> {target_samples} samples)")
left_3k = resample(left, target_samples)
right_3k = resample(right, target_samples)

# Convert s16 -> u8 with gain
def s16_to_s8(val, g):
    return int(np.clip(int(val * g) >> 8, -128, 127))

s8_data = bytearray()
for i in range(target_samples):
    l = int(np.clip(left_3k[i], -32768, 32767))
    r = int(np.clip(right_3k[i], -32768, 32767))
    s8_data.append(s16_to_s8(l, gain))
    s8_data.append(s16_to_s8(r, gain))

print(f"Output: {len(s8_data)} bytes ({len(s8_data)/2} stereo samples)")
print(f"Gain: x{gain}")

# Find DS5
dev_info = None
for info in hid.enumerate(DS5_VID):
    if info['product_id'] in DS5_PIDS:
        dev_info = info
        break
if not dev_info:
    print("No DS5 found")
    sys.exit(1)

dev = hid.device()
dev.open_path(dev_info['path'])
test = dev.read(128, 500)
is_bt = len(test) > 64
if not is_bt:
    print("DS5 is USB, not BT. This script is for BT only.")
    dev.close()
    sys.exit(1)
print(f"DS5: BT connected")

# CRC
def ds5_bt_crc32(data):
    return zlib.crc32(bytes([0xA2]) + data) & 0xFFFFFFFF

# Send at 93.75 Hz (10.67ms per packet, 32 stereo samples = 64 bytes per packet)
INTERVAL_NS = 10_666_666
REPORT_ID = 0x32
PAYLOAD_SIZE = 136
seq = 0
offset = 0
packets = 0

print(f"\nPlaying... ({target_samples/3000:.1f}s)")
next_ns = time.monotonic_ns()

while offset + 64 <= len(s8_data):
    # Wait for next tick
    next_ns += INTERVAL_NS
    now = time.monotonic_ns()
    wait = next_ns - now
    if wait > 2_000_000:
        time.sleep((wait - 1_000_000) / 1_000_000_000)
    while time.monotonic_ns() < next_ns:
        pass

    audio = bytes(s8_data[offset:offset+64])
    offset += 64

    pkt_0x11 = bytes([
        (0x11 & 0x3F) | (1 << 7), 7,
        0b11111110, 0, 0, 0, 0, seq & 0xFF, 0
    ])
    pkt_0x12_header = bytes([(0x12 & 0x3F) | (1 << 7), 64])
    packet_data = pkt_0x11 + pkt_0x12_header + audio
    payload = packet_data.ljust(PAYLOAD_SIZE, b'\x00')
    tag_seq = (seq & 0x0F) << 4
    report_body = bytes([tag_seq]) + payload
    crc = ds5_bt_crc32(bytes([REPORT_ID]) + report_body)
    report = bytes([REPORT_ID]) + report_body + struct.pack('<I', crc)
    dev.write(report)
    seq = (seq + 1) & 0x0F
    packets += 1

print(f"Done. {packets} packets sent.")
dev.close()
