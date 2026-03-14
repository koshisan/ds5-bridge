"""Listen to DualSense virtual speaker via WASAPI loopback (4ch)."""
import comtypes
import numpy as np
import time
import sys

# Initialize COM
comtypes.CoInitialize()

from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IMMDeviceEnumerator, EDataFlow, ERole
from pycaw.constants import CLSID_MMDeviceEnumerator
import ctypes

# Find DualSense output device
devices = AudioUtilities.GetAllDevices()
ds5_dev = None
for d in devices:
    if d.FriendlyName and ('2- DualSense' in d.FriendlyName or '2-DualSense' in d.FriendlyName):
        ds5_dev = d
        print(f"Found: {d.FriendlyName} (id: {d.id})")
        break

if ds5_dev is None:
    print("DualSense speaker not found!")
    print("Available devices:")
    for d in devices:
        if d.FriendlyName:
            print(f"  {d.FriendlyName}")
    sys.exit(1)

# Use WASAPI loopback capture via IAudioClient
from comtypes import GUID
import wave
import struct

# Get the IMMDevice
enumerator = comtypes.CoCreateInstance(
    CLSID_MMDeviceEnumerator,
    IMMDeviceEnumerator,
    CLSCTX_ALL
)

# Enumerate render devices to find ours
from pycaw.pycaw import IMMDeviceCollection
collection = enumerator.EnumAudioEndpoints(EDataFlow.eRender.value, 0x00000001)  # DEVICE_STATE_ACTIVE
count = collection.GetCount()

imm_device = None
for i in range(count):
    dev = collection.Item(i)
    dev_id = dev.GetId()
    if ds5_dev.id in dev_id or dev_id in ds5_dev.id:
        imm_device = dev
        print(f"Matched IMMDevice: {dev_id}")
        break

if imm_device is None:
    print("Could not find IMMDevice!")
    sys.exit(1)

# Get mix format
from pycaw.pycaw import IAudioClient
audio_client = imm_device.Activate(IAudioClient._iid_, CLSCTX_ALL, None)

# Get the mix format (what the device actually uses)
mix_format = audio_client.GetMixFormat()
fmt = mix_format.contents
print(f"\nDevice mix format:")
print(f"  Channels: {fmt.nChannels}")
print(f"  SampleRate: {fmt.nSamplesPerSec}")
print(f"  BitsPerSample: {fmt.wBitsPerSample}")
print(f"  BlockAlign: {fmt.nBlockAlign}")
print(f"  FormatTag: 0x{fmt.wFormatTag:X}")
print(f"\nStart Genshin now! Press Ctrl+C to stop.\n")

if fmt.nChannels >= 4:
    print("  CH1=FL  CH2=FR  CH3=RL(haptic?)  CH4=RR(haptic?)\n")
else:
    print(f"  WARNING: Only {fmt.nChannels} channels in mix format!\n")

comtypes.CoUninitialize()
