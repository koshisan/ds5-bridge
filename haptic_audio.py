#!/usr/bin/env python3
"""DS5 BT Haptic Audio Bridge - WASAPI loopback capture to DS5 haptics via Report 0x32"""

import hid
import math
import time
import struct
import zlib
import sys
import threading
import numpy as np

try:
    import soundcard as sc
except ImportError:
    print("pip install soundcard numpy")
    sys.exit(1)

VENDOR_ID = 0x054C
PRODUCT_ID = 0x0CE6
REPORT_ID = 0x32
REPORT_SIZE = 141
SAMPLE_SIZE = 64  # 32 stereo samples (L/R interleaved)
SAMPLE_RATE = 3000
INTERVAL = SAMPLE_SIZE / (SAMPLE_RATE * 2)  # ~10.67ms
CAPTURE_RATE = 48000
CAPTURE_CHUNK = int(CAPTURE_RATE * INTERVAL)  # samples per interval at 48kHz

def crc32_ds5(data):
    return zlib.crc32(bytes([0xA2]) + data) & 0xFFFFFFFF

def build_haptic_report(sample_data, seq):
    payload_size = REPORT_SIZE - 1 - 4  # 136 bytes

    pkt_0x11 = bytes([
        (0x11 & 0x3F) | (1 << 7),  # pid=0x11, sized=1
        7,
        0b11111110, 0, 0, 0, 0, seq & 0xFF, 0
    ])

    pkt_0x12_header = bytes([
        (0x12 & 0x3F) | (1 << 7),  # pid=0x12, sized=1
        SAMPLE_SIZE,
    ])

    packets = pkt_0x11 + pkt_0x12_header + sample_data
    payload = packets.ljust(payload_size, b'\x00')

    tag_seq = (seq & 0x0F) << 4
    report_body = bytes([tag_seq]) + payload

    crc_data = bytes([REPORT_ID]) + report_body
    crc = crc32_ds5(crc_data)

    return bytes([REPORT_ID]) + report_body + struct.pack('<I', crc)

def downsample_to_haptic(audio_f32_stereo):
    """Convert 48kHz float32 stereo -> 3kHz uint8 stereo (32 samples = 64 bytes)"""
    # Mix to mono-ish, keep stereo
    if len(audio_f32_stereo.shape) > 1 and audio_f32_stereo.shape[1] >= 2:
        left = audio_f32_stereo[:, 0]
        right = audio_f32_stereo[:, 1]
    else:
        left = right = audio_f32_stereo.flatten()

    # Downsample: 48000 -> 3000 = factor 16
    factor = len(left) // 32
    if factor < 1:
        factor = 1

    left_ds = np.array([np.mean(np.abs(left[i*factor:(i+1)*factor])) for i in range(32)])
    right_ds = np.array([np.mean(np.abs(right[i*factor:(i+1)*factor])) for i in range(32)])

    # Scale to uint8 (0-255), with some amplification
    gain = 8.0
    left_u8 = np.clip(left_ds * gain * 127 + 128, 0, 255).astype(np.uint8)
    right_u8 = np.clip(right_ds * gain * 127 + 128, 0, 255).astype(np.uint8)

    # Interleave L/R
    result = np.empty(64, dtype=np.uint8)
    result[0::2] = left_u8
    result[1::2] = right_u8

    return bytes(result)

def main():
    gain = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0

    print(f"DS5 Haptic Audio Bridge (WASAPI Loopback)")
    print(f"Gain: {gain}x | Ctrl+C to stop")

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

    # Get default speaker for loopback
    default_speaker = sc.default_speaker()
    print(f"Capturing: {default_speaker.name}")

    seq = 0
    packets = 0

    try:
        with default_speaker.player(samplerate=CAPTURE_RATE, channels=2) as player:
            # Use loopback recording from default speaker
            mic = sc.get_microphone(default_speaker.id, include_loopback=True)
            with mic.recorder(samplerate=CAPTURE_RATE, channels=2, blocksize=CAPTURE_CHUNK) as recorder:
                print("Streaming... (play some audio!)")
                while True:
                    start = time.perf_counter()

                    # Capture audio chunk
                    audio = recorder.record(numframes=CAPTURE_CHUNK)

                    # Convert to haptic samples
                    samples = downsample_to_haptic(audio)

                    # Build and send report
                    report = build_haptic_report(samples, seq)
                    dev.write(report)

                    seq = (seq + 1) & 0x0F
                    packets += 1

                    if packets % 100 == 0:
                        print(f"  {packets} packets sent", end='\r')

                    # Timing
                    elapsed = time.perf_counter() - start
                    if elapsed < INTERVAL:
                        time.sleep(INTERVAL - elapsed)

    except KeyboardInterrupt:
        print(f"\nStopped after {packets} packets")
        # Send silence
        silence = bytes(SAMPLE_SIZE)
        for _ in range(10):
            dev.write(build_haptic_report(silence, seq))
            seq = (seq + 1) & 0x0F
            time.sleep(INTERVAL)
    finally:
        dev.close()

if __name__ == '__main__':
    main()
