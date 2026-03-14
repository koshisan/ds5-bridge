#!/usr/bin/env python3
"""DS5 Bridge Client (UDP) - Bidirectional DS5 bridge.

Usage: python client.py <host_ip> [--port 5555]
"""
import argparse
import socket
import struct
import sys
import threading
import time
import zlib

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


def ds5_bt_crc32(data):
    """CRC32 with DS5 BT seed byte 0xA2."""
    return zlib.crc32(bytes([0xA2]) + data) & 0xFFFFFFFF


def output_receiver(sock, dev, is_bt):
    """Receive output reports from host and write to real DS5."""
    out_count = 0
    seq = 0

    while True:
        try:
            data, addr = sock.recvfrom(256)
            if len(data) < 2:
                continue

            if is_bt:
                # Build BT output report (78 bytes)
                # USB input: [0]=0x02 [1]=flags0 [2]=flags1 [3..]=data
                # BT output: [0]=0x31 [1]=seq [2]=0x10 [3]=flags0 [4]=flags1 [5..]=data [74..77]=CRC32
                bt_out = bytearray(78)
                bt_out[0] = 0x31  # BT report ID
                bt_out[1] = seq   # Sequence number
                bt_out[2] = 0x10  # Tag: HID output

                # Copy USB payload (skip report ID byte 0x02)
                usb_payload = data[1:] if len(data) > 1 else b''
                copy_len = min(len(usb_payload), 71)  # 78 - 3 header - 4 CRC
                bt_out[3:3 + copy_len] = usb_payload[:copy_len]

                # Calculate CRC32 over bytes 0..73
                crc = ds5_bt_crc32(bytes(bt_out[:74]))
                struct.pack_into('<I', bt_out, 74, crc)

                dev.write(bytes(bt_out))
                seq = (seq + 16) & 0xFF
            else:
                # USB: write as-is
                dev.write(bytes(data))

            out_count += 1
            h = data[:10].hex(" ")
            print(f"  [OUTPUT] #{out_count} {len(data)}B: {h}")

        except Exception as e:
            print(f"\n  [OUTPUT] Error: {e}")
            break



def haptic_receiver(haptic_sock, dev, is_bt):
    """Receive haptic audio packets and send as BT Report 0x32."""
    haptic_count = 0
    seq = 0

    if not is_bt:
        print("  [HAPTIC] Skipping - USB connection, haptics only work over BT")
        return

    print(f"  [HAPTIC] Listening on port {haptic_sock.getsockname()[1]}")

    while True:
        try:
            data, addr = haptic_sock.recvfrom(256)
            if len(data) < 2 or data[0] != 0x32:
                continue

            audio_seq = data[1]
            audio_samples = data[2:66]  # 64 bytes of audio (32 stereo samples)

            if len(audio_samples) < 64:
                audio_samples = audio_samples + bytes(64 - len(audio_samples))

            # Build BT Report 0x32 (141 bytes)
            # [0]=0x32 [1]=tag(4)|seq(4) [2-10]=Packet 0x11 (control, 9B)
            # [11-76]=Packet 0x12 (audio, 2+64B) [77-136]=padding [137-140]=CRC32
            bt_out = bytearray(141)
            bt_out[0] = 0x32

            # Tag nibble (0x4) | Sequence nibble
            bt_out[1] = 0x40 | (seq & 0x0F)

            # Packet 0x11: Control packet (9 bytes, all zero = no changes)
            bt_out[2] = 0x11
            # bytes 3-10 = zeros (no control changes)

            # Packet 0x12: Audio packet
            bt_out[11] = 0x12
            bt_out[12] = 0x00  # Sub-type / flags

            # Audio samples (64 bytes = 32 stereo samples, 8-bit unsigned)
            bt_out[13:13+64] = audio_samples

            # CRC32 over bytes 0..136, seed 0xA2
            crc = ds5_bt_crc32(bytes(bt_out[:137]))
            struct.pack_into('<I', bt_out, 137, crc)

            dev.write(bytes(bt_out))
            seq = (seq + 1) & 0x0F
            haptic_count += 1

            print(f"  [HAPTIC] #{haptic_count} seq={audio_seq} {len(audio_samples)}B", flush=True)

        except Exception as e:
            print(f"\n  [HAPTIC] Error: {e}")
            break


def main():
    parser = argparse.ArgumentParser(description="DS5 Bridge Client (UDP)")
    parser.add_argument("host", help="Host IP address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    print("Searching for DualSense...")
    info = find_ds5()
    if not info:
        print("No DualSense found!")
        return 1

    name = "DualSense Edge" if info["product_id"] == 0x0DF2 else "DualSense"
    print(f"Found: {name}")

    dev = hid.device()
    try:
        dev.open_path(info["path"])
    except Exception as e:
        print(f"Failed to open: {e}")
        return 1

    test = dev.read(128, 1000)
    if not test:
        print("No data from controller!")
        dev.close()
        return 1

    is_bt = len(test) > 64
    print(f"Connection: {'Bluetooth' if is_bt else 'USB'}")
    print(f"Report: {len(test)} bytes, first=0x{test[0]:02X}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    target = (args.host, args.port)
    print(f"Sending to {args.host}:{args.port}")
    print(f"Listening for output reports on port {sock.getsockname()[1]}\n")

    out_thread = threading.Thread(target=output_receiver, args=(sock, dev, is_bt),
                                  daemon=True)
    out_thread.start()

    # Haptic audio receiver (port 5556)
    haptic_port = args.port + 1  # 5556 by default
    haptic_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    haptic_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    haptic_sock.bind(("0.0.0.0", haptic_port))
    haptic_thread = threading.Thread(target=haptic_receiver, args=(haptic_sock, dev, is_bt),
                                     daemon=True)
    haptic_thread.start()

    count = 0
    start = time.monotonic()
    last_print = start

    try:
        while True:
            data = dev.read(128, 50)
            if not data:
                continue

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
