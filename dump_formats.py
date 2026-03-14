import comtypes
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, WAVEFORMATEX
import ctypes

out = open('ds5_audio_formats.txt', 'w', encoding='utf-8')

def log(s=''):
    print(s)
    out.write(s + '\n')

devices = AudioUtilities.GetAllDevices()
for d in devices:
    name = d.FriendlyName or ''
    if 'DualSense' in name or ('2-' in name and 'Wireless Controller' in name):
        log(f"\n=== {name} ===")
        log(f"  ID: {d.id}")
        log(f"  State: {d.state}")

log("\n=== Mix Formats (IAudioClient.GetMixFormat) ===")
for d in devices:
    name = d.FriendlyName or ''
    if not ('DualSense' in name or ('2-' in name and 'Wireless Controller' in name)):
        continue
    if str(d.state) != 'AudioDeviceState.Active':
        continue
    try:
        client = d._dev.Activate(
            comtypes.GUID('{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}'),
            CLSCTX_ALL, None)
        fmt_ptr = ctypes.POINTER(WAVEFORMATEX)()
        client.GetMixFormat(ctypes.byref(fmt_ptr))
        fmt = fmt_ptr.contents
        log(f"\n  {name}:")
        log(f"    wFormatTag: {fmt.wFormatTag} ({'PCM' if fmt.wFormatTag==1 else 'EXTENSIBLE' if fmt.wFormatTag==0xFFFE else hex(fmt.wFormatTag)})")
        log(f"    nChannels: {fmt.nChannels}")
        log(f"    nSamplesPerSec: {fmt.nSamplesPerSec}")
        log(f"    nAvgBytesPerSec: {fmt.nAvgBytesPerSec}")
        log(f"    nBlockAlign: {fmt.nBlockAlign}")
        log(f"    wBitsPerSample: {fmt.wBitsPerSample}")
        log(f"    cbSize: {fmt.cbSize}")
        if fmt.cbSize >= 22 and fmt.wFormatTag == 0xFFFE:
            raw = ctypes.string_at(ctypes.addressof(fmt), ctypes.sizeof(WAVEFORMATEX) + fmt.cbSize)
            valid_bits = int.from_bytes(raw[18:20], 'little')
            channel_mask = int.from_bytes(raw[20:24], 'little')
            sub_guid = raw[24:40]
            log(f"    wValidBitsPerSample: {valid_bits}")
            log(f"    dwChannelMask: 0x{channel_mask:08X}")
            log(f"    SubFormat: {sub_guid.hex()}")
    except Exception as e:
        log(f"  {name}: ERROR {e}")

out.close()
print(f"\nSaved to ds5_audio_formats.txt")
