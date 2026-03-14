import pyaudiowpatch as pyaudio
import numpy as np
import time

p = pyaudio.PyAudio()

# Find DualSense speaker loopback - search by name containing "2-" and "Loopback"
ds5_dev = None
for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)
    name = info['name']
    if ('2- DualSense' in name or '2-DualSense' in name) and 'Loopback' in name:
        ds5_dev = info
        print(f"Found loopback: [{i}] {name} ch={info['maxInputChannels']}")
        break

# Fallback: find the output device and get its loopback
if ds5_dev is None:
    print("No loopback found directly, trying to get loopback from output device...")
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        name = info['name']
        if ('2- DualSense' in name or '2-DualSense' in name) and info['maxOutputChannels'] > 0 and not info.get('isLoopbackDevice'):
            print(f"Found output device: [{i}] {name} ch_out={info['maxOutputChannels']}")
            try:
                loopback = p.get_loopback_device_info_generator()
                for lb in loopback:
                    if '2- DualSense' in lb['name'] or '2-DualSense' in lb['name']:
                        ds5_dev = lb
                        print(f"Found loopback via generator: {lb['name']} ch={lb['maxInputChannels']}")
                        break
            except Exception as e:
                print(f"Loopback generator failed: {e}")
            break

if ds5_dev is None:
    print("\nCould not find DualSense loopback device.")
    print("All loopback devices:")
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info.get('isLoopbackDevice'):
            print(f"  [{i}] {info['name']} ch={info['maxInputChannels']}")
    p.terminate()
    exit(1)

channels = ds5_dev['maxInputChannels']
rate = int(ds5_dev['defaultSampleRate'])
print(f"\nListening on: {ds5_dev['name']} (index {ds5_dev['index']})")
print(f"  Channels: {channels}, Rate: {rate}")
print("Start Genshin now! Press Ctrl+C to stop.\n")

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
