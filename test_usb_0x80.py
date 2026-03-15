"""Test 0x80/0x81 subcommands over USB — figure out the correct protocol."""
import hid
import struct
import time
import sys

DS5_VID = 0x054C
DS5_PIDS = {0x0CE6, 0x0DF2}

dev_info = None
for info in hid.enumerate(DS5_VID):
    if info['product_id'] in DS5_PIDS:
        dev_info = info
        break

if not dev_info:
    print("No DS5 found")
    sys.exit(1)

dev = hid.device()
dev.open_path(dev_info['path'])
test = dev.read(128, 500)
is_bt = len(test) > 64 if test else False
print(f"Connection: {'BT' if is_bt else 'USB'}")
print()

subcmds = [
    (0x01, 0x11, 'PCBA ID'),
    (0x01, 0x13, 'Serial'),
    (0x01, 0x15, 'Board/Color'),
    (0x01, 0x18, 'Battery BC'),
    (0x01, 0x1a, 'VCM Left'),
    (0x01, 0x1c, 'VCM Right'),
    (0x01, 0x09, 'Unique ID'),
    (0x09, 0x02, 'BD MAC'),
]

# Method 1: USB direct (no CRC)
print("=== Method 1: USB direct send_feature_report ===")
for sub1, sub2, name in subcmds:
    try:
        payload = bytearray(64)
        payload[0] = 0x80
        payload[1] = sub1
        payload[2] = sub2
        dev.send_feature_report(bytes(payload))
        time.sleep(0.05)
        resp = dev.get_feature_report(0x81, 64)
        if resp:
            r = bytes(resp)
            has_data = any(b != 0 for b in r[1:])
            print(f"  {name}: {len(r)}B {'DATA' if has_data else 'EMPTY'} [{r[:12].hex(' ')}]")
        else:
            print(f"  {name}: no response")
    except Exception as e:
        print(f"  {name}: ERROR {e}")
    time.sleep(0.02)

print()

# Method 2: Try get_feature_report(0x80, 64) first (maybe USB reads directly?)
print("=== Method 2: Direct read 0x80 ===")
try:
    resp = dev.get_feature_report(0x80, 64)
    if resp:
        print(f"  0x80: {len(resp)}B [{bytes(resp[:12]).hex(' ')}]")
    else:
        print(f"  0x80: no response")
except Exception as e:
    print(f"  0x80: ERROR {e}")

print()

# Method 3: Different payload sizes
print("=== Method 3: Smaller payload sizes ===")
for size in [2, 3, 8, 16, 32]:
    try:
        payload = bytearray(size + 1)
        payload[0] = 0x80
        payload[1] = 0x01
        payload[2] = 0x13  # Serial
        dev.send_feature_report(bytes(payload))
        time.sleep(0.05)
        resp = dev.get_feature_report(0x81, 64)
        if resp:
            r = bytes(resp)
            has_data = any(b != 0 for b in r[1:])
            print(f"  size={size}: {len(r)}B {'DATA' if has_data else 'EMPTY'} [{r[:12].hex(' ')}]")
        else:
            print(f"  size={size}: no response")
    except Exception as e:
        print(f"  size={size}: ERROR {e}")

dev.close()
