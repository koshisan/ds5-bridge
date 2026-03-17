"""Test Report 0x33 (206 bytes) - same structure as 0x32 but larger."""
import sys, time, struct, zlib, hid

DS5_VID = 0x054C
DS5_PIDS = {0x0CE6, 0x0DF2}
SAMPLE_SIZE = 64

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
report_id = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x33
raw = open(rawfile, 'rb').read()

# Report sizes from BT descriptor
report_sizes = {
    0x31: 78, 0x32: 142, 0x33: 206, 0x34: 270,
    0x35: 334, 0x36: 398, 0x37: 462, 0x38: 526, 0x39: 547
}
report_size = report_sizes.get(report_id, 142)
payload_size = report_size - 1 - 1 - 4  # minus ID and CRC
print(f"Report 0x{report_id:02X}: {report_size} bytes, payload {payload_size}")

seq = 0
offset = 0
packets = 0

while offset + SAMPLE_SIZE <= len(raw):
    audio = raw[offset:offset+SAMPLE_SIZE]
    offset += SAMPLE_SIZE

    pkt_0x11 = bytes([
        (0x11 & 0x3F) | (1 << 7), 7,
        0xFE, 0, 0, 0, 0, seq & 0xFF, 0
    ])
    pkt_0x12 = bytes([(0x12 & 0x3F) | (1 << 7), SAMPLE_SIZE]) + audio
    payload = (pkt_0x11 + pkt_0x12).ljust(payload_size, b'\x00')

    tag_seq = (seq & 0x0F) << 4
    body = bytes([tag_seq]) + payload
    crc = ds5_bt_crc32(bytes([report_id]) + body)
    report = bytes([report_id]) + body + struct.pack('<I', crc)

    dev.write(report)
    seq = (seq + 1) & 0x0F
    packets += 1
    time.sleep(0.01067)

print(f"{packets} packets sent")
dev.close()
