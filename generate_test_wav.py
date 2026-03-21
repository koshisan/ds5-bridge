#!/usr/bin/env python3
"""
Generate test WAV files with known patterns for DSX capture analysis.

Generates:
1. test_pattern_stereo.wav — Both channels: 1s max, 1s mid, 1s zero, repeat x2
2. test_pattern_left.wav  — Left only:  1s max, 1s mid, 1s zero (right silent)
3. test_pattern_right.wav — Right only: 1s max, 1s mid, 1s zero (left silent)

Format: 48kHz, 16-bit, stereo (matching Genshin haptics format)
"""

import struct
import wave

SAMPLE_RATE = 48000
DURATION_PER_SECTION = 1  # seconds
SAMPLES_PER_SECTION = SAMPLE_RATE * DURATION_PER_SECTION


def write_wav(filename, left_samples, right_samples):
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        data = bytearray()
        for l, r in zip(left_samples, right_samples):
            data += struct.pack('<hh', l, r)
        wf.writeframes(bytes(data))
    print(f"  Written: {filename} ({len(left_samples)} frames, {len(left_samples)/SAMPLE_RATE:.1f}s)")


def constant_section(value, n=SAMPLES_PER_SECTION):
    """Constant DC value — motor holds position."""
    return [value] * n


def main():
    # s16 values: DC levels for haptic motor position
    MAX_VAL = 32767   # should appear as 0x7f in s8
    MID_VAL = 16384   # should appear as 0x40 in s8
    ZERO_VAL = 0      # should appear as 0x00 in s8

    print("Generating test WAVs (DC levels for haptic analysis)...\n")

    # 1. Stereo: max, mid, zero, repeat
    print("1. test_pattern_stereo.wav (both channels)")
    print("   Pattern: 1s MAX(7f) | 1s MID(40) | 1s ZERO(00) | repeat")
    pattern = (
        constant_section(MAX_VAL) +
        constant_section(MID_VAL) +
        constant_section(ZERO_VAL) +
        constant_section(MAX_VAL) +
        constant_section(MID_VAL) +
        constant_section(ZERO_VAL)
    )
    write_wav("test_pattern_stereo.wav", pattern, pattern)

    # 2. Left only
    print("\n2. test_pattern_left.wav (left channel only)")
    print("   Pattern: L=MAX|MID|ZERO, R=silent")
    left = (
        constant_section(MAX_VAL) +
        constant_section(MID_VAL) +
        constant_section(ZERO_VAL)
    )
    silent = constant_section(ZERO_VAL, len(left))
    write_wav("test_pattern_left.wav", left, silent)

    # 3. Right only
    print("\n3. test_pattern_right.wav (right channel only)")
    print("   Pattern: L=silent, R=MAX|MID|ZERO")
    right = (
        constant_section(MAX_VAL) +
        constant_section(MID_VAL) +
        constant_section(ZERO_VAL)
    )
    write_wav("test_pattern_right.wav", silent, right)

    print("\nDone! Play these through DSX while capturing with frida-trace.")


if __name__ == "__main__":
    main()
