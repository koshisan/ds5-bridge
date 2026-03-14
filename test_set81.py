"""SET 0x80 / GET 0x81 using DeviceIoControl for BOTH (exactly like Chromium)."""
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

ds5_path = None
for d in _hid.enumerate(0x054C, 0x0CE6):
    ds5_path = d['path'].decode('utf-8') if isinstance(d['path'], bytes) else d['path']
    break
if not ds5_path:
    print("DS5 not found!"); exit(1)

handle = ctypes.windll.kernel32.CreateFileW(
    ds5_path, 0xC0000000, 3, None, 3, 0x40000000, None)

pp = ctypes.c_void_p()
ctypes.windll.hid.HidD_GetPreparsedData(handle, ctypes.byref(pp))
caps = HIDP_CAPS()
ctypes.windll.hid.HidP_GetCaps(pp, ctypes.byref(caps))
flen = caps.FeatureReportByteLength
print(f"FeatureReportByteLength: {flen}")
ctypes.windll.hid.HidD_FreePreparsedData(pp)

IOCTL_SET = 0x000b0197
IOCTL_GET = 0x000b0193

def do_ioctl(ioctl_code, in_buf, in_size, out_buf, out_size, timeout_ms=5000):
    ov = OVERLAPPED()
    ev = ctypes.windll.kernel32.CreateEventW(None, True, False, None)
    ov.hEvent = ev
    br = ctypes.c_ulong(0)
    ctypes.windll.kernel32.SetLastError(0)
    ok = ctypes.windll.kernel32.DeviceIoControl(
        handle, ioctl_code, in_buf, in_size, out_buf, out_size,
        ctypes.byref(br), ctypes.byref(ov))
    err = ctypes.GetLastError()
    if not ok and err == 997:  # ERROR_IO_PENDING
        wait = ctypes.windll.kernel32.WaitForSingleObject(ev, timeout_ms)
        if wait == 0:  # WAIT_OBJECT_0
            ok = ctypes.windll.kernel32.GetOverlappedResult(
                handle, ctypes.byref(ov), ctypes.byref(br), False)
            err = ctypes.GetLastError() if not ok else 0
        else:
            ctypes.windll.kernel32.CancelIoEx(handle, ctypes.byref(ov))
            err = -1  # timeout
            ok = False
    ctypes.windll.kernel32.CloseHandle(ev)
    return ok, err, br.value

def ioctl_set_feature(data_bytes):
    buf = (ctypes.c_ubyte * flen)()
    for i, b in enumerate(data_bytes[:flen]):
        buf[i] = b
    return do_ioctl(IOCTL_SET, buf, flen, None, 0)

def ioctl_get_feature(report_id):
    buf = (ctypes.c_ubyte * flen)()
    buf[0] = report_id
    ok, err, br = do_ioctl(IOCTL_GET, None, 0, buf, flen, 2000)
    return bytes(buf), ok, err, br

# Test: GET 0x05 via DeviceIoControl (not HidD_GetFeature!)
data, ok, err, br = ioctl_get_feature(0x05)
print(f"GET 0x05: ok={ok} err={err} bytes={br} data={data[:16].hex(' ')}")

data, ok, err, br = ioctl_get_feature(0x20)
print(f"GET 0x20: ok={ok} err={err} bytes={br} data={data[:16].hex(' ')}")

# Now SET 0x80 + poll GET 0x81
subcmds = [
    (0x09, 0x02, "PCBA_ID"),
    (0x01, 0x11, "FW_VER_1"),
]

for sub1, sub2, name in subcmds:
    ok, err, br = ioctl_set_feature([0x80, sub1, sub2])
    print(f"\nSET 0x80 [{sub1:#04x},{sub2:#04x}] ({name}): ok={ok} err={err}")
    
    for i in range(80):
        time.sleep(0.015)
        data, gok, gerr, gbr = ioctl_get_feature(0x81)
        if data[1] == sub1 and data[2] == sub2:
            print(f"  Poll {i}: MATCH! status={data[3]:#04x} bytes={gbr}")
            print(f"  Data: {data[:32].hex(' ')}")
            break
        elif any(b != 0 for b in data[1:10]):
            if i < 5:
                print(f"  Poll {i}: non-zero: {data[:20].hex(' ')}")
    else:
        # Show what 0x81 actually returns
        print(f"  -> no match after 80 polls. Last: {data[:20].hex(' ')}")

ctypes.windll.kernel32.CloseHandle(handle)
