"""Listen to DualSense virtual speaker - 4ch WASAPI loopback via sounddevice."""
import sounddevice as sd
import numpy as np
import sys

# Find DualSense output device
ds5_idx = None
for i, dev in enumerate(sd.query_devices()):
    if ('2- DualSense' in dev['name'] or '2-DualSense' in dev['name']) and dev['max_output_channels'] > 0 and dev['hostapi'] == sd.query_hostapis().index(next(h for h in sd.query_hostapis() if 'WASAPI' in h['name'])):
        ds5_idx = i
        print(f"Found: [{i}] {dev['name']} out_ch={dev['max_output_channels']}")
        break

if ds5_idx is None:
    print("DualSense speaker not found! WASAPI devices:")
    for i, dev in enumerate(sd.query_devices()):
        if dev['max_output_channels'] > 0:
            print(f"  [{i}] {dev['name']} out={dev['max_output_channels']}ch")
    sys.exit(1)

channels = 4
rate = 48000
print(f"\nLoopback capture: {channels}ch, {rate}Hz")
print("CH1=FL  CH2=FR  CH3=RL(haptic)  CH4=RR(haptic)")
print("Start Genshin now! Press Ctrl+C to stop.\n")

def callback(indata, frames, time, status):
    if status:
        print(f"\r{status}", flush=True)
    peaks = [np.max(np.abs(indata[:, ch])) for ch in range(min(channels, indata.shape[1]))]
    if max(peaks) > 0.001:
        bars = ['#' * int(min(p, 1.0) * 15) for p in peaks]
        print(f"\rCH1:{peaks[0]:.3f} {bars[0]:15s}|CH2:{peaks[1]:.3f} {bars[1]:15s}|CH3:{peaks[2]:.3f} {bars[2]:15s}|CH4:{peaks[3]:.3f} {bars[3]:15s}", end="", flush=True)

try:
    # WASAPI loopback: use the output device as input with wasapi_loopback=True (sounddevice >=0.4.0)
    with sd.InputStream(device=ds5_idx, channels=channels, samplerate=rate,
                        dtype='float32', callback=callback,
                        extra_settings=sd.WasapiSettings(exclusive=False)):
        print("Listening... (Ctrl+C to stop)")
        while True:
            sd.sleep(100)
except KeyboardInterrupt:
    print("\nStopped.")
except Exception as e:
    print(f"\nError: {e}")
    print("\nTrying alternative: loopback via pyaudiowpatch...")
    print("Run: pip install pyaudiowpatch")
