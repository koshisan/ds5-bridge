#!/usr/bin/env python3
"""DS5 Bridge Host (UDP) - Receives DS5 input via UDP, feeds DS5Virtual driver.

Usage: python host.py [--port 5555]
"""
import argparse
import socket
import sys
import time

PIPE_PATH = r"\\.\pipe\ds5virtual"
DEFAULT_PORT = 5555
REPORT_SIZE = 64


def main():
    parser = argparse.ArgumentParser(description="DS5 Bridge Host (UDP)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--bind", default="0.0.0.0")
    args = parser.parse_args()

    # Open pipe
    print(f"Opening {PIPE_PATH}...")
    try:
        pipe = open(PIPE_PATH, 'wb', buffering=0)
    except FileNotFoundError:
        print("Pipe not found! Is DS5Virtual driver loaded?")
        return 1

    print("Pipe connected!")

    # UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    print(f"Listening on UDP {args.bind}:{args.port}\n")

    count = 0
    start = time.monotonic()
    last_print = start
    last_addr = None

    try:
        while True:
            data, addr = sock.recvfrom(256)
            if len(data) < 1:
                continue

            if addr != last_addr:
                print(f"Client: {addr[0]}:{addr[1]}")
                last_addr = addr

            # Ensure 64 bytes
            if len(data) < REPORT_SIZE:
                report = bytearray(REPORT_SIZE)
                report[:len(data)] = data
            else:
                report = data[:REPORT_SIZE]

            try:
                pipe.write(bytes(report))
                count += 1
            except (BrokenPipeError, OSError) as e:
                print(f"Pipe error: {e}")
                break

            now = time.monotonic()
            if now - last_print >= 0.3:
                rate = count / (now - start)
                print(f"\r  [{count} pkts, {rate:.0f}/s] "
                      f"LX={report[1]:3d} LY={report[2]:3d} "
                      f"RX={report[3]:3d} RY={report[4]:3d} "
                      f"LT={report[5]:3d} RT={report[6]:3d}",
                      end="", flush=True)
                last_print = now

    except KeyboardInterrupt:
        pass

    elapsed = time.monotonic() - start
    print(f"\n\nDone. {count} packets in {elapsed:.1f}s")
    pipe.close()
    sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
