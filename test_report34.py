#!/usr/bin/env python3
"""
Test Report 0x34 haptic audio on DualSense via BT.

Mode 1 (--replay):  Replay captured DSX reports (verifies format)
Mode 2 (--sine):    Send sine wave via Report 0x34
Mode 3 (--wav):     Send WAV file via Report 0x34

Usage:
    python test_report34.py --replay
    python test_report34.py --sine --freq 200
    python test_report34.py --wav haptics_test.wav
"""

import hid
import time
import struct
import math
import wave
import argparse
import sys

DS5_VID = 0x054C
DS5_PID = 0x0CE6
DS5_EDGE_PID = 0x0DF2

REPORT_SIZE = 547  # Full report including report ID


def find_ds5():
    """Find DualSense controller."""
    for dev in hid.enumerate(DS5_VID, DS5_PID) + hid.enumerate(DS5_VID, DS5_EDGE_PID):
        # BT device
        if dev.get("interface_number", -1) == -1 or dev.get("usage_page") == 1:
            return dev
    return None


def open_ds5():
    """Open DualSense HID device."""
    dev_info = find_ds5()
    if not dev_info:
        print("DualSense not found!")
        sys.exit(1)

    print(f"Found: {dev_info['product_string']} ({dev_info['path']})")
    d = hid.device()
    d.open_path(dev_info["path"])
    return d


def build_report_34(seq, audio_bytes, control_template=None):
    """
    Build a 547-byte Report 0x34.
    
    Layout:
        Byte 0:       0x34 (Report ID)
        Byte 1:       Sequence counter
        Byte 2-4:     91 07 fe (flags, constant)
        Byte 5-9:     30 30 30 30 30 ("00000")
        Byte 10-12:   Timestamp (we increment by 0x20000 each packet)
        Byte 13-138:  Audio PCM (126 bytes, signed int8, stereo interleaved)
        Byte 139-546: Control data (same as 0x32 payload)
    """
    buf = bytearray(REPORT_SIZE)
    buf[0] = 0x34
    buf[1] = seq & 0xFF
    buf[2] = 0x91
    buf[3] = 0x07
    buf[4] = 0xFE
    # "00000"
    buf[5:10] = b'\x30\x30\x30\x30\x30'
    # Timestamp — will be set by caller
    # buf[10:13] set externally

    # Audio data (126 bytes max)
    audio_len = min(len(audio_bytes), 126)
    buf[13:13 + audio_len] = audio_bytes[:audio_len]

    # Embed control data at offset 139
    if control_template:
        # Copy control portion (skip report ID byte of 0x32)
        ctrl_data = control_template[1:]  # everything after 0x32 report ID
        ctrl_len = min(len(ctrl_data), REPORT_SIZE - 139)
        buf[139:139 + ctrl_len] = ctrl_data[:ctrl_len]

    return bytes(buf)


def build_report_32(seq, control_template=None):
    """Build a 547-byte Report 0x32 (control only, no audio)."""
    if control_template:
        buf = bytearray(control_template)
        buf[0] = 0x32
        buf[1] = seq & 0xFF
        return bytes(buf)
    else:
        buf = bytearray(REPORT_SIZE)
        buf[0] = 0x32
        buf[1] = seq & 0xFF
        buf[2] = 0x90
        buf[3] = 0x3F
        buf[4] = 0xFD
        buf[5] = 0xF7
        buf[8] = 0x7E
        buf[9] = 0x7F
        buf[10] = 0xFF
        buf[11] = 0x09
        buf[13] = 0x0F
        return bytes(buf)


def replay_captured(dev):
    """Replay captured DSX reports."""
    try:
        with open("dsx_report34_capture.bin", "rb") as f:
            data = f.read()
    except FileNotFoundError:
        print("dsx_report34_capture.bin not found! Run the capture first.")
        sys.exit(1)

    num_reports = len(data) // REPORT_SIZE
    print(f"Replaying {num_reports} captured Report 0x34 packets...")
    print("You should feel vibration if the format is correct!")

    for i in range(num_reports):
        report = data[i * REPORT_SIZE:(i + 1) * REPORT_SIZE]
        dev.write(report)
        time.sleep(0.030)  # ~33 Hz like DSX

    print("Replay done!")


def send_sine(dev, freq=200, duration=3.0, control_template=None):
    """Send sine wave via Report 0x34."""
    sample_rate = 4158  # ~126 samples * 33 packets/sec
    interval = 0.030  # 30ms between packets
    samples_per_packet = 126  # stereo interleaved = 63 frames

    num_packets = int(duration / interval)
    print(f"Sending {freq}Hz sine for {duration}s ({num_packets} packets)...")

    seq = 0x80
    ts = 0x7CD240  # starting timestamp (from capture)
    sample_idx = 0

    for pkt in range(num_packets):
        # Generate stereo interleaved audio
        audio = bytearray(samples_per_packet)
        for s in range(0, samples_per_packet, 2):
            t = sample_idx / sample_rate
            val = int(127 * math.sin(2 * math.pi * freq * t))
            val = max(-128, min(127, val))
            val_u8 = val & 0xFF
            audio[s] = val_u8      # left
            audio[s + 1] = val_u8  # right
            sample_idx += 1

        # Build timestamp
        ts_bytes = ts.to_bytes(3, 'big')

        report = bytearray(build_report_34(seq, audio, control_template))
        ts_wrapped = ts & 0xFFFFFF  # 3 bytes max
        report[10:13] = ts_wrapped.to_bytes(3, 'big')

        dev.write(bytes(report))

        seq = (seq + 0x20) & 0xFF
        ts += 0x20000
        time.sleep(interval)

    # Send a few silent 0x32 to cleanly stop
    for _ in range(5):
        dev.write(build_report_32(seq, control_template))
        seq = (seq + 0x10) & 0xFF
        time.sleep(0.030)

    print("Done!")


def send_wav(dev, wav_path, control_template=None):
    """Send WAV file via Report 0x34."""
    with wave.open(wav_path, 'rb') as wf:
        nchannels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)

    print(f"WAV: {nchannels}ch, {sampwidth * 8}bit, {framerate}Hz, {nframes} frames")

    # Convert to signed int8 stereo interleaved
    samples = []
    if sampwidth == 2:
        # 16-bit signed → 8-bit signed
        fmt = f"<{nframes * nchannels}h"
        pcm16 = struct.unpack(fmt, raw)
        for s in pcm16:
            samples.append((s >> 8) & 0xFF)  # Take high byte
    elif sampwidth == 1:
        # 8-bit unsigned → 8-bit signed
        for b in raw:
            samples.append((b - 128) & 0xFF)
    else:
        print(f"Unsupported sample width: {sampwidth}")
        sys.exit(1)

    # If mono, duplicate to stereo
    if nchannels == 1:
        stereo = []
        for s in samples:
            stereo.append(s)
            stereo.append(s)
        samples = stereo

    # Resample to match DS5 rate if needed
    # DSX sends 126 bytes per packet at ~33Hz = 4158 bytes/sec (2079 frames/sec stereo)
    target_rate = 2079  # frames per second
    if framerate != target_rate:
        print(f"Resampling from {framerate}Hz to {target_rate}Hz...")
        ratio = framerate / target_rate
        resampled = []
        i = 0.0
        while int(i) * 2 + 1 < len(samples):
            idx = int(i) * 2
            resampled.append(samples[idx])      # left
            resampled.append(samples[idx + 1])  # right
            i += ratio
        samples = resampled
        print(f"Resampled to {len(samples) // 2} frames")

    samples_per_packet = 126
    num_packets = len(samples) // samples_per_packet
    duration = num_packets * 0.030
    print(f"Sending {num_packets} packets (~{duration:.1f}s)...")

    seq = 0x80
    ts = 0x7CD240

    for pkt in range(num_packets):
        offset = pkt * samples_per_packet
        audio = bytes(samples[offset:offset + samples_per_packet])

        report = bytearray(build_report_34(seq, audio, control_template))
        ts_wrapped = ts & 0xFFFFFF  # 3 bytes max
        report[10:13] = ts_wrapped.to_bytes(3, 'big')

        dev.write(bytes(report))

        seq = (seq + 0x20) & 0xFF
        ts += 0x20000
        time.sleep(0.030)

    # Clean stop
    for _ in range(5):
        dev.write(build_report_32(seq, control_template))
        seq = (seq + 0x10) & 0xFF
        time.sleep(0.030)

    print("Done!")


def main():
    parser = argparse.ArgumentParser(description="Test DS5 Report 0x34 Haptics")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--replay", action="store_true", help="Replay captured DSX reports")
    group.add_argument("--sine", action="store_true", help="Send sine wave")
    group.add_argument("--wav", type=str, help="Send WAV file")
    parser.add_argument("--freq", type=int, default=200, help="Sine frequency (Hz)")
    parser.add_argument("--duration", type=float, default=3.0, help="Sine duration (sec)")
    args = parser.parse_args()

    dev = open_ds5()

    # Load control template if available
    control_template = None
    try:
        with open("dsx_report32_template.bin", "rb") as f:
            control_template = list(f.read())
        print("Loaded control template from DSX capture")
    except FileNotFoundError:
        print("No control template, using default")

    try:
        if args.replay:
            replay_captured(dev)
        elif args.sine:
            send_sine(dev, freq=args.freq, duration=args.duration,
                      control_template=control_template)
        elif args.wav:
            send_wav(dev, args.wav, control_template=control_template)
    finally:
        dev.close()


if __name__ == "__main__":
    main()
