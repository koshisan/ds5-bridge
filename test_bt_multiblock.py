"""Test: multiple Report 0x32 frames in one 547-byte write, each with own CRC."""
import sys, time, struct, zlib, hid

DS5_VID = 0x054C
DS5_PIDS = {0x0CE6, 0x0DF2}
REPORT_ID = 0x32
SAMPLE_SIZE = 64
FRAME_SIZE = 141  # one complete Report 0x32 frame

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

def build_frame(audio_data, seq):
    """Build one complete 141-byte Report 0x32 frame with CRC."""
    pkt_0x11 = bytes([
        (0x11 & 0x3F) | (1 << 7), 7,
        0xFE, 0, 0, 0, 0, seq & 0xFF, 0
    ])
    pkt_0x12 = bytes([(0x12 & 0x3F) | (1 << 7), SAMPLE_SIZE]) + audio_data
    payload = (pkt_0x11 + pkt_0x12).ljust(136, b'\x00')
    tag_seq = (seq & 0x0F) << 4
    body = bytes([tag_seq]) + payload
    crc = ds5_bt_crc32(bytes([REPORT_ID]) + body)
    return bytes([REPORT_ID]) + body + struct.pack('<I', crc)

rawfile = sys.argv[1] if len(sys.argv) > 1 else 'haptics_test_3k_s8_g8.raw'
num_frames = int(sys.argv[2]) if len(sys.argv) > 2 else 3  # 141*3=423 < 547
raw = open(rawfile, 'rb').read()
print(f"File: {len(raw)} bytes, {num_frames} frames per write ({num_frames*FRAME_SIZE} bytes)")

seq = 0
offset = 0
packets = 0
start = time.perf_counter()

while offset + SAMPLE_SIZE * num_frames <= len(raw):
    # Build concatenated frames
    corrupt_after_first = '--corrupt' in sys.argv
    combined = b''
    for i in range(num_frames):
        audio = raw[offset:offset+SAMPLE_SIZE]
        offset += SAMPLE_SIZE
        frame = build_frame(audio, seq)
        seq = (seq + 1) & 0x0F
        if corrupt_after_first and i > 0:
            # Corrupt CRC (last 4 bytes)
            frame = frame[:-4] + b'\xDE\xAD\xBE\xEF'
        combined += frame

    # First byte must be Report ID for hidapi
    # Send the combined data (first frame's report ID is already 0x32)
    dev.write(combined)
    time.sleep(0.01067 * num_frames)
    packets += 1

elapsed = time.perf_counter() - start
print(f"{packets} writes, {packets*num_frames} frames in {elapsed:.2f}s")
dev.close()
