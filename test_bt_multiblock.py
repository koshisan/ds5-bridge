"""Test: send multiple pkt_0x12 audio blocks per Report 0x32 via hidapi."""
import sys, time, struct, zlib, math, hid

DS5_VID = 0x054C
DS5_PIDS = {0x0CE6, 0x0DF2}
SAMPLE_SIZE = 64
REPORT_ID = 0x32

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

rawfile = sys.argv[1] if len(sys.argv) > 1 else 'haptics_test_3k_s8_g8.raw'
num_blocks = int(sys.argv[2]) if len(sys.argv) > 2 else 4
raw = open(rawfile, 'rb').read()
print(f"File: {len(raw)} bytes, {num_blocks} audio blocks per report")

seq = 0
offset = 0
packets = 0
start = time.perf_counter()

while offset + SAMPLE_SIZE * num_blocks <= len(raw):
    # Build report with multiple pkt_0x12 blocks
    pkt_0x11 = bytes([
        (0x11 & 0x3F) | (1 << 7), 7,
        0xFE, 0, 0, 0, 0, seq & 0xFF, 0
    ])

    audio_packets = b''
    for i in range(num_blocks):
        chunk = raw[offset:offset+SAMPLE_SIZE]
        offset += SAMPLE_SIZE
        pkt_0x12 = bytes([(0x12 & 0x3F) | (1 << 7), SAMPLE_SIZE]) + chunk
        audio_packets += pkt_0x12

    payload = pkt_0x11 + audio_packets
    # Pad to 136 bytes (REPORT_SIZE - 1 report_id - 4 crc)
    payload = payload.ljust(136, b'\x00')

    tag_seq = (seq & 0x0F) << 4
    body = bytes([tag_seq]) + payload
    crc = ds5_bt_crc32(bytes([REPORT_ID]) + body)
    report = bytes([REPORT_ID]) + body + struct.pack('<I', crc)

    dev.write(report)
    seq = (seq + 1) & 0x0F
    packets += 1

elapsed = time.perf_counter() - start
print(f"{packets} packets in {elapsed:.2f}s = {packets/elapsed:.0f} Hz")
print(f"Audio consumed: {offset} bytes")

dev.close()
