#!/usr/bin/env python3
"""
Test Report 0x34 haptic audio on DualSense via BT.
Uses captured DSX reports as template, only replaces audio region.

Usage:
    python test_report34.py --replay
    python test_report34.py --sine --freq 200 --amp 5
    python test_report34.py --wav haptics_test.wav
"""

import hid
import time
import struct
import math
import wave
import argparse
import sys
import binascii

DS5_VID = 0x054C
DS5_PID = 0x0CE6
DS5_EDGE_PID = 0x0DF2
REPORT_SIZE = 547
AUDIO_START = 13
AUDIO_END = 139  # exclusive
AUDIO_LEN = AUDIO_END - AUDIO_START  # 126 bytes
CRC_OFFSET = 266


def calc_crc(data):
    """CRC32 with 0xA2 seed byte prepended."""
    return binascii.crc32(bytes([0xA2]) + data) & 0xFFFFFFFF


def find_ds5():
    for dev in hid.enumerate(DS5_VID, DS5_PID) + hid.enumerate(DS5_VID, DS5_EDGE_PID):
        if dev.get("usage_page") == 1 or dev.get("interface_number", -1) == -1:
            return dev
    return None


def load_template():
    """Load captured reports as template."""
    try:
        with open("dsx_report34_capture.bin", "rb") as f:
            data = f.read()
        n = len(data) // REPORT_SIZE
        if n == 0:
            raise ValueError("Empty capture file")
        print(f"Loaded {n} captured template reports")
        return data
    except FileNotFoundError:
        print("ERROR: dsx_report34_capture.bin not found!")
        print("Run the DSX capture first (frida-trace).")
        sys.exit(1)


def get_template_report(template_data, index):
    """Get a template report by index (wraps around)."""
    n = len(template_data) // REPORT_SIZE
    i = index % n
    return bytearray(template_data[i * REPORT_SIZE:(i + 1) * REPORT_SIZE])


def inject_audio(template_report, audio_bytes):
    """Replace audio region in template, recalculate CRC."""
    buf = bytearray(template_report)
    # Clear entire audio region first (remove template's original audio)
    buf[AUDIO_START:AUDIO_END] = b'\x00' * AUDIO_LEN
    # Write new audio
    audio_len = min(len(audio_bytes), AUDIO_LEN)
    buf[AUDIO_START:AUDIO_START + audio_len] = audio_bytes[:audio_len]
    # Recalculate CRC
    crc = calc_crc(bytes(buf[:CRC_OFFSET]))
    struct.pack_into('<I', buf, CRC_OFFSET, crc)
    return bytes(buf)


def open_ds5():
    dev_info = find_ds5()
    if not dev_info:
        print("DualSense not found!")
        sys.exit(1)
    print(f"Found: {dev_info['product_string']}")
    d = hid.device()
    d.open_path(dev_info["path"])
    return d


def replay_captured(dev, template_data):
    """Replay captured DSX reports exactly as-is."""
    n = len(template_data) // REPORT_SIZE
    print(f"Replaying {n} captured reports...")
    for i in range(n):
        dev.write(template_data[i * REPORT_SIZE:(i + 1) * REPORT_SIZE])
        time.sleep(0.030)
    print("Done!")


def send_sine(dev, template_data, freq=200, duration=3.0, amplitude=5):
    """Send sine wave using captured reports as template."""
    interval = 0.030
    num_packets = int(duration / interval)
    frames_per_packet = AUDIO_LEN // 2  # stereo interleaved
    sample_rate = frames_per_packet / interval  # effective sample rate

    print(f"Sending {freq}Hz sine (amp={amplitude}) for {duration}s "
          f"({num_packets} packets, ~{sample_rate:.0f} Hz effective rate)...")

    sample_idx = 0
    for pkt in range(num_packets):
        template = get_template_report(template_data, pkt)

        audio = bytearray(AUDIO_LEN)
        for s in range(0, AUDIO_LEN, 2):
            t = sample_idx / sample_rate
            val = int(amplitude * math.sin(2 * math.pi * freq * t))
            val = max(-128, min(127, val))
            audio[s] = val & 0xFF
            audio[s + 1] = val & 0xFF
            sample_idx += 1

        dev.write(inject_audio(template, audio))
        time.sleep(interval)

    print("Done!")


def send_wav(dev, template_data, wav_path):
    """Send WAV file using captured reports as template."""
    with wave.open(wav_path, 'rb') as wf:
        nchannels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)

    print(f"WAV: {nchannels}ch, {sampwidth * 8}bit, {framerate}Hz, {nframes} frames")

    # Convert to signed int8
    if sampwidth == 2:
        pcm16 = struct.unpack(f'<{nframes * nchannels}h', raw)
        samples = [(s >> 8) & 0xFF for s in pcm16]
    elif sampwidth == 1:
        samples = [(b - 128) & 0xFF for b in raw]
    else:
        print(f"Unsupported sample width: {sampwidth}")
        sys.exit(1)

    # Mono → stereo
    if nchannels == 1:
        stereo = []
        for s in samples:
            stereo.append(s)
            stereo.append(s)
        samples = stereo

    # Resample to match packet rate
    frames_per_packet = AUDIO_LEN // 2
    interval = 0.030
    target_frame_rate = frames_per_packet / interval
    ratio = framerate / target_frame_rate

    if abs(ratio - 1.0) > 0.01:
        print(f"Resampling from {framerate}Hz to {target_frame_rate:.0f}Hz...")
        resampled = []
        i = 0.0
        while int(i) * 2 + 1 < len(samples):
            idx = int(i) * 2
            resampled.append(samples[idx])
            resampled.append(samples[idx + 1])
            i += ratio
        samples = resampled
        print(f"Resampled to {len(samples) // 2} frames")

    num_packets = len(samples) // AUDIO_LEN
    duration = num_packets * interval
    print(f"Sending {num_packets} packets (~{duration:.1f}s)...")

    for pkt in range(num_packets):
        template = get_template_report(template_data, pkt)
        offset = pkt * AUDIO_LEN
        audio = bytes(samples[offset:offset + AUDIO_LEN])
        dev.write(inject_audio(template, audio))
        time.sleep(interval)

    print("Done!")


def main():
    parser = argparse.ArgumentParser(description="DS5 Report 0x34 Haptics")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--replay", action="store_true", help="Replay captured DSX reports")
    group.add_argument("--sine", action="store_true", help="Send sine wave")
    group.add_argument("--wav", type=str, help="Send WAV file")
    parser.add_argument("--freq", type=int, default=200, help="Sine frequency (Hz)")
    parser.add_argument("--duration", type=float, default=3.0, help="Duration (sec)")
    parser.add_argument("--amp", type=int, default=5, help="Sine amplitude (1-127)")
    args = parser.parse_args()

    template_data = load_template()
    dev = open_ds5()

    try:
        if args.replay:
            replay_captured(dev, template_data)
        elif args.sine:
            send_sine(dev, template_data, freq=args.freq,
                      duration=args.duration, amplitude=args.amp)
        elif args.wav:
            send_wav(dev, template_data, args.wav)
    finally:
        dev.close()


if __name__ == "__main__":
    main()
