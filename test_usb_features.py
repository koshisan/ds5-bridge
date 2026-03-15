"""Test which feature reports work over USB and at what sizes."""
import hid
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
print(f"Connection: {'BT' if is_bt else 'USB'}, report size: {len(test) if test else 0}")
print()

report_ids = [0x05, 0x08, 0x09, 0x20, 0x22, 0x80, 0x81, 0x82, 0x83, 0xF0, 0xF1, 0xF2]
sizes = [64, 128, 256, 512]

for rid in report_ids:
    for sz in sizes:
        try:
            r = dev.get_feature_report(rid, sz)
            if r:
                print(f"0x{rid:02X} size={sz:3d}: OK {len(r)}B [{bytes(r[:8]).hex(' ')}...]")
                break
        except Exception as e:
            if sz == sizes[-1]:
                print(f"0x{rid:02X}: FAILED ({e})")

dev.close()
