"""Dump real DS5 audio device format via PowerShell MMDevice API"""
import subprocess, json

# Use PowerShell to query audio endpoint properties
ps = r'''
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
    int Activate(ref Guid iid, int clsCtx, IntPtr pActivationParams, [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface);
    int OpenPropertyStore(int stgmAccess, out IPropertyStore ppProperties);
    int GetId([MarshalAs(UnmanagedType.LPWStr)] out string ppstrId);
    int GetState(out int pdwState);
}

[Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyStore {
    int GetCount(out int cProps);
    int GetAt(int iProp, out PROPERTYKEY pkey);
    int GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
}

[StructLayout(LayoutKind.Sequential)]
struct PROPERTYKEY {
    public Guid fmtid;
    public int pid;
}

[StructLayout(LayoutKind.Sequential)]
struct PROPVARIANT {
    public short vt;
    public short r1, r2, r3;
    public IntPtr data1;
    public IntPtr data2;
}
"@ -ErrorAction SilentlyContinue

# Simpler: use Get-AudioDevice or direct WMI
# Actually just use powershell audio cmdlets
Get-CimInstance Win32_SoundDevice | Where-Object { $_.Name -like "*DualSense*" -or $_.Name -like "*Wireless Controller*" } | Format-List Name, Status, StatusInfo, ProductName, Manufacturer
'''

# Simplest approach: just dump via ffmpeg probe or similar
# Actually let's just use sounddevice which we know works
import sounddevice as sd
out = open('ds5_audio_formats.txt', 'w', encoding='utf-8')

def log(s=''):
    print(s)
    out.write(s + '\n')

log("=== All DualSense Audio Devices (sounddevice) ===")
for i, d in enumerate(sd.query_devices()):
    name = d['name']
    if 'DualSense' in name or ('2-' in name and 'Wireless Controller' in name):
        log(f"\n[{i}] {name}")
        log(f"  hostapi: {sd.query_hostapis(d['hostapi'])['name']}")
        log(f"  max_input_channels: {d['max_input_channels']}")
        log(f"  max_output_channels: {d['max_output_channels']}")
        log(f"  default_samplerate: {d['default_samplerate']}")
        log(f"  default_low_input_latency: {d['default_low_input_latency']}")
        log(f"  default_low_output_latency: {d['default_low_output_latency']}")

# Also try pyaudiowpatch for loopback info
try:
    import pyaudiowpatch as pyaudio
    p = pyaudio.PyAudio()
    log("\n=== All DualSense Audio Devices (pyaudiowpatch) ===")
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        name = info['name']
        if 'DualSense' in name or ('2-' in name and 'Wireless Controller' in name):
            log(f"\n[{i}] {name}")
            for k, v in sorted(info.items()):
                log(f"  {k}: {v}")
    p.terminate()
except ImportError:
    log("\npyaudiowpatch not available")

out.close()
print(f"\nSaved to ds5_audio_formats.txt")
