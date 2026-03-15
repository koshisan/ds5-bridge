"""Play a stereo WAV file on DS5 USB speaker channels 3+4 (haptic)."""
import sys
import numpy as np
import sounddevice as sd
import soundfile as sf

if len(sys.argv) < 2:
    print("Usage: python play_haptic_wav.py <file.wav>")
    sys.exit(1)

# Find DS5
devices = sd.query_devices()
ds5_idx = None
for i, d in enumerate(devices):
    if ('DualSense' in d['name'] or 'Wireless Controller' in d['name']) and d['max_output_channels'] >= 2:
        ds5_idx = i
        ds5_ch = d['max_output_channels']
        print(f"DS5: [{i}] {d['name']} ch={ds5_ch}")
        break

if ds5_idx is None:
    print("No DS5 found")
    sys.exit(1)

# Read WAV
data, rate = sf.read(sys.argv[1], dtype='int16')
print(f"WAV: {data.shape}, {rate}Hz")

if data.ndim == 1:
    data = np.column_stack([data, data])

# Build 4ch output: silence on ch1+2, haptic on ch3+4
n = data.shape[0]
channels = min(ds5_ch, 4)
if channels >= 4:
    out = np.zeros((n, 4), dtype=np.int16)
    out[:, 2] = data[:, 0]  # haptic L
    out[:, 3] = data[:, 1]  # haptic R
else:
    out = data

print(f"Playing on ch3+4, {n/rate:.1f}s...")
sd.play(out, samplerate=rate, device=ds5_idx, blocking=True)
print("Done")
