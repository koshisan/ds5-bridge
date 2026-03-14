"""Test all known 0x80 subcommands with CRC32."""
import ctypes
import struct
import time
import hid as _hid

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

h = ctypes.windll.kernel32.CreateFileW(ds5_path, 0xC0000000, 3, None, 3, 0, None)

def set_and_poll(sub1, sub2, name):
    payload = bytearray(63)
    payload[0] = sub1
    payload[1] = sub2
    crc = ds5_crc32([0x53, 0x80], payload[:59])
    struct.pack_into('<I', payload, 59, crc)
    
    hid_buf = bytearray(64)
    hid_buf[0] = 0x80
    hid_buf[1:] = payload
    
    buf = (ctypes.c_ubyte * 64)(*hid_buf)
    ctypes.windll.kernel32.SetLastError(0)
    ok = HidD_SetFeature(h, buf, 64)
    err = ctypes.GetLastError()
    
    if not ok:
        print(f"  [{sub1:#04x},{sub2:#04x}] {name}: SET FAILED err={err}")
        return
    
    for i in range(100):
        time.sleep(0.015)
        rbuf = (ctypes.c_ubyte * 64)()
        rbuf[0] = 0x81
        HidD_GetFeature(h, rbuf, 64)
        data = bytes(rbuf)
        if data[1] == sub1 and data[2] == sub2:
            status = data[3]
            # Decode ASCII where possible
            ascii_part = ""
            for b in data[4:32]:
                if 0x20 <= b < 0x7f:
                    ascii_part += chr(b)
                elif b == 0:
                    ascii_part += "."
                else:
                    ascii_part += f"\\x{b:02x}"
            print(f"  [{sub1:#04x},{sub2:#04x}] {name}: status={status:#04x} data={data[4:20].hex(' ')} ascii=\"{ascii_part}\"")
            return
    print(f"  [{sub1:#04x},{sub2:#04x}] {name}: no response")

# All subcommands seen in browser tester source + logs
subcmds = [
    (0x01, 0x11, "FW_VERSION_1"),
    (0x01, 0x13, "FW_VERSION_2"),
    (0x01, 0x15, "FW_VERSION_3"),
    (0x01, 0x18, "FW_VERSION_4"),
    (0x01, 0x1a, "FW_VERSION_5"),
    (0x01, 0x1c, "FW_VERSION_6"),
    (0x01, 0x09, "FW_VERSION_7"),
    (0x09, 0x02, "PCBA_ID"),
    (0x07, 0x25, "SERIAL/TRACABILITY_1"),
    (0x07, 0x26, "SERIAL/TRACABILITY_2"),  # guess
]

print("Testing all 0x80 subcommands with CRC32...\n")
for sub1, sub2, name in subcmds:
    set_and_poll(sub1, sub2, name)

ctypes.windll.kernel32.CloseHandle(h)
