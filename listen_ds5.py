"""DualSense HD Haptic Bridge: Capture game audio -> downsample -> UDP -> client.py -> DS5 BT Report 0x32
Based on SAxense research: https://apps.sdore.me/SAxense
"""
import pyaudiowpatch as pyaudio
import numpy as np
import socket
import time
import sys
import struct

# --- Configuration ---
DS5_HAPTIC_RATE = 3000       # 3kHz sample rate for DS5 haptics
DS5_SAMPLES_PER_PACKET = 32  # 32 stereo samples per Report 0x32
PACKET_INTERVAL = DS5_SAMPLES_PER_PACKET / DS5_HAPTIC_RATE  # ~10.67ms
UDP_HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
UDP_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 5556  # Separate port for haptic data
GAIN = 2.0  # Amplification factor

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
capture_rate = int(ds5_lb['defaultSampleRate'])
downsample_ratio = capture_rate // DS5_HAPTIC_RATE  # 48000/3000 = 16

print(f"\nCapture: {channels}ch, {capture_rate}Hz")
print(f"Downsample: {capture_rate} -> {DS5_HAPTIC_RATE}Hz (ratio {downsample_ratio})")
print(f"UDP target: {UDP_HOST}:{UDP_PORT}")
print(f"Gain: {GAIN}x")
print(f"Packet interval: {PACKET_INTERVAL*1000:.1f}ms")
print("Press Ctrl+C to stop.\n")

# UDP socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# Accumulator for downsampled samples
sample_buffer = bytearray()
seq = 0

def float_to_uint8(f):
    """Convert float [-1.0, 1.0] to uint8 [0, 255]."""
    return max(0, min(255, int((f * GAIN + 1.0) * 127.5)))

def send_haptic_packet():
    """Send a Report 0x32 haptic packet via UDP."""
    global seq, sample_buffer
    if len(sample_buffer) < DS5_SAMPLES_PER_PACKET * 2:  # stereo = 2 bytes per sample
        return

    # Extract 64 bytes (32 stereo samples)
    audio_data = bytes(sample_buffer[:DS5_SAMPLES_PER_PACKET * 2])
    sample_buffer = sample_buffer[DS5_SAMPLES_PER_PACKET * 2:]

    # Build UDP packet: [0]=magic 0x32, [1]=seq, [2..65]=audio samples
    packet = struct.pack('BB', 0x32, seq & 0xFF) + audio_data
    sock.sendto(packet, (UDP_HOST, UDP_PORT))
    seq += 1

def callback(in_data, frame_count, time_info, status):
    global sample_buffer
    data = np.frombuffer(in_data, dtype=np.float32).reshape(-1, channels)

    # If stereo (downmixed from quad), both channels contain haptic data
    # Average to mono then duplicate for stereo haptic, or use L/R directly
    if channels >= 2:
        left = data[:, 0]
        right = data[:, 1]
    else:
        left = right = data[:, 0]

    # Downsample by simple decimation (take every Nth sample)
    left_ds = left[::downsample_ratio]
    right_ds = right[::downsample_ratio]

    # Convert to uint8 and interleave (L, R, L, R, ...)
    for i in range(len(left_ds)):
        sample_buffer.append(float_to_uint8(left_ds[i]))
        sample_buffer.append(float_to_uint8(right_ds[i]))

    # Send complete packets (with silence gate)
    while len(sample_buffer) >= DS5_SAMPLES_PER_PACKET * 2:
        # Check if this chunk has actual audio
        chunk = sample_buffer[:DS5_SAMPLES_PER_PACKET * 2]
        has_signal = any(abs(b - 128) > 1 for b in chunk)
        if has_signal:
            send_haptic_packet()
        else:
            sample_buffer = sample_buffer[DS5_SAMPLES_PER_PACKET * 2:]

    # Show activity
    peak = max(np.max(np.abs(left)), np.max(np.abs(right)))
    if peak > 0.001:
        bars = '#' * int(min(peak * GAIN, 1.0) * 30)
        print(f"\rpeak:{peak:.3f} pkt:{seq} buf:{len(sample_buffer)} {bars}    ", end="", flush=True)

    return (None, pyaudio.paContinue)

stream = p.open(
    format=pyaudio.paFloat32,
    channels=channels,
    rate=capture_rate,
    input=True,
    input_device_index=ds5_lb['index'],
    frames_per_buffer=512,
    stream_callback=callback
)

print(f"Listening... Send haptic packets to {UDP_HOST}:{UDP_PORT}")
stream.start_stream()
try:
    while stream.is_active():
        time.sleep(0.1)
except KeyboardInterrupt:
    print(f"\nStopped. Total packets sent: {seq}")

stream.stop_stream()
stream.close()
sock.close()
p.terminate()
