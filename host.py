#!/usr/bin/env python3
"""DS5 Bridge Host (UDP) - Receives DS5 input via UDP, feeds DS5Virtual driver.

Usage: python host.py [--port 5555]
"""
import argparse
import ctypes
import ctypes.wintypes as wintypes
import socket
import sys
import time

PIPE_PATH = r"\\.\pipe\ds5virtual"
DEFAULT_PORT = 5555
REPORT_SIZE = 64

kernel32 = ctypes.windll.kernel32
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


def open_pipe():
    """Open pipe with CreateFile for reliable writes."""
    handle = kernel32.CreateFileW(
        PIPE_PATH,
        GENERIC_WRITE,
        0,      # no sharing
        None,   # security
        OPEN_EXISTING,
        0,      # flags
        None)   # template
    if handle == INVALID_HANDLE_VALUE:
        err = ctypes.get_last_error()
        print(f"Failed to open pipe: error {err}")
        return None
    return handle


def write_pipe(handle, data):
    """Write exactly len(data) bytes to pipe."""
    written = wintypes.DWORD(0)
    buf = (ctypes.c_byte * len(data))(*data)
    ok = kernel32.WriteFile(
        handle,
        buf,
        len(data),
        ctypes.byref(written),
        None)
    return ok and written.value == len(data)


def main():
    parser = argparse.ArgumentParser(description="DS5 Bridge Host (UDP)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--bind", default="0.0.0.0")
    args = parser.parse_args()

    # Open pipe
    print(f"Opening {PIPE_PATH}...")
    handle = open_pipe()
    if not handle:
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
    last_btn = bytes(3)

    try:
        while True:
            data, addr = sock.recvfrom(256)
            if len(data) < REPORT_SIZE:
                report = bytearray(REPORT_SIZE)
                report[:len(data)] = data
                data = bytes(report)

            if addr != last_addr:
                print(f"Client: {addr[0]}:{addr[1]}")
                last_addr = addr

            # Print on button changes
            btn = data[8:11]
            if btn != last_btn:
                print("  BTN: %02X %02X %02X" % (btn[0], btn[1], btn[2]))
                last_btn = btn

            if not write_pipe(handle, data[:REPORT_SIZE]):
                print("Pipe write failed!")
                break

            count += 1
            now = time.monotonic()
            if now - last_print >= 1.0:
                rate = count / (now - start)
                print(f"\r  [{count} pkts, {rate:.0f}/s] "
                      f"LX={data[1]:3d} LY={data[2]:3d} "
                      f"RX={data[3]:3d} RY={data[4]:3d} "
                      f"LT={data[5]:3d} RT={data[6]:3d}",
                      end="", flush=True)
                last_print = now

    except KeyboardInterrupt:
        pass

    elapsed = time.monotonic() - start
    print(f"\n\nDone. {count} packets in {elapsed:.1f}s")
    kernel32.CloseHandle(handle)
    sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
