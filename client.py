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
            data, addr = sock.recvfrom(256)
            if len(data) < 2:
                continue
            # Debug: log ALL received packets

            # Route audio packets to haptic handler
            if data[0] == 0x40 and haptic_queue is not None:
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



def load_haptic_template():
    """Load captured Report 0x34 template for BT haptics.
    Returns a single 547-byte template report, or None if not available."""
    import os
    template_paths = [
        os.path.join(os.path.dirname(__file__), "dsx_report34_capture.bin"),
        "dsx_report34_capture.bin",
    ]
    for path in template_paths:
        try:
            with open(path, "rb") as f:
                data = f.read(547)
            if len(data) >= 547:
                print(f"  [HAPTIC] Loaded template from {path}")
                return data[:547]
        except FileNotFoundError:
            continue
    return None


def build_default_template():
    """Build a default Report 0x34 template from known constants."""
    buf = bytearray(547)
    buf[0] = 0x34
    buf[1] = 0x80  # initial sequence
    buf[2] = 0x91
    buf[3] = 0x07
    buf[4] = 0xFE
    buf[5:10] = b'\x30\x30\x30\x30\x30'
    buf[10] = 0x80  # timestamp
    buf[11] = 0xD2
    buf[12] = 0x40
    # Control block at offset 139 (from 0x32 standalone format, skip reportID+seq)
    buf[139] = 0x90
    buf[140] = 0x3F
    buf[141] = 0xFD
    buf[142] = 0xF7
    buf[145] = 0x7E
    buf[146] = 0x7F
    buf[147] = 0xFF
    buf[148] = 0x09
    buf[150] = 0x0F
    buf[178] = 0x0A
    buf[179] = 0x07
    buf[182] = 0x02
    buf[184] = 0x05
    return bytes(buf)


# Report 0x34 constants
R34_REPORT_SIZE = 547
R34_AUDIO_START = 13
R34_AUDIO_END = 139
R34_AUDIO_LEN = R34_AUDIO_END - R34_AUDIO_START  # 126 bytes = 63 stereo frames
R34_CRC_OFFSET = 266


def haptic_receiver(haptic_queue, dev, is_bt):
    """Receive raw s16 stereo audio from server, resample to s8, send as Report 0x34.

    Same approach as test_report34.py --wav (which works correctly):
    - Accumulate incoming s16 stereo samples
    - Every time we have enough for one packet: downsample, convert s16→s8, send
    - 63 stereo frames per Report 0x34, sent at the rate data arrives
    """
    seq = 0x80
    ts = 0x80D240
    haptic_count = 0

    if not is_bt:
        print("  [HAPTIC] Skipping - USB connection, haptics via USB audio instead")
        return

    template = load_haptic_template()
    if template is None:
        print("  [HAPTIC] No captured template found, using defaults")
        template = build_default_template()

    # 48kHz s16 stereo → 63 frames per Report 0x34 packet
    # DSX sends at ~33Hz → effective rate = 63 * 33 = ~2079 Hz
    # Ratio: 48000 / 2079 ≈ 23.09 → consume ~23 input frames per output frame
    # Per packet: 63 * 23.09 ≈ 1455 input frames = 5820 bytes of s16 stereo
    SERVER_RATE = 48000
    TARGET_FRAMES = 63  # stereo frames per packet
    TARGET_RATE = 2079.0
    RATIO = SERVER_RATE / TARGET_RATE  # ~23.09
    INPUT_FRAMES_PER_PACKET = int(TARGET_FRAMES * RATIO)  # ~1455
    INPUT_BYTES_PER_PACKET = INPUT_FRAMES_PER_PACKET * 4   # ~5820

    sample_buf = bytearray()

    print(f"  [HAPTIC] Report 0x34: 48kHz s16 → {TARGET_RATE:.0f}Hz s8, ratio 1:{RATIO:.1f}")

    while True:
        try:
            data, addr = haptic_queue.get()
            if len(data) < 4:
                continue

            # Accumulate raw s16 stereo (skip type + seq bytes)
            sample_buf.extend(data[2:])

            # Send a packet every time we have enough input data
            while len(sample_buf) >= INPUT_BYTES_PER_PACKET:
                # Downsample + s16→s8 (same as test_report34.py send_wav)
                audio_s8 = bytearray(R34_AUDIO_LEN)
                for i in range(TARGET_FRAMES):
                    src_frame = int(i * RATIO)
                    src_off = src_frame * 4
                    l_s16 = int.from_bytes(sample_buf[src_off:src_off+2], 'little', signed=True)
                    r_s16 = int.from_bytes(sample_buf[src_off+2:src_off+4], 'little', signed=True)
                    audio_s8[i * 2] = (l_s16 >> 8) & 0xFF
                    audio_s8[i * 2 + 1] = (r_s16 >> 8) & 0xFF

                # Consume input
                del sample_buf[:INPUT_BYTES_PER_PACKET]

                # Build Report 0x34
                buf = bytearray(template)
                buf[1] = seq & 0xFF
                ts_wrapped = ts & 0xFFFFFF
                buf[10] = (ts_wrapped >> 16) & 0xFF
                buf[11] = (ts_wrapped >> 8) & 0xFF
                buf[12] = ts_wrapped & 0xFF
                buf[R34_AUDIO_START:R34_AUDIO_END] = audio_s8

                crc = ds5_bt_crc32(bytes(buf[:R34_CRC_OFFSET]))
                struct.pack_into('<I', buf, R34_CRC_OFFSET, crc)

                dev.write(bytes(buf))

                seq = (seq + 0x20) & 0xFF
                ts += 0x20000
                haptic_count += 1

                if haptic_count % 100 == 0:
                    print(f"  [HAPTIC] {haptic_count} pkts", flush=True)

            # Prevent buffer bloat (max 2 packets worth)
            max_buf = INPUT_BYTES_PER_PACKET * 2
            if len(sample_buf) > max_buf:
                del sample_buf[:len(sample_buf) - max_buf]

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
