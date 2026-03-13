#!/usr/bin/env python3
"""Dump all DS5 feature reports."""
import hid
import json
import sys

DS5_VID = 0x054C
DS5_PIDS = {0x0CE6, 0x0DF2}

def find_ds5():
    for info in hid.enumerate(DS5_VID):
        if info["product_id"] in DS5_PIDS:
            return info
    return None

info = find_ds5()
if not info:
    print("No DS5 found!")
    sys.exit(1)

print(f"Found: {info['product_string']} (PID: 0x{info['product_id']:04X})")
print()

dev = hid.device()
dev.open_path(info["path"])

results = {}

for rid in range(0x01, 0x80):
    # Try largest size first — hidapi returns actual size
    for size in [256, 128, 78, 64, 49, 41, 32, 20, 16]:
        try:
            data = dev.get_feature_report(rid, size)
            if data and len(data) > 1 and data[0] == rid:
                hex_str = ' '.join(f'{b:02X}' for b in data)
                results[f"0x{rid:02X}"] = list(data)
                print(f"0x{rid:02X} ({len(data):3d} bytes): {hex_str[:90]}")
                break
        except:
            continue

dev.close()

with open("feature_reports.json", "w") as f:
    json.dump(results, f, indent=2)

print(f"\nDumped {len(results)} feature reports to feature_reports.json")
