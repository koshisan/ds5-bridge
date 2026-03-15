"""Compare: play same WAV via pyaudiowpatch vs sounddevice on DS5 ch3+4."""
import sys
import time
import numpy as np

if len(sys.argv) < 2:
    print("Usage: python test_playback_compare.py <file.wav>")
    sys.exit(1)

import soundfile as sf
data, rate = sf.read(sys.argv[1], dtype='int16')
if data.ndim == 1:
    data = np.column_stack([data, data])
print(f"WAV: {data.shape}, {rate}Hz")

# Build 4ch: silence on 1+2, haptic on 3+4
n = data.shape[0]
out_4ch = np.zeros((n, 4), dtype=np.int16)
out_4ch[:, 2] = data[:, 0]
out_4ch[:, 3] = data[:, 1]

# Find DS5
import sounddevice as sd
ds5_sd = None
for i, d in enumerate(sd.query_devices()):
    if ('DualSense' in d['name'] or 'Wireless Controller' in d['name']) and d['max_output_channels'] >= 4:
        ds5_sd = i
        print(f"sounddevice: [{i}] {d['name']}")
        break

import pyaudiowpatch as pyaudio
pa = pyaudio.PyAudio()
ds5_pa = None
for i in range(pa.get_device_count()):
    info = pa.get_device_info_by_index(i)
    if ('DualSense' in info['name'] or 'Wireless Controller' in info['name']) and info['maxOutputChannels'] >= 4 and not info.get('isLoopbackDevice'):
        ds5_pa = info
        print(f"pyaudiowpatch: [{i}] {info['name']}")
        break

# Limit to 5 seconds
max_samples = min(n, rate * 5)

print(f"\n=== sounddevice (4ch, ch3+4) ===")
sd.play(out_4ch[:max_samples], samplerate=rate, device=ds5_sd, blocking=True)
print("Done")
time.sleep(1)

print(f"\n=== pyaudiowpatch (4ch, ch3+4) ===")
import threading
pos = [0]
raw = out_4ch[:max_samples].tobytes()
bytes_per_frame = 4 * 2
done_event = threading.Event()

def callback(in_data, frame_count, time_info, status):
    start = pos[0]
    end = start + frame_count * bytes_per_frame
    chunk = raw[start:end]
    if len(chunk) < frame_count * bytes_per_frame:
        chunk = chunk + b"\x00" * (frame_count * bytes_per_frame - len(chunk))
        pos[0] = len(raw)
        done_event.set()
        return (chunk, pyaudio.paComplete)
    pos[0] = end
    return (chunk, pyaudio.paContinue)

stream = pa.open(format=pyaudio.paInt16, channels=4, rate=rate, output=True,
                 output_device_index=ds5_pa["index"], frames_per_buffer=1024,
                 stream_callback=callback)
stream.start_stream()
done_event.wait(timeout=10)
time.sleep(0.5)
stream.stop_stream()
stream.close()
pa.terminate()
print("Done")

print("\nWhich felt stronger?")
