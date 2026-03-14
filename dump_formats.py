import sys
import comtypes
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities
import ctypes

out = open('ds5_audio_formats.txt', 'w', encoding='utf-8') if '--save' not in sys.argv else open('ds5_audio_formats.txt', 'w', encoding='utf-8')

def log(s=''):
    print(s)
    out.write(s + '\n')

devices = AudioUtilities.GetAllDevices()
for d in devices:
    if 'DualSense' in (d.FriendlyName or '') or '2-' in (d.FriendlyName or ''):
        log(f"\n=== {d.FriendlyName} ===")
        log(f"  ID: {d.id}")
        log(f"  State: {d.state}")

# Mix Format via IAudioClient
from pycaw.pycaw import WAVEFORMATEX
log("\n=== Mix Formats (IAudioClient.GetMixFormat) ===")
for d in devices:
    if ('DualSense' in (d.FriendlyName or '') or '2-' in (d.FriendlyName or '')) and d.state == 1:
        try:
            client = d._dev.Activate(
                comtypes.GUID('{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}'),
                CLSCTX_ALL, None)
            fmt_ptr = ctypes.POINTER(WAVEFORMATEX)()
            client.GetMixFormat(ctypes.byref(fmt_ptr))
            fmt = fmt_ptr.contents
            log(f"  {d.FriendlyName}:")
            log(f"    wFormatTag: {fmt.wFormatTag} ({'PCM' if fmt.wFormatTag==1 else 'EXTENSIBLE' if fmt.wFormatTag==0xFFFE else 'other'})")
            log(f"    nChannels: {fmt.nChannels}")
            log(f"    nSamplesPerSec: {fmt.nSamplesPerSec}")
            log(f"    nAvgBytesPerSec: {fmt.nAvgBytesPerSec}")
            log(f"    nBlockAlign: {fmt.nBlockAlign}")
            log(f"    wBitsPerSample: {fmt.wBitsPerSample}")
            log(f"    cbSize: {fmt.cbSize}")
            if fmt.cbSize >= 22 and fmt.wFormatTag == 0xFFFE:
                # Read WAVEFORMATEXTENSIBLE extra data
                raw = ctypes.string_at(ctypes.addressof(fmt), ctypes.sizeof(WAVEFORMATEX) + fmt.cbSize)
                valid_bits = int.from_bytes(raw[18:20], 'little')
                channel_mask = int.from_bytes(raw[20:24], 'little')
                sub_format = raw[24:40]
                log(f"    wValidBitsPerSample: {valid_bits}")
                log(f"    dwChannelMask: 0x{channel_mask:08X}")
                log(f"    SubFormat GUID: {sub_format.hex()}")
        except Exception as e:
            log(f"  {d.FriendlyName}: ERROR {e}")

out.close()
print(f"\nSaved to ds5_audio_formats.txt")
