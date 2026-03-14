"""Listen to DualSense virtual speaker - 2ch WASAPI loopback (downmixed from 4ch)."""
import pyaudiowpatch as pyaudio
import numpy as np
import time

p = pyaudio.PyAudio()

# Find DualSense loopback
ds5_lb = None
for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)
    if ('2- DualSense' in info['name'] or '2-DualSense' in info['name']) and info.get('isLoopbackDevice'):
        ds5_lb = info
        print(f"Found: [{i}] {info['name']} ch={info['maxInputChannels']}")
        break

if not ds5_lb:
    print("DualSense loopback not found!")
    p.terminate()
    exit(1)

channels = int(ds5_lb['maxInputChannels'])
rate = int(ds5_lb['defaultSampleRate'])
print(f"\nLoopback: {channels}ch, {rate}Hz (rear channels downmixed to stereo)")
print("Press Ctrl+C to stop.\n")

def callback(in_data, frame_count, time_info, status):
    data = np.frombuffer(in_data, dtype=np.float32).reshape(-1, channels)
    peaks = [np.max(np.abs(data[:, ch])) for ch in range(channels)]
    if max(peaks) > 0.001:
        bars = ['#' * int(min(p, 1.0) * 40) for p in peaks]
        labels = ['L', 'R'] if channels == 2 else [f'CH{i+1}' for i in range(channels)]
        parts = [f"{labels[i]}:{peaks[i]:.3f} {bars[i]}" for i in range(channels)]
        print(f"\r{'  '.join(parts)}    ", end="", flush=True)
    return (None, pyaudio.paContinue)

stream = p.open(
    format=pyaudio.paFloat32,
    channels=channels,
    rate=rate,
    input=True,
    input_device_index=ds5_lb['index'],
    frames_per_buffer=512,
    stream_callback=callback
)

stream.start_stream()
try:
    while stream.is_active():
        time.sleep(0.1)
except KeyboardInterrupt:
    print("\nStopped.")

stream.stop_stream()
stream.close()
p.terminate()
