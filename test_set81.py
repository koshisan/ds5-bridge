"""Test all SET approaches systematically."""
import ctypes
import time
import hid as _hid

class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                 ("Offset", ctypes.c_ulong), ("OffsetHigh", ctypes.c_ulong),
                 ("hEvent", ctypes.c_void_p)]

class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", ctypes.c_ushort), ("UsagePage", ctypes.c_ushort),
        ("InputReportByteLength", ctypes.c_ushort), ("OutputReportByteLength", ctypes.c_ushort),
        ("FeatureReportByteLength", ctypes.c_ushort),
        ("Reserved", ctypes.c_ushort * 17),
        ("NumberLinkCollectionNodes", ctypes.c_ushort),
        ("NumberInputButtonCaps", ctypes.c_ushort), ("NumberInputValueCaps", ctypes.c_ushort),
        ("NumberInputDataIndices", ctypes.c_ushort),
        ("NumberOutputButtonCaps", ctypes.c_ushort), ("NumberOutputValueCaps", ctypes.c_ushort),
        ("NumberOutputDataIndices", ctypes.c_ushort),
        ("NumberFeatureButtonCaps", ctypes.c_ushort), ("NumberFeatureValueCaps", ctypes.c_ushort),
        ("NumberFeatureDataIndices", ctypes.c_ushort),
    ]

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

def poll_81(h, sub1, sub2, count=60):
    for i in range(count):
        time.sleep(0.02)
        rbuf = (ctypes.c_ubyte * 64)()
        rbuf[0] = 0x81
        HidD_GetFeature(h, rbuf, 64)
        data = bytes(rbuf)
        if data[1] == sub1 and data[2] == sub2:
            print(f"    MATCH at poll {i}! status={data[3]:#04x} data={data[:24].hex(' ')}")
            return True
    print(f"    no response after {count} polls")
    return False

sub1, sub2 = 0x09, 0x02

# Test 1: Regular handle + HidD_SetFeature + 547 bytes
print("=== Test 1: Regular handle, HidD_SetFeature, 547B ===")
h = ctypes.windll.kernel32.CreateFileW(ds5_path, 0xC0000000, 3, None, 3, 0, None)
pp = ctypes.c_void_p()
ctypes.windll.hid.HidD_GetPreparsedData(h, ctypes.byref(pp))
caps = HIDP_CAPS()
ctypes.windll.hid.HidP_GetCaps(pp, ctypes.byref(caps))
flen = caps.FeatureReportByteLength
print(f"  flen={flen}")
ctypes.windll.hid.HidD_FreePreparsedData(pp)

buf = (ctypes.c_ubyte * flen)()
buf[0] = 0x80; buf[1] = sub1; buf[2] = sub2
ctypes.windll.kernel32.SetLastError(0)
ok = HidD_SetFeature(h, buf, flen)
err = ctypes.GetLastError()
print(f"  SET: ok={ok} err={err}")
if ok: poll_81(h, sub1, sub2)
ctypes.windll.kernel32.CloseHandle(h)

# Test 2: OVERLAPPED handle + DeviceIoControl + 64 bytes
print("\n=== Test 2: OVERLAPPED handle, DeviceIoControl, 64B ===")
h = ctypes.windll.kernel32.CreateFileW(ds5_path, 0xC0000000, 3, None, 3, 0x40000000, None)
buf = (ctypes.c_ubyte * 64)()
buf[0] = 0x80; buf[1] = sub1; buf[2] = sub2
ov = OVERLAPPED()
ev = ctypes.windll.kernel32.CreateEventW(None, True, False, None)
ov.hEvent = ev
br = ctypes.c_ulong(0)
ok = ctypes.windll.kernel32.DeviceIoControl(h, IOCTL_SET, ctypes.byref(buf), 64, None, 0, ctypes.byref(br), ctypes.byref(ov))
err = ctypes.GetLastError()
if not ok and err == 997:
    ctypes.windll.kernel32.WaitForSingleObject(ev, 5000)
    ok = 1
    err = 0
print(f"  SET: ok={ok} err={err}")
ctypes.windll.kernel32.CloseHandle(ev)
if ok: poll_81(h, sub1, sub2)
ctypes.windll.kernel32.CloseHandle(h)

# Test 3: Regular handle + DeviceIoControl + 64 bytes
print("\n=== Test 3: Regular handle, DeviceIoControl, 64B ===")
h = ctypes.windll.kernel32.CreateFileW(ds5_path, 0xC0000000, 3, None, 3, 0, None)
buf = (ctypes.c_ubyte * 64)()
buf[0] = 0x80; buf[1] = sub1; buf[2] = sub2
br = ctypes.c_ulong(0)
ctypes.windll.kernel32.SetLastError(0)
ok = ctypes.windll.kernel32.DeviceIoControl(h, IOCTL_SET, ctypes.byref(buf), 64, None, 0, ctypes.byref(br), None)
err = ctypes.GetLastError()
print(f"  SET: ok={ok} err={err}")
if ok: poll_81(h, sub1, sub2)
ctypes.windll.kernel32.CloseHandle(h)

# Test 4: Regular handle + DeviceIoControl + 547 bytes
print("\n=== Test 4: Regular handle, DeviceIoControl, 547B ===")
h = ctypes.windll.kernel32.CreateFileW(ds5_path, 0xC0000000, 3, None, 3, 0, None)
buf = (ctypes.c_ubyte * flen)()
buf[0] = 0x80; buf[1] = sub1; buf[2] = sub2
br = ctypes.c_ulong(0)
ctypes.windll.kernel32.SetLastError(0)
ok = ctypes.windll.kernel32.DeviceIoControl(h, IOCTL_SET, ctypes.byref(buf), flen, None, 0, ctypes.byref(br), None)
err = ctypes.GetLastError()
print(f"  SET: ok={ok} err={err}")
if ok: poll_81(h, sub1, sub2)
ctypes.windll.kernel32.CloseHandle(h)

# Test 5: No access + DeviceIoControl (Chrome fallback)
print("\n=== Test 5: No-access handle (0), DeviceIoControl, 64B ===")
h = ctypes.windll.kernel32.CreateFileW(ds5_path, 0, 3, None, 3, 0x40000000, None)
if h != -1:
    buf = (ctypes.c_ubyte * 64)()
    buf[0] = 0x80; buf[1] = sub1; buf[2] = sub2
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
else:
    print(f"  open failed: {ctypes.GetLastError()}")
