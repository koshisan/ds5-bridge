"""Listen to DualSense virtual speaker - 4ch WASAPI loopback."""
import pyaudiowpatch as pyaudio
import numpy as np
import sys

p = pyaudio.PyAudio()

# Find DualSense OUTPUT device (not loopback)
ds5_out = None
for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)
    if ('2- DualSense' in info['name'] or '2-DualSense' in info['name']) and info['maxOutputChannels'] > 0 and not info.get('isLoopbackDevice'):
        ds5_out = info
        print(f"Found output: [{i}] {info['name']} out_ch={info['maxOutputChannels']}")
        break

if ds5_out is None:
    print("DualSense output device not found!")
    p.terminate()
    sys.exit(1)

# Use get_loopback_device_info_generator to find its loopback
ds5_lb = None
try:
    for lb in p.get_loopback_device_info_generator():
        if '2- DualSense' in lb['name'] or '2-DualSense' in lb['name']:
            ds5_lb = lb
            print(f"Found loopback: [{lb['index']}] {lb['name']} in_ch={lb['maxInputChannels']}")
            break
except Exception as e:
    print(f"Loopback generator error: {e}")

if ds5_lb is None:
    print("Loopback not found!")
    p.terminate()
    sys.exit(1)

# The loopback reports maxInputChannels=2 but the actual device is 4ch.
# pyaudiowpatch allows opening with more channels than reported if the device supports it.
channels = 4
rate = int(ds5_lb['defaultSampleRate'])

print(f"\nOpening loopback: {channels}ch (forced), {rate}Hz")
print("CH1=FL  CH2=FR  CH3=RL(haptic)  CH4=RR(haptic)")
print("Press Ctrl+C to stop.\n")

def callback(in_data, frame_count, time_info, status):
    data = np.frombuffer(in_data, dtype=np.float32)
    # If we got 4ch, reshape
    actual_ch = len(data) // frame_count
    if actual_ch >= 4:
        data = data.reshape(-1, actual_ch)
        peaks = [np.max(np.abs(data[:, ch])) for ch in range(4)]
        if max(peaks) > 0.001:
            bars = ['#' * int(min(p, 1.0) * 15) for p in peaks]
            print(f"\rCH1:{peaks[0]:.3f} {bars[0]:15s}|CH2:{peaks[1]:.3f} {bars[1]:15s}|CH3:{peaks[2]:.3f} {bars[2]:15s}|CH4:{peaks[3]:.3f} {bars[3]:15s}", end="", flush=True)
    else:
        # Only got 2ch
        data = data.reshape(-1, actual_ch)
        peaks = [np.max(np.abs(data[:, ch])) for ch in range(actual_ch)]
        if max(peaks) > 0.001:
            print(f"\r[{actual_ch}ch] peaks: {['%.3f'%p for p in peaks]}", end="", flush=True)
    return (None, pyaudio.paContinue)

try:
    stream = p.open(
        format=pyaudio.paFloat32,
        channels=channels,
        rate=rate,
        input=True,
        input_device_index=ds5_lb['index'],
        frames_per_buffer=512,
        stream_callback=callback
    )
except OSError as e:
    print(f"4ch failed ({e}), falling back to {ds5_lb['maxInputChannels']}ch")
    channels = ds5_lb['maxInputChannels']
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
        import time; time.sleep(0.1)
except KeyboardInterrupt:
    print("\nStopped.")

stream.stop_stream()
stream.close()
p.terminate()
