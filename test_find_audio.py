#!/usr/bin/env python3
"""
Find the actual audio byte positions in Report 0x34.

Step 1: Keep only known-critical header (bytes 0-12), zero EVERYTHING else
        before CRC. If silent → audio is in 13-265.
Step 2: Inject loud sine into sub-regions to find exactly where.

Usage:
    python test_find_audio.py
"""

import hid
import time
import struct
import math
import binascii
import sys

DS5_VID = 0x054C
DS5_PID = 0x0CE6
REPORT_SIZE = 547
CRC_OFFSET = 266
NUM_LOOPS = 3  # replay template N times for each test


def calc_crc(data):
    return binascii.crc32(bytes([0xA2]) + data) & 0xFFFFFFFF


def find_ds5():
    for dev in hid.enumerate(DS5_VID, DS5_PID):
        if dev.get("usage_page") == 1 or dev.get("interface_number", -1) == -1:
            return dev
    return None


def make_silent_template(captured_report):
    """Keep bytes 0-12 from capture, zero 13-265, recalc CRC."""
    buf = bytearray(REPORT_SIZE)
    buf[0:13] = captured_report[0:13]  # report ID + header
    # everything else stays zero
    crc = calc_crc(bytes(buf[:CRC_OFFSET]))
    struct.pack_into('<I', buf, CRC_OFFSET, crc)
    return buf


def make_sine_at(silent_template, start, end, sample_idx, freq=200, amp=80):
    """Inject sine into silent template at given byte range."""
    buf = bytearray(silent_template)
    for i in range(start, end, 2):
        t = sample_idx / 2100.0  # approximate sample rate
        val = int(amp * math.sin(2 * math.pi * freq * t))
        val = max(-128, min(127, val))
        buf[i] = val & 0xFF
        if i + 1 < end:
            buf[i + 1] = val & 0xFF
        sample_idx += 1
    crc = calc_crc(bytes(buf[:CRC_OFFSET]))
    struct.pack_into('<I', buf, CRC_OFFSET, crc)
    return buf, sample_idx


def send_test(d, captured, template_fn, duration_loops=3):
    """Send modified reports for N loops of the captured data."""
    n = len(captured) // REPORT_SIZE
    total = n * duration_loops
    sample_idx = 0
    for pkt in range(total):
        base = captured[(pkt % n) * REPORT_SIZE:((pkt % n) + 1) * REPORT_SIZE]
        report, sample_idx = template_fn(base, sample_idx)
        dev.write(bytes(report))
        time.sleep(0.030)


def main():
    dev_info = find_ds5()
    if not dev_info:
        print("DS5 not found!")
        sys.exit(1)

    d = hid.device()
    d.open_path(dev_info["path"])

    with open("dsx_report34_capture.bin", "rb") as f:
        captured = f.read()
    n = len(captured) // REPORT_SIZE
    print(f"Loaded {n} reports\n")

    # Baseline
    print("=== BASELINE: Pure replay ===")
    input("Enter...")
    for loop in range(NUM_LOOPS):
        for i in range(n):
            d.write(captured[i * REPORT_SIZE:(i + 1) * REPORT_SIZE])
            time.sleep(0.030)
    print("That was the baseline vibration.\n")
    time.sleep(0.5)

    # Step 1: Silent template (header only)
    print("=== STEP 1: Header only, everything else zeroed ===")
    input("Enter...")
    send_test(d, captured,
              lambda base, si: (make_silent_template(base), si))
    r = input("Silent? (y/n): ").strip().lower()
    if r == 'n':
        print("Still vibrates with only header! Audio might be in header bytes.")
        print("Trying: zero bytes 1-12 too (only keep report ID 0x34)...")
        input("Enter...")

        def only_report_id(base, si):
            buf = bytearray(REPORT_SIZE)
            buf[0] = base[0]
            crc = calc_crc(bytes(buf[:CRC_OFFSET]))
            struct.pack_into('<I', buf, CRC_OFFSET, crc)
            return buf, si

        send_test(d, captured, only_report_id)
        r2 = input("Silent now? (y/n): ").strip().lower()
        if r2 == 'n':
            print("Even with ONLY report ID?! Something is very wrong.")
            d.close()
            return
    time.sleep(0.5)

    # Step 2: Inject sine into sub-regions
    print("\n=== STEP 2: Finding audio position ===")
    print("Injecting loud 200Hz sine into different byte ranges.\n")

    regions = [
        (1, 13, "header 1-12"),
        (13, 77, "bytes 13-76"),
        (77, 139, "bytes 77-138"),
        (139, 200, "bytes 139-199"),
        (200, 266, "bytes 200-265"),
    ]

    results = {}
    for start, end, desc in regions:
        print(f"--- Sine in {desc} ---")
        input("Enter...")

        def inject_fn(base, si, s=start, e=end):
            tmpl = make_silent_template(base)
            return make_sine_at(tmpl, s, e, si, freq=200, amp=80)

        send_test(d, captured, inject_fn)
        r = input("Vibration? (y/n): ").strip().lower()
        results[(start, end)] = r == 'y'
        status = "YES" if r == 'y' else "no"
        print(f"  → {status}\n")
        time.sleep(0.5)

    print("========================================")
    print("RESULTS")
    print("========================================")
    for (start, end), vibrated in results.items():
        marker = "◀ AUDIO" if vibrated else ""
        print(f"  Bytes {start:3d}-{end - 1:3d}: {'vibrates' if vibrated else 'silent'} {marker}")

    d.close()


if __name__ == "__main__":
    main()
