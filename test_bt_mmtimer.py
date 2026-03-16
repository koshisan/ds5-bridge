"""Play S8 raw with Windows multimedia timer for precise 1ms sleep."""
import sys, time, struct, zlib, ctypes, hid

# Set Windows timer resolution to 1ms
winmm = ctypes.WinDLL('winmm')
winmm.timeBeginPeriod(1)

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

rawfile = sys.argv[1] if len(sys.argv) > 1 else 'haptics_test_3k_s8_g8.raw'
with open(rawfile, 'rb') as f:
    raw = f.read()

print(f"Raw: {len(raw)} bytes, {len(raw)/64} packets, {len(raw)/2/3000:.1f}s")
print(f"Timer resolution: 1ms (timeBeginPeriod)")

TARGET = 1.0 / 93.75
offset = 0
packets = 0
start = time.perf_counter()
next_t = start

while offset + 64 <= len(raw):
    next_t += TARGET
    
    # Sleep until ~1ms before target
    now = time.perf_counter()
    sleep_time = next_t - now - 0.001
    if sleep_time > 0:
        time.sleep(sleep_time)
    
    # Spin for final precision
    while time.perf_counter() < next_t:
        pass
    
    send_haptic(raw[offset:offset+64])
    offset += 64
    packets += 1

elapsed = time.perf_counter() - start
print(f"{packets} packets in {elapsed:.2f}s = {packets/elapsed:.1f} Hz")

send_haptic(bytes(64))
winmm.timeEndPeriod(1)
dev.close()
