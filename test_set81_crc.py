"""Test SET 0x80 with CRC32 (like ds.daidr.me browser tester)."""
import ctypes
import struct
import time
import hid as _hid

# CRC32 table
crc_table = []
for n in range(256):
    c = n
    for _ in range(8):
        c = (0xEDB88320 ^ (c >> 1)) if (c & 1) else (c >> 1)
    crc_table.append(c & 0xFFFFFFFF)

def ds5_crc32(seed_bytes, data_bytes):
    crc = 0xFFFFFFFF
    for b in seed_bytes:
        crc = (crc >> 8) ^ crc_table[(crc ^ b) & 0xFF]
    for b in data_bytes:
        crc = (crc >> 8) ^ crc_table[(crc ^ b) & 0xFF]
    return crc ^ 0xFFFFFFFF

class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                 ("Offset", ctypes.c_ulong), ("OffsetHigh", ctypes.c_ulong),
                 ("hEvent", ctypes.c_void_p)]

HidD_SetFeature = ctypes.windll.hid.HidD_SetFeature
HidD_SetFeature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
HidD_SetFeature.restype = ctypes.c_bool

HidD_GetFeature = ctypes.windll.hid.HidD_GetFeature
HidD_GetFeature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
HidD_GetFeature.restype = ctypes.c_bool

ds5_path = None
for d in _hid.enumerate(0x054C, 0x0CE6):
    ds5_path = d['path'].decode('utf-8') if isinstance(d['path'], bytes) else d['path']
    break
if not ds5_path:
    print("DS5 not found!"); exit(1)

IOCTL_SET = 0x000b0197
sub1, sub2 = 0x09, 0x02

def poll_81(h, sub1, sub2, count=80):
    for i in range(count):
        time.sleep(0.015)
        rbuf = (ctypes.c_ubyte * 64)()
        rbuf[0] = 0x81
        HidD_GetFeature(h, rbuf, 64)
        data = bytes(rbuf)
        if data[1] == sub1 and data[2] == sub2:
            print(f"    MATCH at poll {i}! status={data[3]:#04x}")
            print(f"    Data: {data[:32].hex(' ')}")
            return True
    print(f"    no response after {count} polls")
    return False

# Build the payload like daidr does:
# reportCount = 63 (for report 0x80)
# Buffer = 63 bytes, [0]=subcmd, [1]=subsub, [2..58]=zeros, [59..62]=CRC32
# CRC seed = [0x53, 0x80] (0x53 = BT feature SET marker)
# CRC over seed + data[0..58] (everything except last 4 bytes)

payload = bytearray(63)
payload[0] = sub1  # 0x09
payload[1] = sub2  # 0x02
# CRC32 with seed [0x53, 0x80]
crc = ds5_crc32([0x53, 0x80], payload[:59])
struct.pack_into('<I', payload, 59, crc)
print(f"Payload (63B): {payload[:20].hex(' ')} ... CRC={crc:#010x}")

# Full HID buffer = reportId + payload = 64 bytes
hid_buf = bytearray(64)
hid_buf[0] = 0x80
hid_buf[1:] = payload
print(f"HID buf (64B): {hid_buf[:20].hex(' ')} ... {hid_buf[59:64].hex(' ')}")

# Test A: HidD_SetFeature with 64B + CRC
print("\n=== Test A: HidD_SetFeature 64B with CRC ===")
h = ctypes.windll.kernel32.CreateFileW(ds5_path, 0xC0000000, 3, None, 3, 0, None)
buf = (ctypes.c_ubyte * 64)(*hid_buf)
ctypes.windll.kernel32.SetLastError(0)
ok = HidD_SetFeature(h, buf, 64)
err = ctypes.GetLastError()
print(f"  SET: ok={ok} err={err}")
if ok: poll_81(h, sub1, sub2)
ctypes.windll.kernel32.CloseHandle(h)

# Test B: HidD_SetFeature with 547B + CRC (padded)
print("\n=== Test B: HidD_SetFeature 547B with CRC ===")
h = ctypes.windll.kernel32.CreateFileW(ds5_path, 0xC0000000, 3, None, 3, 0, None)
buf547 = (ctypes.c_ubyte * 547)()
for i in range(64):
    buf547[i] = hid_buf[i]
ctypes.windll.kernel32.SetLastError(0)
ok = HidD_SetFeature(h, buf547, 547)
err = ctypes.GetLastError()
print(f"  SET: ok={ok} err={err}")
if ok: poll_81(h, sub1, sub2)
ctypes.windll.kernel32.CloseHandle(h)

# Test C: DeviceIoControl 64B with CRC + OVERLAPPED
print("\n=== Test C: DeviceIoControl 64B with CRC + OVERLAPPED ===")
h = ctypes.windll.kernel32.CreateFileW(ds5_path, 0xC0000000, 3, None, 3, 0x40000000, None)
buf = (ctypes.c_ubyte * 64)(*hid_buf)
ov = OVERLAPPED()
ev = ctypes.windll.kernel32.CreateEventW(None, True, False, None)
ov.hEvent = ev
br = ctypes.c_ulong(0)
ok = ctypes.windll.kernel32.DeviceIoControl(h, IOCTL_SET, ctypes.byref(buf), 64, None, 0, ctypes.byref(br), ctypes.byref(ov))
err = ctypes.GetLastError()
if not ok and err == 997:
    ctypes.windll.kernel32.WaitForSingleObject(ev, 5000)
    ok = 1; err = 0
print(f"  SET: ok={ok} err={err}")
ctypes.windll.kernel32.CloseHandle(ev)
if ok: poll_81(h, sub1, sub2)
ctypes.windll.kernel32.CloseHandle(h)

# Test D: DeviceIoControl 547B with CRC
print("\n=== Test D: DeviceIoControl 547B with CRC ===")
h = ctypes.windll.kernel32.CreateFileW(ds5_path, 0xC0000000, 3, None, 3, 0x40000000, None)
buf547 = (ctypes.c_ubyte * 547)()
for i in range(64):
    buf547[i] = hid_buf[i]
ov = OVERLAPPED()
ev = ctypes.windll.kernel32.CreateEventW(None, True, False, None)
ov.hEvent = ev
br = ctypes.c_ulong(0)
ok = ctypes.windll.kernel32.DeviceIoControl(h, IOCTL_SET, ctypes.byref(buf547), 547, None, 0, ctypes.byref(br), ctypes.byref(ov))
err = ctypes.GetLastError()
if not ok and err == 997:
    ctypes.windll.kernel32.WaitForSingleObject(ev, 5000)
    ok = 1; err = 0
print(f"  SET: ok={ok} err={err}")
ctypes.windll.kernel32.CloseHandle(ev)
if ok: poll_81(h, sub1, sub2)
ctypes.windll.kernel32.CloseHandle(h)
