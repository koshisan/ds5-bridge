"""Test DS5 SET 0x80 using direct HidD_SetFeature via ctypes."""
import ctypes
import ctypes.wintypes
import time

# Open DS5 via SetupAPI + CreateFile
SetupDiGetClassDevs = ctypes.windll.setupapi.SetupDiGetClassDevsW
SetupDiEnumDeviceInterfaces = ctypes.windll.setupapi.SetupDiEnumDeviceInterfaces
SetupDiGetDeviceInterfaceDetailW = ctypes.windll.setupapi.SetupDiGetDeviceInterfaceDetailW
SetupDiDestroyDeviceInfoList = ctypes.windll.setupapi.SetupDiDestroyDeviceInfoList

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
GUID_DEVINTERFACE_HID = ctypes.c_byte * 16

import struct

# HID GUID: {4D1E55B2-F16F-11CF-88CB-001111000030}
class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

HID_GUID = GUID(0x4D1E55B2, 0xF16F, 0x11CF, 
                (ctypes.c_ubyte * 8)(0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30))

class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("InterfaceClassGuid", GUID),
                ("Flags", ctypes.c_ulong), ("Reserved", ctypes.POINTER(ctypes.c_ulong))]

# Find DS5 HID device path
hdev_info = SetupDiGetClassDevs(ctypes.byref(HID_GUID), None, None, 
                                 DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)

ds5_path = None
for idx in range(100):
    iface_data = SP_DEVICE_INTERFACE_DATA()
    iface_data.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
    if not SetupDiEnumDeviceInterfaces(hdev_info, None, ctypes.byref(HID_GUID), idx, ctypes.byref(iface_data)):
        break
    
    # Get required size
    required = ctypes.c_ulong(0)
    SetupDiGetDeviceInterfaceDetailW(hdev_info, ctypes.byref(iface_data), None, 0, ctypes.byref(required), None)
    
    # Allocate and get detail
    class SP_DEVICE_INTERFACE_DETAIL_DATA(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("DevicePath", ctypes.c_wchar * (required.value // 2))]
    
    detail = SP_DEVICE_INTERFACE_DETAIL_DATA()
    detail.cbSize = 8  # sizeof on 64-bit
    if SetupDiGetDeviceInterfaceDetailW(hdev_info, ctypes.byref(iface_data), ctypes.byref(detail), required, None, None):
        path = detail.DevicePath
        if "054c" in path.lower() and "0ce6" in path.lower():
            print(f"Found DS5: {path}")
            ds5_path = path
            # Take the first one (might need to filter for the right collection)

SetupDiDestroyDeviceInfoList(hdev_info)

if not ds5_path:
    # BT path format differs - get from hidapi
    import hid as _hid
    for d in _hid.enumerate(0x054C, 0x0CE6):
        ds5_path = d['path'].decode('utf-8') if isinstance(d['path'], bytes) else d['path']
        print(f"Using hidapi path: {ds5_path}")
        break

if not ds5_path:
    print("DS5 not found!")
    exit(1)

# Open device
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

handle = ctypes.windll.kernel32.CreateFileW(
    ds5_path, GENERIC_READ | GENERIC_WRITE,
    FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)

if handle == INVALID_HANDLE_VALUE:
    err = ctypes.GetLastError()
    print(f"CreateFile failed: {err}")
    exit(1)

print(f"Opened handle: {handle}")

# HidD_SetFeature / HidD_GetFeature
HidD_SetFeature = ctypes.windll.hid.HidD_SetFeature
HidD_SetFeature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
HidD_SetFeature.restype = ctypes.c_bool

HidD_GetFeature = ctypes.windll.hid.HidD_GetFeature
HidD_GetFeature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
HidD_GetFeature.restype = ctypes.c_bool

# Check what HidD_GetFeature returns for different report IDs
for rid in [0x05, 0x09, 0x20, 0x22, 0x80, 0x81]:
    rbuf = (ctypes.c_ubyte * 64)()
    rbuf[0] = rid
    ctypes.windll.kernel32.SetLastError(0)
    ok = HidD_GetFeature(handle, rbuf, 64)
    err = ctypes.GetLastError()
    data = bytes(rbuf)
    print(f"GET 0x{rid:02X} size=64: ok={ok} err={err} data={data[:16].hex(' ')}")

# Check HidP_GetCaps to see actual feature report sizes
HidD_GetPreparsedData = ctypes.windll.hid.HidD_GetPreparsedData
HidD_GetPreparsedData.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
HidD_GetPreparsedData.restype = ctypes.c_bool

HidP_GetCaps = ctypes.windll.hid.HidP_GetCaps

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
ok = HidD_GetPreparsedData(handle, ctypes.byref(pp))
print(f"\nGetPreparsedData: {ok}")

if ok:
    caps = HIDP_CAPS()
    HidP_GetCaps(pp, ctypes.byref(caps))
    print(f"FeatureReportByteLength: {caps.FeatureReportByteLength}")
    print(f"InputReportByteLength: {caps.InputReportByteLength}")
    print(f"OutputReportByteLength: {caps.OutputReportByteLength}")

# Now try SET with FeatureReportByteLength
    flen = caps.FeatureReportByteLength
    ctypes.windll.hid.HidD_FreePreparsedData(pp)

    # Close and reopen with FILE_FLAG_OVERLAPPED (like Chromium does)
    ctypes.windll.kernel32.CloseHandle(handle)
    FILE_FLAG_OVERLAPPED = 0x40000000
    handle = ctypes.windll.kernel32.CreateFileW(
        ds5_path, GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING,
        FILE_FLAG_OVERLAPPED, None)
    print(f"\nReopened with OVERLAPPED: handle={handle}")

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                     ("Offset", ctypes.c_ulong), ("OffsetHigh", ctypes.c_ulong),
                     ("hEvent", ctypes.c_void_p)]

    # Use DeviceIoControl with IOCTL_HID_SET_FEATURE (like Chromium!)
    IOCTL_HID_SET_FEATURE = 0x000b0197
    # Try multiple sizes
    for sz in [64, flen]:
        buf = (ctypes.c_ubyte * sz)()
        buf[0] = 0x80
        buf[1] = 0x09
        buf[2] = 0x02

        overlapped = OVERLAPPED()
        event = ctypes.windll.kernel32.CreateEventW(None, True, False, None)
        overlapped.hEvent = event

        ctypes.windll.kernel32.SetLastError(0)
        bytesReturned = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.DeviceIoControl(
            handle, IOCTL_HID_SET_FEATURE,
            buf, sz,
            None, 0,
            ctypes.byref(bytesReturned),
            ctypes.byref(overlapped))
        err = ctypes.GetLastError()
        if not ok and err == 997:
            ctypes.windll.kernel32.WaitForSingleObject(event, 5000)
            ok2 = ctypes.windll.kernel32.GetOverlappedResult(
                handle, ctypes.byref(overlapped), ctypes.byref(bytesReturned), False)
            err2 = ctypes.GetLastError()
            print(f"DeviceIoControl SET size={sz}: PENDING -> ok={ok2} err={err2}")
        else:
            print(f"DeviceIoControl SET size={sz}: ok={ok} err={err}")
        ctypes.windll.kernel32.CloseHandle(event)

        if ok or (not ok and err == 997):
            # Poll 0x81
            time.sleep(0.05)
            for i in range(30):
                time.sleep(0.02)
                rbuf = (ctypes.c_ubyte * flen)()
                rbuf[0] = 0x81
                HidD_GetFeature(handle, rbuf, flen)
                data = bytes(rbuf)
                if any(b != 0 for b in data[1:6]):
                    print(f"  Poll {i}: {data[:32].hex(' ')}")
                    break
            else:
                print(f"  -> 0x81 still zeros after 30 polls")

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                     ("Offset", ctypes.c_ulong), ("OffsetHigh", ctypes.c_ulong),
                     ("hEvent", ctypes.c_void_p)]

    overlapped = OVERLAPPED()
    event = ctypes.windll.kernel32.CreateEventW(None, True, False, None)
    overlapped.hEvent = event

    ctypes.windll.kernel32.SetLastError(0)
    bytesReturned = ctypes.c_ulong(0)
    ok = ctypes.windll.kernel32.DeviceIoControl(
        handle, IOCTL_HID_SET_FEATURE,
        buf, flen,
        None, 0,
        ctypes.byref(bytesReturned),
        ctypes.byref(overlapped))
    err = ctypes.GetLastError()
    print(f"DeviceIoControl SET_FEATURE size={flen}: ok={ok} err={err}")

    if not ok and err == 997:  # ERROR_IO_PENDING
        print("IO_PENDING - waiting...")
        ctypes.windll.kernel32.WaitForSingleObject(event, 5000)
        ok2 = ctypes.windll.kernel32.GetOverlappedResult(
            handle, ctypes.byref(overlapped), ctypes.byref(bytesReturned), False)
        err2 = ctypes.GetLastError()
        print(f"GetOverlappedResult: ok={ok2} err={err2} bytes={bytesReturned.value}")


    ctypes.windll.kernel32.CloseHandle(handle)
