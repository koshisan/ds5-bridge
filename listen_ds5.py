import sounddevice as sd
import numpy as np
import time

# Find DualSense WASAPI device
dev_idx = None
for i, d in enumerate(sd.query_devices()):
    if 'DualSense' in d['name'] and d['max_input_channels'] == 0 and d['hostapi'] == 2:
        dev_idx = i
        break

if dev_idx is None:
    print("DualSense speaker not found!")
    exit(1)

info = sd.query_devices(dev_idx)
print(f"Listening on: {info['name']} (index {dev_idx})")
print("Start Genshin now! Press Ctrl+C to stop.")

def callback(indata, frames, time_info, status):
    peak = np.max(np.abs(indata))
    if peak > 0.001:
        bars = int(peak * 50)
        print(f"\rAudio! Peak: {peak:.4f} {'#' * bars}    ", end="", flush=True)

try:
    with sd.InputStream(device=dev_idx, channels=2, samplerate=48000, callback=callback, dtype='float32'):
        while True:
            time.sleep(0.1)
except KeyboardInterrupt:
    print("\nStopped.")
