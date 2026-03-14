"""Listen to DualSense virtual speaker - WASAPI loopback with forced 4ch."""
import comtypes
comtypes.CoInitialize()

from comtypes import CLSCTX_ALL, GUID, HRESULT, COMMETHOD, IUnknown
import ctypes
from ctypes import POINTER, byref, c_uint32, c_void_p
from pycaw.pycaw import AudioUtilities
import numpy as np
import sys
import time

class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ('wFormatTag', ctypes.c_ushort),
        ('nChannels', ctypes.c_ushort),
        ('nSamplesPerSec', ctypes.c_uint),
        ('nAvgBytesPerSec', ctypes.c_uint),
        ('nBlockAlign', ctypes.c_ushort),
        ('wBitsPerSample', ctypes.c_ushort),
        ('cbSize', ctypes.c_ushort),
    ]

class WAVEFORMATEXTENSIBLE(ctypes.Structure):
    _fields_ = [
        ('Format', WAVEFORMATEX),
        ('wValidBitsPerSample', ctypes.c_ushort),
        ('dwChannelMask', ctypes.c_uint),
        ('SubFormat', comtypes.GUID),
    ]

AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_SHAREMODE_SHARED = 0
REFTIMES_PER_SEC = 10000000
KSDATAFORMAT_SUBTYPE_PCM = GUID('{00000001-0000-0010-8000-00aa00389b71}')

IID_IAudioClient = GUID('{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}')
IID_IAudioCaptureClient = GUID('{C8ADBD64-E71E-48a0-A4DE-185C395CD317}')

class IAudioClient(IUnknown):
    _iid_ = IID_IAudioClient
    _methods_ = [
        COMMETHOD([], HRESULT, 'Initialize',
            (['in'], c_uint32, 'ShareMode'),
            (['in'], c_uint32, 'StreamFlags'),
            (['in'], ctypes.c_longlong, 'hnsBufferDuration'),
            (['in'], ctypes.c_longlong, 'hnsPeriodicity'),
            (['in'], POINTER(WAVEFORMATEX), 'pFormat'),
            (['in'], POINTER(GUID), 'AudioSessionGuid')),
        COMMETHOD([], HRESULT, 'GetBufferSize',
            (['out', 'retval'], POINTER(c_uint32), 'pNumBufferFrames')),
        COMMETHOD([], HRESULT, 'GetStreamLatency',
            (['out', 'retval'], POINTER(ctypes.c_longlong), 'phnsLatency')),
        COMMETHOD([], HRESULT, 'GetCurrentPadding',
            (['out', 'retval'], POINTER(c_uint32), 'pNumPaddingFrames')),
        COMMETHOD([], HRESULT, 'IsFormatSupported',
            (['in'], c_uint32, 'ShareMode'),
            (['in'], POINTER(WAVEFORMATEX), 'pFormat'),
            (['out'], POINTER(POINTER(WAVEFORMATEX)), 'ppClosestMatch')),
        COMMETHOD([], HRESULT, 'GetMixFormat',
            (['out', 'retval'], POINTER(POINTER(WAVEFORMATEX)), 'ppDeviceFormat')),
        COMMETHOD([], HRESULT, 'GetDevicePeriod',
            (['out'], POINTER(ctypes.c_longlong), 'phnsDefaultDevicePeriod'),
            (['out'], POINTER(ctypes.c_longlong), 'phnsMinimumDevicePeriod')),
        COMMETHOD([], HRESULT, 'Start'),
        COMMETHOD([], HRESULT, 'Stop'),
        COMMETHOD([], HRESULT, 'Reset'),
        COMMETHOD([], HRESULT, 'SetEventHandle',
            (['in'], ctypes.c_void_p, 'eventHandle')),
        COMMETHOD([], HRESULT, 'GetService',
            (['in'], POINTER(GUID), 'riid'),
            (['out', 'retval'], POINTER(c_void_p), 'ppv')),
    ]

class IAudioCaptureClient(IUnknown):
    _iid_ = IID_IAudioCaptureClient
    _methods_ = [
        COMMETHOD([], HRESULT, 'GetBuffer',
            (['out'], POINTER(ctypes.c_void_p), 'ppData'),
            (['out'], POINTER(c_uint32), 'pNumFramesAvailable'),
            (['out'], POINTER(c_uint32), 'pdwFlags'),
            (['out'], POINTER(ctypes.c_ulonglong), 'pu64DevicePosition'),
            (['out'], POINTER(ctypes.c_ulonglong), 'pu64QPCPosition')),
        COMMETHOD([], HRESULT, 'ReleaseBuffer',
            (['in'], c_uint32, 'NumFramesRead')),
        COMMETHOD([], HRESULT, 'GetNextPacketSize',
            (['out', 'retval'], POINTER(c_uint32), 'pNumFramesInNextPacket')),
    ]

# Find DualSense
devices = AudioUtilities.GetAllDevices()
ds5 = None
for d in devices:
    if d.FriendlyName and '2- DualSense' in d.FriendlyName:
        ds5 = d
        print(f"Found: {d.FriendlyName}")
        break

if not ds5:
    print("DualSense not found!")
    sys.exit(1)

imm_device = ds5._dev
punk = imm_device.Activate(IID_IAudioClient, CLSCTX_ALL, None)
audio_client = punk.QueryInterface(IAudioClient)

# Get mix format to see what Windows thinks
mix_fmt = audio_client.GetMixFormat()
print(f"Mix format: ch={mix_fmt.contents.nChannels} (we'll force 4ch)")

# Build explicit 4ch format
fmt = WAVEFORMATEXTENSIBLE()
fmt.Format.wFormatTag = 0xFFFE  # WAVE_FORMAT_EXTENSIBLE
fmt.Format.nChannels = 4
fmt.Format.nSamplesPerSec = 48000
fmt.Format.wBitsPerSample = 32
fmt.Format.nBlockAlign = 4 * 4  # 4ch * 4 bytes
fmt.Format.nAvgBytesPerSec = 48000 * 16
fmt.Format.cbSize = 22
fmt.wValidBitsPerSample = 32
fmt.dwChannelMask = 0x33  # KSAUDIO_SPEAKER_QUAD
fmt.SubFormat = KSDATAFORMAT_SUBTYPE_PCM

# Initialize with our 4ch format + loopback flag
try:
    audio_client.Initialize(
        AUDCLNT_SHAREMODE_SHARED,
        AUDCLNT_STREAMFLAGS_LOOPBACK,
        REFTIMES_PER_SEC, 0,
        ctypes.cast(byref(fmt), POINTER(WAVEFORMATEX)),
        None
    )
    print("Initialized with 4ch loopback!")
except Exception as e:
    print(f"4ch init failed: {e}")
    print("Falling back to mix format (2ch)...")
    # Need new IAudioClient for retry
    punk = imm_device.Activate(IID_IAudioClient, CLSCTX_ALL, None)
    audio_client = punk.QueryInterface(IAudioClient)
    audio_client.Initialize(
        AUDCLNT_SHAREMODE_SHARED,
        AUDCLNT_STREAMFLAGS_LOOPBACK,
        REFTIMES_PER_SEC, 0,
        mix_fmt,
        None
    )
    fmt.Format.nChannels = mix_fmt.contents.nChannels
    print(f"Initialized with {mix_fmt.contents.nChannels}ch loopback")

channels = fmt.Format.nChannels

# Get capture client
capture_ptr = audio_client.GetService(byref(IID_IAudioCaptureClient))
capture_client = ctypes.cast(capture_ptr, POINTER(IAudioCaptureClient)).contents

print(f"\nLoopback active: {channels}ch, 48000Hz")
if channels >= 4:
    print("CH1=FL  CH2=FR  CH3=RL(haptic)  CH4=RR(haptic)")
print("Press Ctrl+C to stop.\n")

audio_client.Start()

try:
    while True:
        time.sleep(0.01)
        packet_size = capture_client.GetNextPacketSize()

        while packet_size > 0:
            data_ptr = c_void_p()
            frames = c_uint32()
            flags = c_uint32()
            dev_pos = ctypes.c_ulonglong()
            qpc_pos = ctypes.c_ulonglong()

            capture_client.GetBuffer(byref(data_ptr), byref(frames), byref(flags),
                                     byref(dev_pos), byref(qpc_pos))

            if frames.value > 0 and data_ptr.value:
                n = frames.value * channels
                buf = (ctypes.c_float * n).from_address(data_ptr.value)
                data = np.ctypeslib.as_array(buf).reshape(-1, channels)

                peaks = [np.max(np.abs(data[:, ch])) for ch in range(min(channels, 4))]
                if max(peaks) > 0.001:
                    labels = ['FL', 'FR', 'RL', 'RR'][:min(channels, 4)]
                    parts = [f"{labels[i]}:{peaks[i]:.3f} {'#'*int(min(peaks[i],1)*15):15s}" for i in range(len(labels))]
                    print(f"\r{'|'.join(parts)}", end="", flush=True)

            capture_client.ReleaseBuffer(frames.value)
            packet_size = capture_client.GetNextPacketSize()

except KeyboardInterrupt:
    print("\nStopped.")

audio_client.Stop()
comtypes.CoUninitialize()
