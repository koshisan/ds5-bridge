"""Test DS5 haptic motor frequency response."""
import sys
import time
import numpy as np
import sounddevice as sd

devices = sd.query_devices()
ds5_idx = None
for i, d in enumerate(devices):
    if ('DualSense' in d['name'] or 'Wireless Controller' in d['name']) and d['max_output_channels'] >= 2:
        ds5_idx = i
        ds5_ch = min(d['max_output_channels'], 4)
        print(f"DS5: [{i}] {d['name']} ch={ds5_ch}")
        break

if ds5_idx is None:
    print("No DS5 found")
    sys.exit(1)

rate = 48000
duration = 1.5
amplitude = 16000
n = int(duration * rate)
t = np.arange(n) / rate

freqs = [50, 100, 150, 200, 300, 500, 800, 1000, 1500, 2000]

for freq in freqs:
    data = np.zeros((n, ds5_ch), dtype=np.int16)
    sine = (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.int16)
    if ds5_ch >= 4:
        data[:, 2] = sine
        data[:, 3] = sine
    else:
        data[:, 0] = sine
        data[:, 1] = sine
    
    print(f"{freq:5d} Hz ... ", end='', flush=True)
    sd.play(data, samplerate=rate, device=ds5_idx, blocking=True)
    print("done")
    time.sleep(0.5)

print("\nWhich frequencies felt strongest?")
