"""Minimal SET 0x80 / GET 0x81 test via DeviceIoControl (Chromium-style)."""
import ctypes
import time
import hid as _hid

class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                 ("Offset", ctypes.c_ulong), ("OffsetHigh", ctypes.c_ulong),
                 ("hEvent", ctypes.c_void_p)]

ds5_path = None
for d in _hid.enumerate(0x054C, 0x0CE6):
    ds5_path = d['path'].decode('utf-8') if isinstance(d['path'], bytes) else d['path']
    break
if not ds5_path:
    print("DS5 not found!"); exit(1)
print(f"Path: {ds5_path}")

handle = ctypes.windll.kernel32.CreateFileW(
    ds5_path, 0xC0000000, 3, None, 3, 0x40000000, None)
if handle == -1:
    print(f"Open failed: {ctypes.GetLastError()}"); exit(1)

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

pp = ctypes.c_void_p()
ctypes.windll.hid.HidD_GetPreparsedData(handle, ctypes.byref(pp))
caps = HIDP_CAPS()
ctypes.windll.hid.HidP_GetCaps(pp, ctypes.byref(caps))
flen = caps.FeatureReportByteLength
print(f"FeatureReportByteLength: {flen}")
ctypes.windll.hid.HidD_FreePreparsedData(pp)

IOCTL_SET = 0x000b0197
IOCTL_GET = 0x000b0193

HidD_GetFeature = ctypes.windll.hid.HidD_GetFeature
HidD_GetFeature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
HidD_GetFeature.restype = ctypes.c_bool

def ioctl_set(data_bytes, size):
    buf = (ctypes.c_ubyte * size)()
    for i, b in enumerate(data_bytes[:size]):
        buf[i] = b
    ov = OVERLAPPED()
    ev = ctypes.windll.kernel32.CreateEventW(None, True, False, None)
    ov.hEvent = ev
    br = ctypes.c_ulong(0)
    ctypes.windll.kernel32.SetLastError(0)
    ok = ctypes.windll.kernel32.DeviceIoControl(
        handle, IOCTL_SET, buf, size, None, 0, ctypes.byref(br), ctypes.byref(ov))
    err = ctypes.GetLastError()
    if not ok and err == 997:
        ctypes.windll.kernel32.WaitForSingleObject(ev, 5000)
        ok = ctypes.windll.kernel32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(br), False)
        err = ctypes.GetLastError()
    ctypes.windll.kernel32.CloseHandle(ev)
    return ok, err

def hid_get(rid, size=64):
    buf = (ctypes.c_ubyte * size)()
    buf[0] = rid
    ok = HidD_GetFeature(handle, buf, size)
    return bytes(buf), ok

# Step 1: GET 0x05 first (triggers extended mode on BT)
data, ok = hid_get(0x05)
print(f"\nGET 0x05: ok={ok} data={data[:16].hex(' ')}")

# Step 2: Try subcmds with both 64 and flen
for sz_label, sz in [("64B", 64), (f"{flen}B", flen)]:
    print(f"\n=== Testing with SET buffer size {sz_label} ===")
    sub1, sub2 = 0x09, 0x02
    payload = [0x80, sub1, sub2]
    ok, err = ioctl_set(payload, sz)
    print(f"SET 0x80 [0x09,0x02]: ok={ok} err={err}")
    
    for i in range(50):
        time.sleep(0.02)
        data, gok = hid_get(0x81)
        if data[1] == sub1 and data[2] == sub2:
            print(f"  Poll {i}: MATCH! {data[:32].hex(' ')}")
            break
        elif any(b != 0 for b in data[1:6]):
            if i < 5:
                print(f"  Poll {i}: {data[:20].hex(' ')}")
    else:
        print(f"  -> zeros after 50 polls")

ctypes.windll.kernel32.CloseHandle(handle)
