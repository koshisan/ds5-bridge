"""Blast Report 0x32 as fast as possible - no timing, just write()."""
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
offset = 0
packets = 0
start = time.perf_counter()

print("Blasting 200Hz sine for 5 seconds...")
while time.perf_counter() - start < 5.0:
    audio = bytearray(64)
    for i in range(32):
        t = (offset + i) / 3000.0
        val = int(64 * math.sin(2 * math.pi * 200 * t)) + 128
        audio[i*2] = max(0, min(255, val))
        audio[i*2+1] = max(0, min(255, val))
    
    REPORT_ID = 0x32
    pkt_0x11 = bytes([(0x11 & 0x3F)|(1<<7), 7, 0b11111110, 0, 0, 0, 0, seq & 0xFF, 0])
    pkt_0x12 = bytes([(0x12 & 0x3F)|(1<<7), 64])
    payload = (pkt_0x11 + pkt_0x12 + bytes(audio)).ljust(136, b'\x00')
    tag_seq = (seq & 0x0F) << 4
    body = bytes([tag_seq]) + payload
    crc = ds5_bt_crc32(bytes([REPORT_ID]) + body)
    report = bytes([REPORT_ID]) + body + struct.pack('<I', crc)
    
    dev.write(report)
    seq = (seq + 1) & 0x0F
    offset += 32
    packets += 1

elapsed = time.perf_counter() - start
print(f"{packets} packets in {elapsed:.1f}s = {packets/elapsed:.0f} Hz")
print("Smooth or ratty?")
dev.close()
