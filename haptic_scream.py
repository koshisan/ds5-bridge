#!/usr/bin/env python3
"""DS5 BT Haptic Bridge via Scream virtual audio - receives Scream UDP, converts to DS5 haptics"""

import hid
import socket
import struct
import zlib
import time
import sys
import numpy as np

VENDOR_ID = 0x054C
PRODUCT_ID = 0x0CE6
REPORT_ID = 0x32
REPORT_SIZE = 141
SAMPLE_SIZE = 64  # 32 stereo samples
HAPTIC_RATE = 3000
HAPTIC_INTERVAL = SAMPLE_SIZE / (HAPTIC_RATE * 2)

SCREAM_PORT = 4010
SCREAM_MULTICAST = "239.255.77.77"
SCREAM_HEADER = 5
SCREAM_PAYLOAD = 1152

def crc32_ds5(data):
    return zlib.crc32(bytes([0xA2]) + data) & 0xFFFFFFFF

def build_haptic_report(sample_data, seq):
    payload_size = REPORT_SIZE - 1 - 4

    pkt_0x11 = bytes([
        (0x11 & 0x3F) | (1 << 7),
        7,
        0b11111110, 0, 0, 0, 0, seq & 0xFF, 0
    ])

    pkt_0x12_header = bytes([
        (0x12 & 0x3F) | (1 << 7),
        SAMPLE_SIZE,
    ])

    packets = pkt_0x11 + pkt_0x12_header + sample_data
    payload = packets.ljust(payload_size, b'\x00')

    tag_seq = (seq & 0x0F) << 4
    report_body = bytes([tag_seq]) + payload

    crc_data = bytes([REPORT_ID]) + report_body
    crc = crc32_ds5(crc_data)

    return bytes([REPORT_ID]) + report_body + struct.pack('<I', crc)

def pcm_to_haptic(pcm_data, bits_per_sample, channels, samples_per_chunk):
    """Convert Scream PCM chunk to 64 haptic bytes (32 stereo samples, 8-bit)"""
    bps = bits_per_sample // 8
    frame_size = bps * channels

    # Parse PCM to float
    n_frames = len(pcm_data) // frame_size
    if bits_per_sample == 16:
        dtype = np.int16
        scale = 32768.0
    elif bits_per_sample == 32:
        dtype = np.int32
        scale = 2147483648.0
    else:
        return bytes(SAMPLE_SIZE)

    raw = np.frombuffer(pcm_data[:n_frames * frame_size], dtype=dtype).reshape(-1, channels)
    floats = raw.astype(np.float32) / scale

    # Take first 2 channels (or mono)
    left = floats[:, 0] if channels >= 1 else np.zeros(n_frames)
    right = floats[:, 1] if channels >= 2 else left

    # Downsample to 32 samples using RMS of chunks
    factor = max(1, n_frames // 32)
    left_ds = np.zeros(32, dtype=np.float32)
    right_ds = np.zeros(32, dtype=np.float32)

    for i in range(32):
        start = i * factor
        end = min(start + factor, n_frames)
        if start < n_frames:
            left_ds[i] = np.sqrt(np.mean(left[start:end] ** 2))
            right_ds[i] = np.sqrt(np.mean(right[start:end] ** 2))

    # Scale to uint8 with gain
    gain = 12.0
    left_u8 = np.clip(left_ds * gain * 255, 0, 255).astype(np.uint8)
    right_u8 = np.clip(right_ds * gain * 255, 0, 255).astype(np.uint8)

    # Interleave
    result = np.empty(64, dtype=np.uint8)
    result[0::2] = left_u8
    result[1::2] = right_u8

    return bytes(result)

def main():
    gain = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
    listen_addr = sys.argv[2] if len(sys.argv) > 2 else "0.0.0.0"

    print(f"DS5 Haptic Bridge (Scream Receiver)")
    print(f"Listening on UDP {listen_addr}:{SCREAM_PORT} (multicast {SCREAM_MULTICAST})")
    print(f"Gain: {gain}x")

    # Find DS5
    devs = hid.enumerate(VENDOR_ID, PRODUCT_ID)
    bt_dev = None
    for d in devs:
        if d.get('usage_page') == 1 and d.get('usage') == 5:
            bt_dev = d
            break
    if not bt_dev and devs:
        bt_dev = devs[0]
    if not bt_dev:
        print("No DS5 found!")
        return

    print(f"DS5: {bt_dev.get('product_string', 'DualSense')}")
    dev = hid.device()
    dev.open_path(bt_dev['path'])

    # Setup UDP receiver
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((listen_addr, SCREAM_PORT))

    # Join multicast
    import struct as st
    mreq = st.pack("4s4s", socket.inet_aton(SCREAM_MULTICAST), socket.inet_aton("0.0.0.0"))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    # Also accept unicast
    sock.settimeout(0.05)

    seq = 0
    packets = 0
    last_print = time.time()
    silence_count = 0

    print("Waiting for Scream audio...")

    try:
        while True:
            try:
                data, addr = sock.recvfrom(SCREAM_HEADER + SCREAM_PAYLOAD + 100)
            except socket.timeout:
                # Send silence to keep connection alive
                silence_count += 1
                if silence_count > 20:  # ~1 second of silence
                    continue
                report = build_haptic_report(bytes(SAMPLE_SIZE), seq)
                dev.write(report)
                seq = (seq + 1) & 0x0F
                continue

            silence_count = 0

            if len(data) < SCREAM_HEADER + 4:
                continue

            # Parse Scream header
            rate_marker = data[0]
            bps = data[1]
            channels = data[2]
            # channel_mask = data[3] | (data[4] << 8)

            if rate_marker < 128:
                sample_rate = 48000 * (rate_marker + 1)
            else:
                sample_rate = 44100 * (rate_marker - 127)

            pcm_data = data[SCREAM_HEADER:]

            # Convert to haptic samples
            haptic = pcm_to_haptic(pcm_data, bps, channels, sample_rate)

            # Send to DS5
            report = build_haptic_report(haptic, seq)
            dev.write(report)

            seq = (seq + 1) & 0x0F
            packets += 1

            now = time.time()
            if now - last_print > 2.0:
                print(f"  {packets} pkts | {sample_rate}Hz/{bps}bit/{channels}ch from {addr[0]}", end='\r')
                last_print = now

    except KeyboardInterrupt:
        print(f"\nStopped after {packets} packets")
        silence = bytes(SAMPLE_SIZE)
        for _ in range(10):
            dev.write(build_haptic_report(silence, seq))
            seq = (seq + 1) & 0x0F
            time.sleep(HAPTIC_INTERVAL)
    finally:
        dev.close()
        sock.close()

if __name__ == '__main__':
    main()
