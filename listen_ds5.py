import pyaudiowpatch as pyaudio
import numpy as np
import time

p = pyaudio.PyAudio()

# Find DualSense WASAPI loopback device (speaker, not mic)
ds5_dev = None
for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)
    name = info['name']
    if ('DualSense' in name or 'Wireless Controller' in name) and info.get('isLoopbackDevice') and info['maxInputChannels'] > 0 and 'Mikrofon' not in name and 'Microphone' not in name:
        ds5_dev = info
        break

if ds5_dev is None:
    print("DualSense speaker loopback not found!")
    print("Available loopback devices:")
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info.get('isLoopbackDevice'):
            print(f"  [{i}] {info['name']} ch={info['maxInputChannels']}")
    p.terminate()
    exit(1)

print(f"Listening on: {ds5_dev['name']} (index {ds5_dev['index']})")
print(f"  Channels: {ds5_dev['maxInputChannels']}, Rate: {int(ds5_dev['defaultSampleRate'])}")
print("Start Genshin now! Press Ctrl+C to stop.\n")

channels = ds5_dev['maxInputChannels']
rate = int(ds5_dev['defaultSampleRate'])

def callback(in_data, frame_count, time_info, status):
    data = np.frombuffer(in_data, dtype=np.float32)
    peak = np.max(np.abs(data))
    if peak > 0.001:
        bars = int(min(peak, 1.0) * 50)
        print(f"\rAudio! Peak: {peak:.4f} {'#' * bars}    ", end="", flush=True)
    return (None, pyaudio.paContinue)

stream = p.open(
    format=pyaudio.paFloat32,
    channels=channels,
    rate=rate,
    input=True,
    input_device_index=ds5_dev['index'],
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
