"""Generate sine wave test WAVs at different frequencies."""
import wave
import struct
import math
import sys

rate = 48000
duration = 3.0
amplitude = 16000
n = int(rate * duration)

for freq in [50, 100, 150, 200, 300, 500]:
    fname = f"sine_{freq}hz.wav"
    wf = wave.open(fname, 'wb')
    wf.setnchannels(2)
    wf.setsampwidth(2)
    wf.setframerate(rate)
    for i in range(n):
        val = int(amplitude * math.sin(2 * math.pi * freq * i / rate))
        wf.writeframes(struct.pack('<hh', val, val))
    wf.close()
    print(f"Created {fname} ({freq}Hz, {duration}s, stereo, 48kHz)")
