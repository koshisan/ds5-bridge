#!/usr/bin/env python3
"""Dump all DS5 feature reports."""
import hid
import json
import sys

DS5_VID = 0x054C
DS5_PIDS = {0x0CE6, 0x0DF2}

# Known DS5 feature report IDs and sizes
FEATURE_REPORTS = {
    0x05: 41,   # Calibration
    0x06: 41,   # Calibration (alt)
    0x08: 49,   # Chip info?
    0x09: 49,   # Firmware info
    0x0A: 49,   # Calibration 2?
    0x20: 64,   # Firmware info 2
    0x21: 64,   # ?
    0x22: 64,   # ?
}

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
print(f"Path: {info['path']}")
print(f"Interface: {info.get('interface_number', '?')}")
print()

dev = hid.device()
dev.open_path(info["path"])

results = {}

# Try all report IDs 0x01-0x40
for rid in range(0x01, 0x41):
    for size in [41, 49, 64, 78, 128, 256]:
        try:
            data = dev.get_feature_report(rid, size)
            if data and len(data) > 1:
                # Verify first byte is report ID
                if data[0] == rid:
                    hex_str = ' '.join(f'{b:02X}' for b in data)
                    results[rid] = list(data)
                    print(f"Report 0x{rid:02X} ({len(data):3d} bytes): {hex_str[:80]}...")
                    break
        except Exception:
            break

dev.close()

# Save as JSON
with open("feature_reports.json", "w") as f:
    json.dump({f"0x{k:02X}": v for k, v in results.items()}, f, indent=2)

print(f"\nDumped {len(results)} feature reports to feature_reports.json")
