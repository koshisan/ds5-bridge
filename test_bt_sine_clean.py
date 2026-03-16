"""Clean sine test: pre-generate all samples at 3kHz, blast as packets."""
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

freq = float(sys.argv[1]) if len(sys.argv) > 1 else 150.0
duration = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
amplitude = int(sys.argv[3]) if len(sys.argv) > 3 else 127

# Pre-generate entire signal at 3kHz
total_samples = int(3000 * duration)
all_s8 = bytearray(total_samples * 2)  # stereo
for i in range(total_samples):
    val = int(amplitude * math.sin(2 * math.pi * freq * i / 3000.0))
    val = max(-128, min(127, val))
    all_s8[i*2] = val & 0xFF
    all_s8[i*2+1] = val & 0xFF

print(f"Sine {freq}Hz, amplitude {amplitude}, {duration}s")
print(f"{total_samples} samples at 3kHz, {total_samples//32} packets")
print(f"Samples/cycle: {3000/freq:.1f}")

# Blast packets
offset = 0
packets = 0
start = time.perf_counter()
while offset + 64 <= len(all_s8):
    send_haptic(bytes(all_s8[offset:offset+64]))
    offset += 64
    packets += 1
elapsed = time.perf_counter() - start

print(f"{packets} packets in {elapsed:.2f}s = {packets/elapsed:.0f} Hz")

send_haptic(bytes([128]*64))
dev.close()
