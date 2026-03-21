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

def ds5_crc32_payload(seed_bytes, data_bytes):
    """CRC32 for DS5 feature report payload. seed_bytes e.g. [0x53, 0x80]."""
    return zlib.crc32(bytes(seed_bytes) + bytes(data_bytes)) & 0xFFFFFFFF

def ds5_bt_crc32_seed(data, seed):
    """CRC32 with configurable seed byte."""
    return zlib.crc32(bytes([seed]) + data) & 0xFFFFFFFF


def output_receiver(sock, dev, is_bt, haptic_queue=None):
    """Receive output reports from host and write to real DS5."""
    out_count = 0
    seq = 0

    while True:
        try:
            data, addr = sock.recvfrom(4096)
            if len(data) < 2:
                continue
            # Debug: log ALL received packets

            # Route audio packets to haptic handler
            if data[0] in (0x32, 0x40) and haptic_queue is not None:
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
                    else:
                        pass
                except Exception as e:
                    print(f'  [FEATURE] GET 0x{report_id:02X} error: {e}')
                continue

            # Set feature: [0x05, reportId, data...]
            if data[0] == 0x05 and len(data) >= 2:
                report_id = data[1]
                payload = data[1:]  # includes report ID
                try:
                    if is_bt:
                        # DS5 BT feature SET needs payload CRC32
                        # Seed: [0x53, reportId], placed in last 4 bytes of 64-byte report
                        # Layout: [reportId, subcmd, subsub, ...zeros..., CRC32] = 64 bytes
                        buf = bytearray(64)
                        buf[0] = report_id
                        # Copy payload data (skip report_id since it's already buf[0])
                        pdata = data[2:]  # skip 0x05 prefix and report_id
                        buf[1:1+len(pdata)] = pdata[:63]
                        # CRC32 over buf[1..59] with seed [0x53, reportId]
                        crc = ds5_crc32_payload([0x53, report_id], buf[1:60])
                        struct.pack_into('<I', buf, 60, crc)
                        dev.send_feature_report(bytes(buf))
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

        except ConnectionResetError:
            # ICMP port unreachable from server - ignore, server may not be up yet
            continue
        except Exception as e:
            print(f"\n  [OUTPUT] Error: {e}")
            break



def _load_haptic_template():
    """Load captured Report 0x34 template for BT haptics."""
    import os
    for path in [os.path.join(os.path.dirname(__file__), "dsx_report34_capture.bin"),
                 "dsx_report34_capture.bin"]:
        try:
            with open(path, "rb") as f:
                data = f.read(547)
            if len(data) >= 547:
                return data[:547]
        except FileNotFoundError:
            pass

    # Default template from known constants
    buf = bytearray(547)
    buf[0] = 0x34
    buf[2:5] = b'\x91\x07\xfe'
    buf[5:10] = b'\x30\x30\x30\x30\x30'
    buf[11] = 0xD2; buf[12] = 0x40
    buf[139:143] = b'\x90\x3f\xfd\xf7'
    buf[145:149] = b'\x7e\x7f\xff\x09'
    buf[150] = 0x0F
    buf[178:180] = b'\x0a\x07'
    buf[182] = 0x02; buf[184] = 0x05
    return bytes(buf)


# Report 0x34 layout
_R34_SIZE = 547
_R34_AUDIO_START = 13
_R34_AUDIO_END = 139
_R34_AUDIO_LEN = 126   # 63 stereo frames
_R34_CRC_OFFSET = 266
_R34_TARGET_FRAMES = 63
_R34_DOWNSAMPLE_RATIO = 48000 / 2079  # ~23.09
_R34_INPUT_FRAMES_NEEDED = int(_R34_TARGET_FRAMES * _R34_DOWNSAMPLE_RATIO)  # ~1455


def haptic_receiver(haptic_queue, dev, is_bt):
    """Receive raw s16 48kHz audio, accumulate, downsample, send as Report 0x34.

    No timer, no sleep. Driven purely by incoming data rate from server.
    Server sends 256 frames per callback at 48kHz (~5.3ms intervals).
    We accumulate ~1455 input frames (~5.7 packets), then emit one Report 0x34.
    """
    if not is_bt:
        print("  [HAPTIC] Skipping - USB, haptics via USB audio")
        return

    template = _load_haptic_template()
    print(f"  [HAPTIC] Report 0x34: accumulate {_R34_INPUT_FRAMES_NEEDED} frames → "
          f"{_R34_TARGET_FRAMES} frames/pkt, ratio 1:{_R34_DOWNSAMPLE_RATIO:.1f}")

    seq = 0x80
    ts = 0x80D240
    sample_buf = bytearray()
    haptic_count = 0
    bytes_needed = _R34_INPUT_FRAMES_NEEDED * 4  # 4 bytes per s16 stereo frame

    while True:
        try:
            data, addr = haptic_queue.get()
            if len(data) < 4:
                continue

            # Accumulate s16 stereo samples (skip 2-byte header)
            sample_buf.extend(data[2:])

            # Emit Report 0x34 every time we have enough
            while len(sample_buf) >= bytes_needed:
                # Downsample + s16→s8 (same as test_report34.py --wav)
                audio_s8 = bytearray(_R34_AUDIO_LEN)
                for i in range(_R34_TARGET_FRAMES):
                    src_off = int(i * _R34_DOWNSAMPLE_RATIO) * 4
                    l = int.from_bytes(sample_buf[src_off:src_off+2], 'little', signed=True)
                    r = int.from_bytes(sample_buf[src_off+2:src_off+4], 'little', signed=True)
                    audio_s8[i*2] = (l >> 8) & 0xFF
                    audio_s8[i*2+1] = (r >> 8) & 0xFF

                del sample_buf[:bytes_needed]

                # Build report: header from template, audio from stream,
                # control block zeroed (let game's 0x31 reports handle LED/triggers)
                buf = bytearray(_R34_SIZE)
                buf[0:13] = template[0:13]  # only copy header
                buf[1] = seq & 0xFF
                tw = ts & 0xFFFFFF
                buf[10] = (tw >> 16) & 0xFF
                buf[11] = (tw >> 8) & 0xFF
                buf[12] = tw & 0xFF
                buf[_R34_AUDIO_START:_R34_AUDIO_END] = audio_s8
                crc = ds5_bt_crc32(bytes(buf[:_R34_CRC_OFFSET]))
                struct.pack_into('<I', buf, _R34_CRC_OFFSET, crc)

                dev.write(bytes(buf))
                seq = (seq + 0x20) & 0xFF
                ts += 0x20000
                haptic_count += 1

                if haptic_count % 100 == 0:
                    print(f"  [HAPTIC] {haptic_count} pkts, buf={len(sample_buf)}B",
                          flush=True)

            # Prevent buffer bloat (max 2 packets worth)
            if len(sample_buf) > bytes_needed * 2:
                del sample_buf[:len(sample_buf) - bytes_needed]

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
