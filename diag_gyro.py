"""Gyro diagnostic: Compare USB vs BT gyro output from ds5client perspective.

Run twice — once with DS5 on USB, once on BT.
Logs the EXACT bytes that would be sent to the server, focusing on:
  - Gyro X/Y/Z (report bytes 17-22, int16 LE)
  - Accel X/Y/Z (report bytes 23-28, int16 LE)
  - Sensor timestamp (report bytes 29-32, uint32 LE) — original from controller
  - Synthetic timestamp (what _input_loop would write at offset 28)
  - Wall-clock delta between reports
  - Original BT timestamp delta vs synthetic delta

Usage:
    python diag_gyro.py [--samples N] [--csv output.csv]
"""
import sys
import time
import struct
import argparse
import csv as csvmod

try:
    import hid
except ImportError:
    print("pip install hidapi")
    sys.exit(1)

DS5_VID = 0x054C
DS5_PIDS = {0x0CE6, 0x0DF2}


def find_ds5():
    for info in hid.enumerate(DS5_VID):
        if info['product_id'] in DS5_PIDS:
            return info
    return None


def main():
    parser = argparse.ArgumentParser(description='DS5 Gyro Diagnostic')
    parser.add_argument('--samples', type=int, default=1000, help='Number of samples to capture')
    parser.add_argument('--csv', type=str, default=None, help='Output CSV file')
    parser.add_argument('--quiet', action='store_true', help='Only write CSV, no console output')
    args = parser.parse_args()

    info = find_ds5()
    if not info:
        print("DualSense not found!")
        sys.exit(1)

    dev = hid.device()
    dev.open_path(info['path'])

    # Detect USB vs BT
    test = dev.read(128, 2000)
    if not test:
        print("No data from controller!")
        sys.exit(1)

    is_bt = len(test) > 64
    conn_type = "BT" if is_bt else "USB"
    name = "DualSense Edge" if info['product_id'] == 0x0DF2 else "DualSense"
    print(f"{name} ({conn_type}, report size={len(test)}B)")
    print(f"Capturing {args.samples} samples...\n")

    # CSV setup
    csv_file = None
    csv_writer = None
    if args.csv:
        csv_file = open(args.csv, 'w', newline='')
        csv_writer = csvmod.writer(csv_file)
        csv_writer.writerow([
            'sample', 'conn', 'wall_time_ms', 'wall_delta_us',
            'orig_ts', 'orig_ts_delta', 'synth_ts', 'synth_ts_delta',
            'gyro_x', 'gyro_y', 'gyro_z',
            'accel_x', 'accel_y', 'accel_z',
            'report_hex_16_32'  # bytes 16-32 of the output report (gyro+accel+ts region)
        ])

    # Simulate _input_loop's synthetic timestamp
    usb_ts = 0

    samples = []
    prev_wall = None
    prev_orig_ts = None
    prev_synth_ts = None
    start_wall = time.perf_counter()

    for i in range(args.samples):
        data = dev.read(128, 100)
        if not data:
            continue

        wall_now = time.perf_counter()
        wall_ms = (wall_now - start_wall) * 1000.0
        wall_delta_us = (wall_now - prev_wall) * 1_000_000 if prev_wall else 0
        prev_wall = wall_now

        # Build output report exactly like _input_loop
        report = bytearray(64)
        report[0] = 0x01

        if is_bt:
            src = data[2:] if data[0] == 0x31 else data[1:]
        else:
            src = data[1:] if data[0] == 0x01 else data

        copy_len = min(len(src), 63)
        report[1:1 + copy_len] = src[:copy_len]

        # Read ORIGINAL timestamp before overwrite (offset 28-31 in report)
        orig_ts = struct.unpack_from('<I', report, 28)[0]
        orig_ts_delta = (orig_ts - prev_orig_ts) & 0xFFFFFFFF if prev_orig_ts is not None else 0
        prev_orig_ts = orig_ts

        # Synthetic timestamp (what _input_loop does for BT)
        usb_ts = (usb_ts + 12121) & 0xFFFFFFFF
        synth_ts_delta = 12121  # always constant by design
        if is_bt:
            struct.pack_into('<I', report, 28, usb_ts)

        # Extract gyro/accel from the FINAL report (after potential ts overwrite)
        # DS5 USB layout (report-relative offsets, report[0]=0x01):
        # Gyro: report[16:22], Accel: report[22:28], Timestamp: report[28:32]
        gyro_x = struct.unpack_from('<h', report, 16)[0]
        gyro_y = struct.unpack_from('<h', report, 18)[0]
        gyro_z = struct.unpack_from('<h', report, 20)[0]
        accel_x = struct.unpack_from('<h', report, 22)[0]
        accel_y = struct.unpack_from('<h', report, 24)[0]
        accel_z = struct.unpack_from('<h', report, 26)[0]
        final_ts = struct.unpack_from('<I', report, 28)[0]

        sample = {
            'i': i,
            'wall_ms': wall_ms,
            'wall_delta_us': wall_delta_us,
            'orig_ts': orig_ts,
            'orig_ts_delta': orig_ts_delta,
            'synth_ts': usb_ts if is_bt else orig_ts,
            'synth_ts_delta': synth_ts_delta if is_bt else orig_ts_delta,
            'gyro_x': gyro_x, 'gyro_y': gyro_y, 'gyro_z': gyro_z,
            'accel_x': accel_x, 'accel_y': accel_y, 'accel_z': accel_z,
            'report_hex': report[16:32].hex(' '),
        }
        samples.append(sample)

        if csv_writer:
            csv_writer.writerow([
                i, conn_type, f'{wall_ms:.2f}', f'{wall_delta_us:.0f}',
                orig_ts, orig_ts_delta, sample['synth_ts'], sample['synth_ts_delta'],
                gyro_x, gyro_y, gyro_z,
                accel_x, accel_y, accel_z,
                report[16:32].hex(' ')
            ])

        if not args.quiet and i % 50 == 0:
            print(f"[{i:4d}] wall_dt={wall_delta_us:7.0f}µs  "
                  f"orig_ts_dt={orig_ts_delta:6d}  "
                  f"gyro=({gyro_x:6d},{gyro_y:6d},{gyro_z:6d})  "
                  f"ts_final={final_ts}")

    dev.close()

    if csv_file:
        csv_file.close()
        print(f"\nCSV saved to {args.csv}")

    # === Summary Statistics ===
    print(f"\n{'='*60}")
    print(f"Connection: {conn_type}")
    print(f"Samples: {len(samples)}")

    if len(samples) > 1:
        wall_deltas = [s['wall_delta_us'] for s in samples[1:]]
        orig_deltas = [s['orig_ts_delta'] for s in samples[1:]]

        print(f"\n--- Wall-clock delta (µs) ---")
        print(f"  Mean:   {sum(wall_deltas)/len(wall_deltas):8.1f}")
        print(f"  Min:    {min(wall_deltas):8.1f}")
        print(f"  Max:    {max(wall_deltas):8.1f}")
        print(f"  StdDev: {(sum((d - sum(wall_deltas)/len(wall_deltas))**2 for d in wall_deltas) / len(wall_deltas))**0.5:8.1f}")

        print(f"\n--- Original sensor timestamp delta (0.33µs ticks) ---")
        print(f"  Mean:   {sum(orig_deltas)/len(orig_deltas):8.1f}")
        print(f"  Min:    {min(orig_deltas):8.1f}")
        print(f"  Max:    {max(orig_deltas):8.1f}")
        print(f"  StdDev: {(sum((d - sum(orig_deltas)/len(orig_deltas))**2 for d in orig_deltas) / len(orig_deltas))**0.5:8.1f}")

        expected_delta = 12121  # 4ms @ 0.33µs/tick
        outliers = [d for d in orig_deltas if abs(d - expected_delta) > expected_delta * 0.5]
        print(f"  Outliers (>50% off expected {expected_delta}): {len(outliers)} / {len(orig_deltas)}")

        if is_bt:
            print(f"\n--- Synthetic vs Original timestamp comparison ---")
            print(f"  Synthetic delta: always {12121} (constant)")
            print(f"  Original delta range: {min(orig_deltas)} - {max(orig_deltas)}")
            # Show how much the original timestamps deviate from 12121
            deviations = [abs(d - 12121) for d in orig_deltas]
            print(f"  Mean deviation from 12121: {sum(deviations)/len(deviations):.1f} ticks")
            print(f"  Max deviation from 12121: {max(deviations)} ticks")

            # Check if original BT timestamps are actually stable
            if max(deviations) < 500:  # less than ~165µs jitter
                print(f"\n  ⚠️  Original BT timestamps are STABLE (max dev {max(deviations)} ticks)")
                print(f"  → The synthetic timestamp replacement may be UNNECESSARY")
                print(f"  → Try using original BT timestamps instead!")
            else:
                print(f"\n  ⚠️  Original BT timestamps are JITTERY (max dev {max(deviations)} ticks)")
                print(f"  → But constant synthetic timestamps may cause different problems")
                print(f"  → Consider using smoothed original timestamps instead")

        # Gyro range
        gx = [s['gyro_x'] for s in samples]
        gy = [s['gyro_y'] for s in samples]
        gz = [s['gyro_z'] for s in samples]
        print(f"\n--- Gyro ranges ---")
        print(f"  X: {min(gx):6d} to {max(gx):6d}  (range {max(gx)-min(gx)})")
        print(f"  Y: {min(gy):6d} to {max(gy):6d}  (range {max(gy)-min(gy)})")
        print(f"  Z: {min(gz):6d} to {max(gz):6d}  (range {max(gz)-min(gz)})")

        # Check for zero-runs or stuck values in gyro (could indicate bad offset)
        zero_runs = sum(1 for s in samples if s['gyro_x'] == 0 and s['gyro_y'] == 0 and s['gyro_z'] == 0)
        if zero_runs > len(samples) * 0.1:
            print(f"\n  ⚠️  {zero_runs} samples with ALL gyro axes = 0 ({zero_runs*100/len(samples):.1f}%)")
            print(f"  → Possible offset issue or data not reaching these bytes")

    print(f"\n{'='*60}")
    print("Run this once with USB, once with BT, then compare the CSVs!")
    print("  USB: python diag_gyro.py --csv gyro_usb.csv")
    print("  BT:  python diag_gyro.py --csv gyro_bt.csv")


if __name__ == '__main__':
    main()
