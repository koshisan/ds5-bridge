#!/usr/bin/env python3
"""DS5 Bridge Host (UDP) - Receives DS5 input via UDP, feeds DS5Virtual driver."""
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

# PeekNamedPipe
PeekNamedPipe = kernel32.PeekNamedPipe
PeekNamedPipe.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD)]
PeekNamedPipe.restype = wintypes.BOOL


def open_pipe():
    handle = kernel32.CreateFileW(
        PIPE_PATH, GENERIC_WRITE | 0x80000000,  # GENERIC_WRITE | GENERIC_READ
        0, None, OPEN_EXISTING, 0, None)
    if handle == INVALID_HANDLE_VALUE:
        print(f"Failed to open pipe: error {ctypes.get_last_error()}")
        return None
    return handle


def write_pipe(handle, data):
    written = wintypes.DWORD(0)
    buf = (ctypes.c_byte * len(data))(*data)
    ok = kernel32.WriteFile(handle, buf, len(data), ctypes.byref(written), None)
    return ok and written.value == len(data)


def peek_pipe(handle):
    """Return number of bytes available in pipe buffer."""
    avail = wintypes.DWORD(0)
    ok = PeekNamedPipe(handle, None, 0, None, ctypes.byref(avail), None)
    if ok:
        return avail.value
    return -1


def main():
    parser = argparse.ArgumentParser(description="DS5 Bridge Host (UDP)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--bind", default="0.0.0.0")
    args = parser.parse_args()

    print(f"Opening {PIPE_PATH}...")
    handle = open_pipe()
    if not handle:
        return 1
    print("Pipe connected!")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    print(f"Listening on UDP {args.bind}:{args.port}\n")

    count = 0
    start = time.monotonic()
    last_print = start
    last_addr = None
    max_backlog = 0

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

            if not write_pipe(handle, data[:REPORT_SIZE]):
                print("Pipe write failed!")
                break

            count += 1

            # Check pipe backlog after write
            backlog = peek_pipe(handle)
            if backlog > 0:
                reports_queued = backlog // REPORT_SIZE
                if backlog > max_backlog:
                    max_backlog = backlog
                print(f"\r  BACKLOG: {backlog} bytes ({reports_queued} reports queued, max={max_backlog})")

            now = time.monotonic()
            if now - last_print >= 2.0:
                rate = count / (now - start)
                print(f"\r  [{count} pkts, {rate:.0f}/s] backlog_max={max_backlog}   ",
                      end="", flush=True)
                last_print = now
                max_backlog = 0

    except KeyboardInterrupt:
        pass

    elapsed = time.monotonic() - start
    print(f"\n\nDone. {count} packets in {elapsed:.1f}s")
    kernel32.CloseHandle(handle)
    sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
