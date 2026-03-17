"""SAxense-style playback using Python hidapi - THE KNOWN WORKING PATH.
With Multimedia Timer via ctypes for precise 10ms intervals."""
import sys, time, struct, zlib, ctypes, threading
import hid

DS5_VID = 0x054C
DS5_PIDS = {0x0CE6, 0x0DF2}
REPORT_SIZE = 141
REPORT_ID = 0x32
SAMPLE_SIZE = 64

winmm = ctypes.WinDLL('winmm')

def crc32_ds5(data):
    return zlib.crc32(bytes([0xA2]) + data) & 0xFFFFFFFF

def build_report(audio_data, seq):
    pkt_0x11 = bytes([(0x11 & 0x3F) | (1 << 7), 7, 0xFE, 0, 0, 0, 0, seq & 0xFF, 0])
    pkt_0x12 = bytes([(0x12 & 0x3F) | (1 << 7), SAMPLE_SIZE])
    payload = (pkt_0x11 + pkt_0x12 + audio_data).ljust(REPORT_SIZE - 1 - 4, b'\x00')
    tag_seq = (seq & 0x0F) << 4
    body = bytes([tag_seq]) + payload
    crc = crc32_ds5(bytes([REPORT_ID]) + body)
    return bytes([REPORT_ID]) + body + struct.pack('<I', crc)

if len(sys.argv) < 2:
    print("Usage: python saxense_hidapi.py <file.raw> [gain]")
    sys.exit(1)

raw = open(sys.argv[1], 'rb').read()
print(f"Loaded {len(raw)} bytes = {len(raw)//64} packets")

dev_info = None
for info in hid.enumerate(DS5_VID):
    if info['product_id'] in DS5_PIDS:
        dev_info = info
        break
if not dev_info:
    print("No DS5!")
    sys.exit(1)

dev = hid.device()
dev.open_path(dev_info['path'])
test = dev.read(128, 500)
is_bt = len(test) > 64
print(f"DS5: {'BT' if is_bt else 'USB'}")

# Use multimedia timer for precise 10ms callbacks
TIMECALLBACK = ctypes.WINFUNCTYPE(None, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong))

seq = [0]
offset = [0]
count = [0]
done = threading.Event()

@TIMECALLBACK
def timer_cb(uTimerID, uMsg, dwUser, dw1, dw2):
    if offset[0] + SAMPLE_SIZE > len(raw):
        done.set()
        return
    audio = raw[offset[0]:offset[0]+SAMPLE_SIZE]
    offset[0] += SAMPLE_SIZE
    report = build_report(audio, seq[0])
    seq[0] = (seq[0] + 1) & 0x0F
    dev.write(report)
    count[0] += 1

winmm.timeBeginPeriod(1)
timer_id = winmm.timeSetEvent(10, 1, timer_cb, 0, 1)  # TIME_PERIODIC=1
if not timer_id:
    print("Timer failed!")
    sys.exit(1)

print("Playing...")
done.wait()
winmm.timeKillEvent(timer_id)
winmm.timeEndPeriod(1)
print(f"Done. {count[0]} packets sent.")
dev.close()
