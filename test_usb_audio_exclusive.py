"""Test: write sine wave to DS5 USB speaker - shared vs exclusive mode."""
import sys
import time
import numpy as np
import sounddevice as sd

# Find DS5
devices = sd.query_devices()
ds5_idx = None
for i, d in enumerate(devices):
    if ('DualSense' in d['name'] or 'Wireless Controller' in d['name']) and d['max_output_channels'] >= 2:
        ds5_idx = i
        ds5_ch = d['max_output_channels']
        print(f"DS5: [{i}] {d['name']} ch={ds5_ch} sr={d['default_samplerate']}")
        break

if ds5_idx is None:
    print("No DS5 found")
    sys.exit(1)

duration = 2.0
rate = 48000
freq = 200
n_samples = int(duration * rate)
channels = min(ds5_ch, 4)

# Sine on ch3+4
data_f32 = np.zeros((n_samples, channels), dtype=np.float32)
data_s16 = np.zeros((n_samples, channels), dtype=np.int16)
t = np.arange(n_samples) / rate
sine_f32 = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
sine_s16 = (16000 * np.sin(2 * np.pi * freq * t)).astype(np.int16)

if channels >= 4:
    data_f32[:, 2] = sine_f32
    data_f32[:, 3] = sine_f32
    data_s16[:, 2] = sine_s16
    data_s16[:, 3] = sine_s16
else:
    data_f32[:, 0] = sine_f32
    data_f32[:, 1] = sine_f32
    data_s16[:, 0] = sine_s16
    data_s16[:, 1] = sine_s16

print(f"\n=== TEST 1: Shared, float32 ===")
try:
    sd.play(data_f32, samplerate=rate, device=ds5_idx, blocking=True)
    print("  Done")
except Exception as e:
    print(f"  Error: {e}")
time.sleep(1)

print(f"\n=== TEST 2: Shared, int16 ===")
try:
    sd.play(data_s16, samplerate=rate, device=ds5_idx, blocking=True)
    print("  Done")
except Exception as e:
    print(f"  Error: {e}")
time.sleep(1)

# Exclusive attempts with different configs
for dtype, data, label in [
    ('int16', data_s16, 'Exclusive int16'),
    ('float32', data_f32, 'Exclusive float32'),
]:
    print(f"\n=== TEST: {label} ===")
    try:
        sd.play(data, samplerate=rate, device=ds5_idx, blocking=True,
                extra_settings=sd.WasapiSettings(exclusive=True))
        print("  Done")
    except Exception as e:
        print(f"  Error: {e}")
    time.sleep(0.5)

# Exclusive with 2ch only
print(f"\n=== TEST: Exclusive int16, 2ch stereo ===")
try:
    data_2ch = np.zeros((n_samples, 2), dtype=np.int16)
    data_2ch[:, 0] = sine_s16
    data_2ch[:, 1] = sine_s16
    sd.play(data_2ch, samplerate=rate, device=ds5_idx, blocking=True,
            extra_settings=sd.WasapiSettings(exclusive=True))
    print("  Done")
except Exception as e:
    print(f"  Error: {e}")

print("\nWhich tests produced vibration?")
