#!/usr/bin/env python3
"""
Minimal test: take captured DSX reports, replace ONLY the suspected
audio region with a sine wave, recalculate CRC, send.

If this vibrates differently than replay → audio offset is correct.
If identical to replay → audio offset is wrong (we're overwriting non-audio bytes).
If no vibration → something else is broken.

Usage:
    python test_report34_minimal.py
"""

import hid
import time
import struct
import math
import binascii
import sys


DS5_VID = 0x054C
DS5_PID = 0x0CE6


def calc_crc(data):
    return binascii.crc32(bytes([0xA2]) + data) & 0xFFFFFFFF


def find_ds5():
    for dev in hid.enumerate(DS5_VID, DS5_PID):
        if dev.get("usage_page") == 1 or dev.get("interface_number", -1) == -1:
            return dev
    return None


def main():
    dev_info = find_ds5()
    if not dev_info:
        print("DS5 not found!")
        sys.exit(1)

    print(f"Found: {dev_info['product_string']}")
    d = hid.device()
    d.open_path(dev_info["path"])

    # Load captured reports
    with open("dsx_report34_capture.bin", "rb") as f:
        captured = f.read()

    num_reports = len(captured) // 547
    print(f"Loaded {num_reports} captured reports")

    # Test 1: Pure replay (sanity check)
    print("\n=== TEST 1: Pure replay (should vibrate) ===")
    input("Press Enter to start...")
    for i in range(num_reports):
        d.write(captured[i * 547:(i + 1) * 547])
        time.sleep(0.030)
    print("Done. Did it vibrate? (y/n)")
    t1 = input().strip().lower()

    time.sleep(1)

    # Test 2: Replace bytes 13-138 with sine, keep everything else from capture
    print("\n=== TEST 2: Replace bytes 13-138 with 200Hz sine ===")
    input("Press Enter to start...")
    sample_idx = 0
    for i in range(num_reports):
        buf = bytearray(captured[i * 547:(i + 1) * 547])

        # Replace suspected audio region with sine
        for s in range(13, 139, 2):
            t = sample_idx / 4158.0
            val = int(5 * math.sin(2 * math.pi * 200 * t))  # small amplitude like captured
            val = max(-128, min(127, val))
            buf[s] = val & 0xFF
            buf[s + 1] = val & 0xFF
            sample_idx += 1

        # Recalculate CRC
        crc = calc_crc(bytes(buf[:266]))
        struct.pack_into('<I', buf, 266, crc)

        d.write(bytes(buf))
        time.sleep(0.030)
    print("Done. Did it vibrate differently? (y/n)")
    t2 = input().strip().lower()

    time.sleep(1)

    # Test 3: Zero out bytes 13-138, keep everything else
    print("\n=== TEST 3: Zero out bytes 13-138 (silence) ===")
    input("Press Enter to start...")
    for i in range(num_reports):
        buf = bytearray(captured[i * 547:(i + 1) * 547])
        buf[13:139] = b'\x00' * 126
        crc = calc_crc(bytes(buf[:266]))
        struct.pack_into('<I', buf, 266, crc)
        d.write(bytes(buf))
        time.sleep(0.030)
    print("Done. Silent? (y/n)")
    t3 = input().strip().lower()

    time.sleep(1)

    # Test 4: Zero out bytes 0-12 header (but keep report ID), keep audio from capture
    print("\n=== TEST 4: Zero header bytes 1-12, keep audio from capture ===")
    input("Press Enter to start...")
    for i in range(num_reports):
        buf = bytearray(captured[i * 547:(i + 1) * 547])
        buf[1:13] = b'\x00' * 12
        crc = calc_crc(bytes(buf[:266]))
        struct.pack_into('<I', buf, 266, crc)
        d.write(bytes(buf))
        time.sleep(0.030)
    print("Done. Still vibrate? (y/n)")
    t4 = input().strip().lower()

    print(f"\n=== RESULTS ===")
    print(f"Test 1 (pure replay):     {t1}")
    print(f"Test 2 (sine at 13-138):  {t2}")
    print(f"Test 3 (zero at 13-138):  {t3}")
    print(f"Test 4 (zero header 1-12):{t4}")

    if t2 == 'y' and t3 == 'y':
        print("\n→ Bytes 13-138 ARE the audio region!")
    elif t2 == 'n' and t3 == 'n':
        print("\n→ Bytes 13-138 are NOT audio. Need to search elsewhere.")
    else:
        print("\n→ Mixed results. Need more tests.")

    d.close()


if __name__ == "__main__":
    main()
