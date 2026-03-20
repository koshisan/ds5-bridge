#!/usr/bin/env python3
"""
Binary search: which bytes in Report 0x34 control vibration?
Zeroes out different regions and asks if vibration stops.

Usage:
    python test_report34_binsearch.py
"""

import hid
import time
import struct
import binascii
import sys

DS5_VID = 0x054C
DS5_PID = 0x0CE6
REPORT_SIZE = 547


def calc_crc(data):
    return binascii.crc32(bytes([0xA2]) + data) & 0xFFFFFFFF


def find_ds5():
    for dev in hid.enumerate(DS5_VID, DS5_PID):
        if dev.get("usage_page") == 1 or dev.get("interface_number", -1) == -1:
            return dev
    return None


def send_modified(dev, captured, zero_start, zero_end, recalc_crc=True):
    """Send captured reports with bytes zero_start..zero_end zeroed out."""
    num_reports = len(captured) // REPORT_SIZE
    for i in range(num_reports):
        buf = bytearray(captured[i * REPORT_SIZE:(i + 1) * REPORT_SIZE])
        buf[zero_start:zero_end + 1] = b'\x00' * (zero_end - zero_start + 1)
        if recalc_crc:
            crc = calc_crc(bytes(buf[:266]))
            struct.pack_into('<I', buf, 266, crc)
        dev.write(bytes(buf))
        time.sleep(0.030)


def test_region(dev, captured, start, end, description):
    """Zero a region and ask if it still vibrates."""
    print(f"\n--- Zero bytes {start}-{end} ({description}) ---")
    input("Press Enter...")
    send_modified(dev, captured, start, end)
    result = input("Did it vibrate? (y/n): ").strip().lower()
    return result == 'y'


def main():
    dev_info = find_ds5()
    if not dev_info:
        print("DS5 not found!")
        sys.exit(1)

    d = hid.device()
    d.open_path(dev_info["path"])

    with open("dsx_report34_capture.bin", "rb") as f:
        captured = f.read()

    print(f"Loaded {len(captured) // REPORT_SIZE} reports")
    print("First, verifying replay works...")
    input("Press Enter for baseline replay...")
    for i in range(len(captured) // REPORT_SIZE):
        d.write(captured[i * REPORT_SIZE:(i + 1) * REPORT_SIZE])
        time.sleep(0.030)
    baseline = input("Vibrated? (y/n): ").strip().lower() == 'y'
    if not baseline:
        print("Baseline failed! Aborting.")
        d.close()
        return

    time.sleep(0.5)

    # Phase 1: Find which broad region matters
    print("\n========================================")
    print("PHASE 1: Broad region search")
    print("========================================")

    regions = [
        (1, 4, "bytes 1-4"),
        (5, 9, "bytes 5-9 (the '00000')"),
        (10, 12, "bytes 10-12 (timestamp?)"),
        (13, 76, "bytes 13-76 (first half of suspected audio)"),
        (77, 138, "bytes 77-138 (second half of suspected audio)"),
        (139, 187, "bytes 139-187 (control data)"),
        (188, 265, "bytes 188-265 (zero region)"),
        (266, 269, "bytes 266-269 (CRC)"),
        (270, 546, "bytes 270-546 (tail)"),
    ]

    results = {}
    for start, end, desc in regions:
        time.sleep(0.5)
        vibrated = test_region(d, captured, start, end, desc)
        results[(start, end)] = vibrated
        status = "VIBRATES" if vibrated else "SILENT"
        print(f"  → {status}")

    print("\n========================================")
    print("RESULTS SUMMARY")
    print("========================================")
    for (start, end), vibrated in results.items():
        status = "✓ vibrates" if vibrated else "✗ SILENT"
        print(f"  Bytes {start:3d}-{end:3d}: {status}")

    # Phase 2: Narrow down silent regions
    silent_regions = [(s, e) for (s, e), v in results.items() if not v]
    if silent_regions:
        print(f"\n========================================")
        print(f"PHASE 2: Narrowing down {len(silent_regions)} silent region(s)")
        print(f"========================================")

        for sr_start, sr_end in silent_regions:
            mid = (sr_start + sr_end) // 2
            if mid > sr_start:
                time.sleep(0.5)
                first_half = test_region(d, captured, sr_start, mid,
                                        f"first half {sr_start}-{mid}")
                fh_status = "VIBRATES" if first_half else "SILENT"
                print(f"  → {fh_status}")

                time.sleep(0.5)
                second_half = test_region(d, captured, mid + 1, sr_end,
                                          f"second half {mid + 1}-{sr_end}")
                sh_status = "VIBRATES" if second_half else "SILENT"
                print(f"  → {sh_status}")

    d.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
