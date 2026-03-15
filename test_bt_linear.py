"""Send linear ramp 1->255 spread across packets, blast as fast as possible."""
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

repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 6

# Build all samples: 1,2,3,...,254,255 then 255,254,...,2,1 = one full cycle = 508 samples
# Repeat that 'repeats' times
ramp_up = list(range(1, 256))       # 1..255 = 255 values
ramp_down = list(range(255, 0, -1)) # 255..1 = 255 values
one_cycle = ramp_up + ramp_down     # 510 samples per cycle

all_samples = one_cycle * repeats
total = len(all_samples)
print(f"Linear ramp 1->255->1, {repeats} cycles, {total} samples total")

# Pack into 64-byte packets (32 stereo pairs = 32 mono samples per packet)
packets = []
for i in range(0, total, 32):
    chunk = all_samples[i:i+32]
    if len(chunk) < 32:
        chunk = chunk + [128] * (32 - len(chunk))
    audio = bytearray(64)
    for j in range(32):
        audio[j*2] = chunk[j]
        audio[j*2+1] = chunk[j]
    packets.append(bytes(audio))

print(f"Packed into {len(packets)} packets")
print(f"First packet samples: {[packets[0][i*2] for i in range(32)]}")
print(f"Blasting...")

start = time.perf_counter()
for pkt in packets:
    send_haptic(pkt)
elapsed = time.perf_counter() - start

print(f"\n{len(packets)} packets in {elapsed:.2f}s = {len(packets)/elapsed:.0f} Hz")
print(f"Total duration: {elapsed:.2f}s")

send_haptic(bytes([128]*64))
dev.close()
