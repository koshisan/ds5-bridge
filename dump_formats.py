import comtypes
from comtypes import CLSCTX_ALL, GUID
from pycaw.pycaw import AudioUtilities
import ctypes

# Define IAudioClient interface
class IAudioClient(comtypes.IUnknown):
    _iid_ = GUID('{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}')
    _methods_ = [
        comtypes.COMMETHOD([], comtypes.HRESULT, 'Initialize'),
        comtypes.COMMETHOD([], comtypes.HRESULT, 'GetBufferSize'),
        comtypes.COMMETHOD([], comtypes.HRESULT, 'GetStreamLatency'),
        comtypes.COMMETHOD([], comtypes.HRESULT, 'GetCurrentPadding'),
        comtypes.COMMETHOD([], comtypes.HRESULT, 'IsFormatSupported'),
        comtypes.COMMETHOD([], comtypes.HRESULT, 'GetMixFormat',
            ['out'], ctypes.POINTER(ctypes.c_void_p), 'ppDeviceFormat'),
    ]

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
        client = d._dev.Activate(IAudioClient._iid_, CLSCTX_ALL, None)
        ac = client.QueryInterface(IAudioClient)
        fmt_ptr = ctypes.c_void_p()
        ac.GetMixFormat(ctypes.byref(fmt_ptr))
        fmt = ctypes.cast(fmt_ptr, ctypes.POINTER(WAVEFORMATEX)).contents
        log(f"\n  {name}:")
        log(f"    wFormatTag: 0x{fmt.wFormatTag:04X} ({'PCM' if fmt.wFormatTag==1 else 'EXTENSIBLE' if fmt.wFormatTag==0xFFFE else 'other'})")
        log(f"    nChannels: {fmt.nChannels}")
        log(f"    nSamplesPerSec: {fmt.nSamplesPerSec}")
        log(f"    nAvgBytesPerSec: {fmt.nAvgBytesPerSec}")
        log(f"    nBlockAlign: {fmt.nBlockAlign}")
        log(f"    wBitsPerSample: {fmt.wBitsPerSample}")
        log(f"    cbSize: {fmt.cbSize}")
        if fmt.cbSize >= 22 and fmt.wFormatTag == 0xFFFE:
            raw = (ctypes.c_byte * (18 + fmt.cbSize)).from_address(fmt_ptr.value)
            data = bytes(raw)
            valid_bits = int.from_bytes(data[18:20], 'little')
            channel_mask = int.from_bytes(data[20:24], 'little')
            log(f"    wValidBitsPerSample: {valid_bits}")
            log(f"    dwChannelMask: 0x{channel_mask:08X}")
            log(f"    SubFormat: {data[24:40].hex()}")
    except Exception as e:
        log(f"  {name}: ERROR {e}")

out.close()
print(f"\nSaved to ds5_audio_formats.txt")
