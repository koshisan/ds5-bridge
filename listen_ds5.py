import pyaudiowpatch as pyaudio
import numpy as np
import time

p = pyaudio.PyAudio()

# Find DualSense speaker loopback
ds5_dev = None
for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)
    name = info['name']
    if ('2- DualSense' in name or '2-DualSense' in name) and 'Loopback' in name:
        ds5_dev = info
        print(f"Found loopback: [{i}] {name} ch={info['maxInputChannels']}")
        break

if ds5_dev is None:
    print("DualSense loopback not found!")
    p.terminate()
    exit(1)

# Force 4 channels even if device reports 2
channels = 4
rate = int(ds5_dev['defaultSampleRate'])
print(f"\nListening on: {ds5_dev['name']} (index {ds5_dev['index']})")
print(f"  Channels: {channels} (forced), Rate: {rate}")
print("Start Genshin now! Press Ctrl+C to stop.\n")
print("  CH1=FL  CH2=FR  CH3=RL(haptic?)  CH4=RR(haptic?)\n")

def callback(in_data, frame_count, time_info, status):
    data = np.frombuffer(in_data, dtype=np.float32).reshape(-1, channels)
    peaks = [np.max(np.abs(data[:, ch])) for ch in range(channels)]
    if max(peaks) > 0.001:
        bars = ['#' * int(min(p, 1.0) * 15) for p in peaks]
        print(f"\rCH1:{peaks[0]:.3f} {bars[0]:15s} | CH2:{peaks[1]:.3f} {bars[1]:15s} | CH3:{peaks[2]:.3f} {bars[2]:15s} | CH4:{peaks[3]:.3f} {bars[3]:15s}", end="", flush=True)
    return (None, pyaudio.paContinue)

try:
    stream = p.open(
        format=pyaudio.paFloat32,
        channels=channels,
        rate=rate,
        input=True,
        input_device_index=ds5_dev['index'],
        frames_per_buffer=512,
        stream_callback=callback
    )
except Exception as e:
    print(f"Failed to open 4ch: {e}")
    print("Trying 2ch fallback...")
    channels = 2
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
