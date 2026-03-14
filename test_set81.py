"""Hybrid: DeviceIoControl SET + HidD_GetFeature GET on OVERLAPPED handle."""
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

# Open with FILE_FLAG_OVERLAPPED
handle = ctypes.windll.kernel32.CreateFileW(
    ds5_path, 0xC0000000, 3, None, 3, 0x40000000, None)

pp = ctypes.c_void_p()
ctypes.windll.hid.HidD_GetPreparsedData(handle, ctypes.byref(pp))
caps = HIDP_CAPS()
ctypes.windll.hid.HidP_GetCaps(pp, ctypes.byref(caps))
flen = caps.FeatureReportByteLength
print(f"FeatureReportByteLength: {flen}")
ctypes.windll.hid.HidD_FreePreparsedData(pp)

HidD_GetFeature = ctypes.windll.hid.HidD_GetFeature
HidD_GetFeature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
HidD_GetFeature.restype = ctypes.c_bool

IOCTL_SET = 0x000b0197

# Verify HidD_GetFeature works on this overlapped handle
for rid in [0x05, 0x09, 0x20]:
    buf = (ctypes.c_ubyte * 64)()
    buf[0] = rid
    ok = HidD_GetFeature(handle, buf, 64)
    data = bytes(buf)
    print(f"HidD_GetFeature 0x{rid:02X}: ok={ok} data={data[:16].hex(' ')}")

# Now SET 0x80 via DeviceIoControl + poll with HidD_GetFeature
subcmds = [
    (0x09, 0x02, "PCBA_ID"),
    (0x01, 0x11, "FW_VER_1"),
]

for sub1, sub2, name in subcmds:
    buf = (ctypes.c_ubyte * flen)()
    buf[0] = 0x80
    buf[1] = sub1
    buf[2] = sub2
    
    ov = OVERLAPPED()
    ev = ctypes.windll.kernel32.CreateEventW(None, True, False, None)
    ov.hEvent = ev
    br = ctypes.c_ulong(0)
    ctypes.windll.kernel32.SetLastError(0)
    ok = ctypes.windll.kernel32.DeviceIoControl(
        handle, IOCTL_SET,
        ctypes.byref(buf), flen,
        None, 0,
        ctypes.byref(br), ctypes.byref(ov))
    err = ctypes.GetLastError()
    if not ok and err == 997:
        ctypes.windll.kernel32.WaitForSingleObject(ev, 5000)
        ok = ctypes.windll.kernel32.GetOverlappedResult(
            handle, ctypes.byref(ov), ctypes.byref(br), False)
        err = ctypes.GetLastError() if not ok else 0
    ctypes.windll.kernel32.CloseHandle(ev)
    print(f"\nSET 0x80 [{sub1:#04x},{sub2:#04x}] ({name}): ok={ok} err={err}")
    
    for i in range(80):
        time.sleep(0.015)
        rbuf = (ctypes.c_ubyte * 64)()
        rbuf[0] = 0x81
        HidD_GetFeature(handle, rbuf, 64)
        data = bytes(rbuf)
        if data[1] == sub1 and data[2] == sub2:
            print(f"  Poll {i}: MATCH! status={data[3]:#04x}")
            print(f"  Data: {data[:32].hex(' ')}")
            break
        elif any(b != 0 for b in data[1:10]) and i < 3:
            print(f"  Poll {i}: {data[:20].hex(' ')}")
    else:
        print(f"  -> zeros/no match after 80 polls")

ctypes.windll.kernel32.CloseHandle(handle)
