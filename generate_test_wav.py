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


def square_wave_section(amplitude, freq=200, n=SAMPLES_PER_SECTION):
    """Square wave at given amplitude and frequency."""
    samples = []
    half_period = SAMPLE_RATE // (2 * freq)
    for i in range(n):
        if (i // half_period) % 2 == 0:
            samples.append(amplitude)
        else:
            samples.append(-amplitude)
    return samples


def silent_section(n=SAMPLES_PER_SECTION):
    return [0] * n


def main():
    # s16 amplitudes
    MAX_AMP = 32767
    MID_AMP = 16384

    print("Generating test WAVs (200Hz square waves)...\n")

    # 1. Stereo: loud, medium, silent, repeat
    print("1. test_pattern_stereo.wav (both channels)")
    print("   Pattern: 1s LOUD | 1s MEDIUM | 1s SILENT | repeat")
    pattern = (
        square_wave_section(MAX_AMP) +
        square_wave_section(MID_AMP) +
        silent_section() +
        square_wave_section(MAX_AMP) +
        square_wave_section(MID_AMP) +
        silent_section()
    )
    write_wav("test_pattern_stereo.wav", pattern, pattern)

    # 2. Left only
    print("\n2. test_pattern_left.wav (left channel only)")
    print("   Pattern: L=LOUD|MED|SILENT, R=silent")
    left = (
        square_wave_section(MAX_AMP) +
        square_wave_section(MID_AMP) +
        silent_section()
    )
    silent = silent_section(len(left))
    write_wav("test_pattern_left.wav", left, silent)

    # 3. Right only
    print("\n3. test_pattern_right.wav (right channel only)")
    print("   Pattern: L=silent, R=LOUD|MED|SILENT")
    right = (
        square_wave_section(MAX_AMP) +
        square_wave_section(MID_AMP) +
        silent_section()
    )
    write_wav("test_pattern_right.wav", silent, right)

    print("\nDone! Play these through DSX while capturing with frida-trace.")


if __name__ == "__main__":
    main()
