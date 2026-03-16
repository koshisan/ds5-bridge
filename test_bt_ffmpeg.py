"""Play pre-converted 3kHz S8 raw file via BT Report 0x32."""
import sys, time, struct, zlib, hid

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

rawfile = sys.argv[1] if len(sys.argv) > 1 else 'haptics_test_3k_s8.raw'

with open(rawfile, 'rb') as f:
    raw = f.read()

print(f"Raw S8 data: {len(raw)} bytes = {len(raw)/2} stereo samples = {len(raw)/2/3000:.1f}s")
print(f"Packets: {len(raw)//64}")

# Measure write timing first
print("Measuring...")
times = []
for i in range(20):
    t0 = time.perf_counter()
    send_haptic(raw[i*64:(i+1)*64])
    t1 = time.perf_counter()
    times.append(t1 - t0)
avg = sum(times)/len(times)
print(f"Write: avg={avg*1000:.1f}ms min={min(times)*1000:.1f}ms max={max(times)*1000:.1f}ms")

# Play with adaptive timing
TARGET = 1.0 / 93.75
offset = 20 * 64
packets = 20
print("Playing...")

while offset + 64 <= len(raw):
    t0 = time.perf_counter()
    send_haptic(raw[offset:offset+64])
    write_time = time.perf_counter() - t0
    
    remaining = TARGET - write_time
    if remaining > 0.001:
        time.sleep(remaining - 0.0005)
        target_t = t0 + TARGET
        while time.perf_counter() < target_t:
            pass
    
    offset += 64
    packets += 1

elapsed_total = len(raw) / 64 * TARGET
print(f"{packets} packets. Done.")

send_haptic(bytes(64))
dev.close()
