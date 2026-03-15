"""Test: write a sine wave to DS5 USB speaker in both shared and exclusive mode."""
import sys
import time
import struct
import math

# Test 1: list DS5 audio devices
try:
    import sounddevice as sd
    print("=== sounddevice devices ===")
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if 'DualSense' in d['name'] or 'Wireless Controller' in d['name']:
            print(f"  [{i}] {d['name']} out={d['max_output_channels']} in={d['max_input_channels']} sr={d['default_samplerate']}")
    print()
except ImportError:
    print("pip install sounddevice")
    sys.exit(1)

# Find DS5 output
ds5_idx = None
ds5_ch = 0
for i, d in enumerate(devices):
    if ('DualSense' in d['name'] or 'Wireless Controller' in d['name']) and d['max_output_channels'] >= 2:
        ds5_idx = i
        ds5_ch = d['max_output_channels']
        break

if ds5_idx is None:
    print("No DS5 USB speaker found")
    sys.exit(1)

print(f"Using device [{ds5_idx}] channels={ds5_ch}")

# Generate 2 seconds of 200Hz sine wave on ch3+4
duration = 2.0
rate = 48000
freq = 200
n_samples = int(duration * rate)
amplitude = 0.5

import numpy as np

# Test shared mode
print("\n=== SHARED MODE (2s, 200Hz sine on ch3+4) ===")
try:
    data = np.zeros((n_samples, min(ds5_ch, 4)), dtype=np.float32)
    t = np.arange(n_samples) / rate
    sine = (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    if ds5_ch >= 4:
        data[:, 2] = sine  # ch3
        data[:, 3] = sine  # ch4
    else:
        data[:, 0] = sine
        data[:, 1] = sine
    sd.play(data, samplerate=rate, device=ds5_idx, blocking=True)
    print("  Done - did you feel it?")
except Exception as e:
    print(f"  Error: {e}")

time.sleep(1)

# Test exclusive mode
print("\n=== EXCLUSIVE MODE (2s, 200Hz sine on ch3+4) ===")
try:
    data = np.zeros((n_samples, min(ds5_ch, 4)), dtype=np.float32)
    t = np.arange(n_samples) / rate
    sine = (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    if ds5_ch >= 4:
        data[:, 2] = sine
        data[:, 3] = sine
    else:
        data[:, 0] = sine
        data[:, 1] = sine
    sd.play(data, samplerate=rate, device=ds5_idx, blocking=True,
            extra_settings=sd.WasapiSettings(exclusive=True))
    print("  Done - did you feel it?")
except Exception as e:
    print(f"  Error: {e}")

print("\nWhich felt stronger?")
