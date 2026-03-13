#!/usr/bin/env python3
"""DS5 Bridge Client (UDP) - Bidirectional: reads real DS5 → sends input to host,
receives output reports from host → writes to real DS5.

Usage: python client.py <host_ip> [--port 5555]
"""
import argparse
import select
import socket
import sys
import threading
import time

try:
    import hid
except ImportError:
    print("pip install hidapi")
    sys.exit(1)

DS5_VID = 0x054C
DS5_PIDS = {0x0CE6, 0x0DF2}
DEFAULT_PORT = 5555
USB_REPORT_SIZE = 64


def find_ds5():
    for info in hid.enumerate(DS5_VID):
        if info["product_id"] in DS5_PIDS:
            return info
    return None


def output_receiver(sock, dev, is_bt):
    """Receive output reports from host and write to real DS5."""
    out_count = 0
    while True:
        try:
            data, addr = sock.recvfrom(256)
            if len(data) < 2:
                continue

            if is_bt:
                # Convert USB output report to BT format
                # USB: 0x02 + 47 bytes = 48 bytes
                # BT:  0x31 + 1 seq + 1 tag + data + CRC32
                # For now, write raw — hidapi handles framing
                bt_report = bytearray(78)
                bt_report[0] = 0x31  # BT output report ID
                bt_report[1] = 0x02  # flags
                bt_report[2] = data[1] if len(data) > 1 else 0  # flags2
                copy_len = min(len(data) - 1, 75)
                bt_report[3:3 + copy_len] = data[1:1 + copy_len]
                dev.write(bytes(bt_report))
            else:
                # USB: write as-is
                dev.write(bytes(data))

            out_count += 1
            if out_count == 1:
                print(f"\n  [OUTPUT] First output report received ({len(data)} bytes)")
            elif out_count % 100 == 0:
                print(f"\n  [OUTPUT] {out_count} reports forwarded to DS5")

        except Exception as e:
            print(f"\n  [OUTPUT] Error: {e}")
            break


def main():
    parser = argparse.ArgumentParser(description="DS5 Bridge Client (UDP)")
    parser.add_argument("host", help="Host IP address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    # Find DS5
    print("Searching for DualSense...")
    info = find_ds5()
    if not info:
        print("No DualSense found!")
        return 1

    name = "DualSense Edge" if info["product_id"] == 0x0DF2 else "DualSense"
    print(f"Found: {name}")

    # Open device
    dev = hid.device()
    try:
        dev.open_path(info["path"])
    except Exception as e:
        print(f"Failed to open: {e}")
        return 1

    # Detect BT vs USB
    test = dev.read(128, 1000)
    if not test:
        print("No data from controller!")
        dev.close()
        return 1

    is_bt = len(test) > 64
    print(f"Connection: {'Bluetooth' if is_bt else 'USB'}")
    print(f"Report: {len(test)} bytes, first=0x{test[0]:02X}")

    # UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))  # Bind to any port so we can receive replies
    target = (args.host, args.port)
    print(f"Sending to {args.host}:{args.port}")
    print(f"Listening for output reports on port {sock.getsockname()[1]}\n")

    # Start output receiver thread
    out_thread = threading.Thread(target=output_receiver, args=(sock, dev, is_bt),
                                  daemon=True)
    out_thread.start()

    count = 0
    start = time.monotonic()
    last_print = start

    try:
        while True:
            data = dev.read(128, 50)
            if not data:
                continue

            # Convert to 64-byte USB format
            report = bytearray(USB_REPORT_SIZE)
            report[0] = 0x01

            if is_bt:
                if data[0] == 0x31:
                    src = data[2:]
                else:
                    src = data[1:]
                copy_len = min(len(src), USB_REPORT_SIZE - 1)
                report[1:1 + copy_len] = src[:copy_len]
            else:
                if data[0] == 0x01:
                    src = data[1:]
                else:
                    src = data
                copy_len = min(len(src), USB_REPORT_SIZE - 1)
                report[1:1 + copy_len] = src[:copy_len]

            sock.sendto(bytes(report), target)
            count += 1

            now = time.monotonic()
            if now - last_print >= 2.0:
                rate = count / (now - start)
                print(f"\r  [{count} pkts, {rate:.0f}/s] "
                      f"LX={report[1]:3d} LY={report[2]:3d} "
                      f"RX={report[3]:3d} RY={report[4]:3d}",
                      end="", flush=True)
                last_print = now

    except KeyboardInterrupt:
        pass

    elapsed = time.monotonic() - start
    print(f"\n\nDone. {count} packets in {elapsed:.1f}s")
    dev.close()
    sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
