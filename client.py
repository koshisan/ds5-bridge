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

def ds5_bt_crc32_seed(data, seed):
    """CRC32 with configurable seed byte."""
    return zlib.crc32(bytes([seed]) + data) & 0xFFFFFFFF


def output_receiver(sock, dev, is_bt, haptic_queue=None):
    """Receive output reports from host and write to real DS5."""
    out_count = 0
    seq = 0

    while True:
        try:
            data, addr = sock.recvfrom(256)
            if len(data) < 2:
                continue
            # Debug: log all non-input packets from server
            if len(data) != 64 and data[0] not in (0x01,):
                print(f'  [RECV] {len(data)}B first=0x{data[0]:02X} from {addr}')

            # Route 0x32 haptic packets to haptic handler
            if data[0] == 0x32 and haptic_queue is not None:
                haptic_queue.put((data, addr))
                continue

            # Feature request: [0x03, reportId]
            if data[0] == 0x03 and len(data) >= 2:
                report_id = data[1]
                try:
                    response = dev.get_feature_report(report_id, 256)
                    if response:
                        resp_bytes = bytes(response)
                        # BT feature reports may include report ID as first byte
                        # Strip it if present to send clean data
                        if resp_bytes[0] == report_id:
                            resp_bytes = resp_bytes  # keep as-is, driver expects it
                        pkt = bytes([0x04, report_id]) + resp_bytes
                        sock.sendto(pkt, addr)
                        print(f'  [FEATURE] GET 0x{report_id:02X} -> {len(resp_bytes)}B: {resp_bytes[:20].hex(chr(32))}')
                    else:
                        print(f'  [FEATURE] GET 0x{report_id:02X} -> empty')
                except Exception as e:
                    print(f'  [FEATURE] GET 0x{report_id:02X} error: {e}')
                continue

            # Set feature: [0x05, reportId, data...]
            if data[0] == 0x05 and len(data) >= 2:
                report_id = data[1]
                payload = data[1:]  # includes report ID
                print(f'  [FEATURE] SET 0x{report_id:02X} ({len(payload)}B): {bytes(payload[:16]).hex(chr(32))}')
                try:
                    if is_bt:
                        # BT feature reports need CRC32 (seed 0xA3)
                        # Format: [reportId, ...data..., CRC32]
                        buf = bytearray(payload)
                        # Pad to expected size if needed
                        while len(buf) < 74:
                            buf.append(0)
                        crc = ds5_bt_crc32_seed(bytes(buf[:len(buf)]), 0xA3)
                        buf.extend(struct.pack("<I", crc))
                        dev.send_feature_report(bytes(buf))
                        print(f'  [FEATURE] SET 0x{report_id:02X} BT with CRC ({len(buf)}B)')
                    else:
                        dev.send_feature_report(payload)
                except Exception as e:
                    print(f'  [FEATURE] SET 0x{report_id:02X} error: {e}')
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

        except ConnectionResetError:
            # ICMP port unreachable from server - ignore, server may not be up yet
            continue
        except Exception as e:
            print(f"\n  [OUTPUT] Error: {e}")
            break



def haptic_receiver(haptic_queue, dev, is_bt):
    """Receive haptic audio packets and send as BT Report 0x32."""
    haptic_count = 0
    seq = 0

    REPORT_ID = 0x32
    REPORT_SIZE = 141
    SAMPLE_SIZE = 64

    if not is_bt:
        print("  [HAPTIC] Skipping - USB connection, haptics only work over BT")
        return

    print(f"  [HAPTIC] Listening on main UDP socket")

    while True:
        try:
            data, addr = haptic_queue.get()
            if len(data) < 2 or data[0] != 0x32:
                continue

            audio_seq = data[1]
            audio_samples = data[2:66]  # 64 bytes of audio (32 stereo samples)

            if len(audio_samples) < SAMPLE_SIZE:
                audio_samples = audio_samples + bytes(SAMPLE_SIZE - len(audio_samples))

            # Build Report 0x32 using proven format from haptic_demo.py
            payload_size = REPORT_SIZE - 1 - 4  # 136 bytes (minus report_id and crc)

            # Packet 0x11: control (pid=0x11, sized=1, length=7)
            pkt_0x11 = bytes([
                (0x11 & 0x3F) | (0 << 6) | (1 << 7),  # pid=0x11, unk=0, sized=1
                7,  # length
                0b11111110, 0, 0, 0, 0, seq & 0xFF, 0  # data (7 bytes)
            ])

            # Packet 0x12: audio samples (pid=0x12, sized=1, length=64)
            pkt_0x12_header = bytes([
                (0x12 & 0x3F) | (0 << 6) | (1 << 7),  # pid=0x12, unk=0, sized=1
                SAMPLE_SIZE,  # length
            ])

            # Build payload
            packets = pkt_0x11 + pkt_0x12_header + bytes(audio_samples)
            payload = packets.ljust(payload_size, b'\x00')

            # Tag=0, seq in upper nibble
            tag_seq = (seq & 0x0F) << 4

            report_body = bytes([tag_seq]) + payload

            # CRC32 over report_id + body
            crc_data = bytes([REPORT_ID]) + report_body
            crc = ds5_bt_crc32(crc_data)

            # Final report
            report = bytes([REPORT_ID]) + report_body + struct.pack('<I', crc)
            dev.write(report)

            seq = (seq + 1) & 0x0F
            haptic_count += 1

            print(f"  [HAPTIC] #{haptic_count} {report[:20].hex(' ')}", flush=True)

        except ConnectionResetError:
            continue
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

    # Haptic audio receiver (shared queue from output_receiver)
    import queue
    haptic_q = queue.Queue()
    haptic_thread = threading.Thread(target=haptic_receiver, args=(haptic_q, dev, is_bt),
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
