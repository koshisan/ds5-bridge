"""Listen to DualSense virtual speaker - WASAPI loopback."""
import comtypes
comtypes.CoInitialize()

from comtypes import CLSCTX_ALL, GUID, HRESULT, COMMETHOD, IUnknown
import ctypes
from ctypes import POINTER, byref, cast, c_uint32, c_void_p
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

# Find DualSense via pycaw (simple device enumeration)
devices = AudioUtilities.GetAllDevices()
ds5 = None
for d in devices:
    if d.FriendlyName and '2- DualSense' in d.FriendlyName:
        ds5 = d
        print(f"Found: {d.FriendlyName}")
        break

if ds5 is None:
    print("DualSense not found!")
    sys.exit(1)

# Get IMMDevice directly from pycaw device
imm_device = ds5._dev

# Activate as IAudioClient
punk = imm_device.Activate(IID_IAudioClient, CLSCTX_ALL, None)
audio_client = punk.QueryInterface(IAudioClient)

# Get mix format
pp_format = audio_client.GetMixFormat()
fmt = pp_format.contents
print(f"\nMix format: ch={fmt.nChannels} rate={fmt.nSamplesPerSec} bps={fmt.wBitsPerSample} align={fmt.nBlockAlign}")

if fmt.cbSize >= 22:
    ext = cast(pp_format, POINTER(WAVEFORMATEXTENSIBLE)).contents
    print(f"  validBps={ext.wValidBitsPerSample} mask=0x{ext.dwChannelMask:X}")

channels = fmt.nChannels
rate = fmt.nSamplesPerSec

# Initialize loopback
audio_client.Initialize(AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK,
                        REFTIMES_PER_SEC, 0, pp_format, None)

# Get capture client
capture_ptr = c_void_p()
audio_client.GetService(byref(IID_IAudioCaptureClient), byref(capture_ptr))
capture_client = ctypes.cast(capture_ptr, POINTER(IAudioCaptureClient)).contents

print(f"\nLoopback active: {channels}ch, {rate}Hz")
if channels >= 4:
    print("CH1=FL  CH2=FR  CH3=RL(haptic)  CH4=RR(haptic)")
print("Press Ctrl+C to stop.\n")

audio_client.Start()

try:
    while True:
        time.sleep(0.01)
        packet_size = c_uint32()
        capture_client.GetNextPacketSize(byref(packet_size))

        while packet_size.value > 0:
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
            capture_client.GetNextPacketSize(byref(packet_size))

except KeyboardInterrupt:
    print("\nStopped.")

audio_client.Stop()
comtypes.CoUninitialize()
