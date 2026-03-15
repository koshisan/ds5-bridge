"""Send full-range sine waves packed into packets, blast as fast as possible."""
import sys
import time
import struct
import zlib
import math
import hid

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

# Generate: 5 full sine cycles (1-255) spread over 32 mono samples per packet
# 5 cycles in 32 samples = ~6.4 samples per cycle
# Stereo: L=R
cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 5
num_packets = int(sys.argv[2]) if len(sys.argv) > 2 else 200

print(f"Generating {cycles} full sine cycles per packet (1-255)")
print(f"Blasting {num_packets} packets as fast as possible...")

audio = bytearray(64)
for i in range(32):
    val = int(128 + 127 * math.sin(2 * math.pi * cycles * i / 32))
    val = max(1, min(255, val))
    audio[i*2] = val
    audio[i*2+1] = val
audio = bytes(audio)

print(f"Samples: {[audio[i*2] for i in range(32)]}")

start = time.perf_counter()
for _ in range(num_packets):
    send_haptic(audio)
elapsed = time.perf_counter() - start

print(f"\n{num_packets} packets in {elapsed:.2f}s = {num_packets/elapsed:.0f} Hz")
print("How did it feel?")

# Silence
send_haptic(bytes([128]*64))
dev.close()
