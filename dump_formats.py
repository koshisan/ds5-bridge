from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from ctypes import cast, POINTER, Structure, c_ushort, c_uint, c_byte, sizeof, string_at, addressof
from comtypes import CLSCTX_ALL

class WAVEFORMATEX(Structure):
    _fields_ = [
        ('wFormatTag', c_ushort),
        ('nChannels', c_ushort),
        ('nSamplesPerSec', c_uint),
        ('nAvgBytesPerSec', c_uint),
        ('nBlockAlign', c_ushort),
        ('wBitsPerSample', c_ushort),
        ('cbSize', c_ushort),
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

        if str(d.state) != 'AudioDeviceState.Active':
            continue

        # Get properties via IPropertyStore
        try:
            store = d._dev.OpenPropertyStore(0)
            count = store.GetCount()
            for i in range(count):
                pk = store.GetAt(i)
                fmtid = str(pk.fmtid)
                pid = pk.pid
                try:
                    val = store.GetValue(pk)
                    v = val.GetValue()
                    # Audio endpoint format property
                    if pid == 0 and 'f19f064d' in fmtid.lower():
                        log(f"  [DeviceFormat] raw bytes: {bytes(v).hex() if isinstance(v, (bytes,bytearray)) else v}")
                    elif pid == 2 and '233164c8' in fmtid.lower():
                        log(f"  [FriendlyName] {v}")
                    elif isinstance(v, bytes) and len(v) >= 18:
                        fmt = WAVEFORMATEX.from_buffer_copy(v[:sizeof(WAVEFORMATEX)])
                        if fmt.wFormatTag in (1, 0xFFFE) and 0 < fmt.nChannels <= 8 and 8000 <= fmt.nSamplesPerSec <= 384000:
                            log(f"  [Property {fmtid}#{pid}] WAVEFORMAT:")
                            log(f"    wFormatTag: 0x{fmt.wFormatTag:04X}")
                            log(f"    nChannels: {fmt.nChannels}")
                            log(f"    nSamplesPerSec: {fmt.nSamplesPerSec}")
                            log(f"    nAvgBytesPerSec: {fmt.nAvgBytesPerSec}")
                            log(f"    nBlockAlign: {fmt.nBlockAlign}")
                            log(f"    wBitsPerSample: {fmt.wBitsPerSample}")
                            log(f"    cbSize: {fmt.cbSize}")
                            if fmt.cbSize >= 22 and fmt.wFormatTag == 0xFFFE:
                                valid_bits = int.from_bytes(v[18:20], 'little')
                                channel_mask = int.from_bytes(v[20:24], 'little')
                                log(f"    wValidBitsPerSample: {valid_bits}")
                                log(f"    dwChannelMask: 0x{channel_mask:08X}")
                                log(f"    SubFormat: {v[24:40].hex()}")
                except:
                    pass
        except Exception as e:
            log(f"  PropertyStore error: {e}")

out.close()
print(f"\nSaved to ds5_audio_formats.txt")
