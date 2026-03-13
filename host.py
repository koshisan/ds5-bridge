#!/usr/bin/env python3
"""DS5 Bridge Host - Receives input from Bridge Client (TCP) and feeds DS5Virtual driver.

This runs on the gaming/streaming PC alongside the DS5Virtual driver.
Flow: Remote DS5 -> Bridge Client -> TCP -> This Host -> Named Pipe -> DS5Virtual Driver

Usage: python host.py [--port 5555]
"""

import argparse
import logging
import socket
import struct
import sys
import threading
import time

from protocol import (
    MSG_CONTROLLER_INFO,
    MSG_INPUT_REPORT,
    MSG_OUTPUT_REPORT,
    MSG_PING,
    frame_message,
    read_frame,
)

PIPE_PATH = r"\\.\pipe\ds5virtual"
USB_REPORT_SIZE = 64
DEFAULT_PORT = 5555

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("host")


def bt_to_usb_report(bt_data: bytes) -> bytes | None:
    """Convert a BT input report (0x31) to USB format (0x01).
    
    BT: [0x31, padding, ...63 bytes data...]
    USB: [0x01, ...63 bytes data...]
    """
    if len(bt_data) < 3 or bt_data[0] != 0x31:
        return None
    usb = bytearray(USB_REPORT_SIZE)
    usb[0] = 0x01
    copy_len = min(len(bt_data) - 2, USB_REPORT_SIZE - 1)
    usb[1:1 + copy_len] = bt_data[2:2 + copy_len]
    return bytes(usb)


def open_pipe():
    """Open the DS5Virtual named pipe for writing."""
    try:
        return open(PIPE_PATH, 'wb', buffering=0)
    except FileNotFoundError:
        log.error(f"Pipe {PIPE_PATH} not found! Is DS5Virtual driver loaded?")
        return None
    except PermissionError:
        log.error("Permission denied opening pipe!")
        return None


def handle_client(conn: socket.socket, addr, pipe):
    """Handle a single bridge client connection."""
    log.info(f"Client connected: {addr}")
    
    count = 0
    start = time.monotonic()
    controller_info = None
    is_bt = False
    
    try:
        while True:
            frame = read_frame(conn)
            if frame is None:
                log.info("Client disconnected")
                break
            
            msg_type, payload = frame
            
            if msg_type == MSG_CONTROLLER_INFO:
                import json
                controller_info = json.loads(payload.decode("utf-8"))
                is_bt = controller_info.get("connection_mode") == "BT"
                log.info(f"Controller: {controller_info.get('controller_type', '?')} "
                         f"({controller_info.get('connection_mode', '?')})")
            
            elif msg_type == MSG_INPUT_REPORT:
                # Convert to USB format if needed
                if is_bt:
                    usb_report = bt_to_usb_report(payload)
                    if not usb_report:
                        continue
                else:
                    # Already USB format, ensure 64 bytes
                    usb_report = bytearray(USB_REPORT_SIZE)
                    copy_len = min(len(payload), USB_REPORT_SIZE)
                    usb_report[:copy_len] = payload[:copy_len]
                    usb_report = bytes(usb_report)
                
                try:
                    pipe.write(usb_report)
                    count += 1
                except (BrokenPipeError, OSError) as e:
                    log.error(f"Pipe write failed: {e}")
                    # Try to reopen pipe
                    pipe = open_pipe()
                    if not pipe:
                        break
                
                # Status every 5 seconds
                if count % 300 == 0:
                    elapsed = time.monotonic() - start
                    rate = count / elapsed if elapsed > 0 else 0
                    lx, ly = usb_report[1], usb_report[2]
                    rx, ry = usb_report[3], usb_report[4]
                    log.info(f"{count} reports ({rate:.0f}/s) "
                             f"LX={lx} LY={ly} RX={rx} RY={ry}")
            
            elif msg_type == MSG_PING:
                # Respond with pong
                pong = frame_message(MSG_PING, b"pong")
                conn.sendall(pong)
            
            elif msg_type == MSG_OUTPUT_REPORT:
                # TODO: Forward output reports (haptics, LED, adaptive triggers)
                # back to the client for the real DS5
                pass
    
    except Exception as e:
        log.error(f"Client error: {e}")
    
    elapsed = time.monotonic() - start
    log.info(f"Session ended. {count} reports in {elapsed:.1f}s")


def main():
    parser = argparse.ArgumentParser(description="DS5 Bridge Host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP listen port")
    parser.add_argument("--bind", default="0.0.0.0", help="Bind address")
    args = parser.parse_args()
    
    # Open pipe first
    log.info(f"Opening {PIPE_PATH}...")
    pipe = open_pipe()
    if not pipe:
        return 1
    log.info("Pipe connected!")
    
    # Start TCP server
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.bind, args.port))
    server.listen(1)
    
    log.info(f"Listening on {args.bind}:{args.port}")
    log.info("Waiting for Bridge Client connection...\n")
    
    try:
        while True:
            conn, addr = server.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            
            # Handle one client at a time
            handle_client(conn, addr, pipe)
            conn.close()
            
            # Reopen pipe for next client
            log.info("Reopening pipe for next client...")
            pipe = open_pipe()
            if not pipe:
                break
    
    except KeyboardInterrupt:
        log.info("\nShutting down.")
    
    if pipe:
        pipe.close()
    server.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
