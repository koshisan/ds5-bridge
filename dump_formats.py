import comtypes
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities
import struct

devices = AudioUtilities.GetAllDevices()
for d in devices:
    if 'DualSense' in (d.FriendlyName or '') or '2-' in (d.FriendlyName or ''):
        print(f"\n=== {d.FriendlyName} ===")
        print(f"  ID: {d.id}")
        print(f"  State: {d.state}")
        
        try:
            props = d._dev.OpenPropertyStore(0)  # STGM_READ
            count = props.GetCount()
            for i in range(count):
                try:
                    pk = props.GetAt(i)
                    val = props.GetValue(pk)
                    # Format key
                    fmtid = str(pk.fmtid)
                    pid = pk.pid
                    # Check for audio format properties
                    if 'f19f064d' in fmtid.lower() or 'e4870e26' in fmtid.lower():
                        print(f"  Property {fmtid}#{pid} = {val}")
                except:
                    pass
        except Exception as e:
            print(f"  Error: {e}")

# Also try via IAudioClient
print("\n=== Mix Format (IAudioClient) ===")
import comtypes
from ctypes import POINTER, cast, byref, c_void_p
for d in devices:
    if 'DualSense' in (d.FriendlyName or '') and d.state == 1:
        try:
            client = d._dev.Activate(
                comtypes.GUID('{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}'),
                CLSCTX_ALL, None)
            from pycaw.pycaw import WAVEFORMATEX
            # GetMixFormat
            import ctypes
            fmt_ptr = ctypes.POINTER(WAVEFORMATEX)()
            client.GetMixFormat(ctypes.byref(fmt_ptr))
            fmt = fmt_ptr.contents
            print(f"  {d.FriendlyName}:")
            print(f"    wFormatTag: {fmt.wFormatTag}")
            print(f"    nChannels: {fmt.nChannels}")
            print(f"    nSamplesPerSec: {fmt.nSamplesPerSec}")
            print(f"    nAvgBytesPerSec: {fmt.nAvgBytesPerSec}")
            print(f"    nBlockAlign: {fmt.nBlockAlign}")
            print(f"    wBitsPerSample: {fmt.wBitsPerSample}")
        except Exception as e:
            print(f"  {d.FriendlyName}: {e}")
