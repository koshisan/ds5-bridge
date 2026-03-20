#!/usr/bin/env python3
"""
Frida-based HID sniffer for DSX.exe
Hooks at ntdll level to capture ALL writes to HID devices.

Usage:
    pip install frida-tools
    python frida_dsx_sniff.py <PID>
"""

import frida
import sys
import signal

SCRIPT_CODE = r"""
'use strict';

const NtWriteFile = Module.getExportByName('ntdll.dll', 'NtWriteFile');
const NtDeviceIoControlFile = Module.getExportByName('ntdll.dll', 'NtDeviceIoControlFile');
const NtCreateFile = Module.getExportByName('ntdll.dll', 'NtCreateFile');
const GetFinalPathNameByHandleW = Module.getExportByName('kernel32.dll', 'GetFinalPathNameByHandleW');

// Also try hooking HidD_ functions directly
var HidD_SetOutputReport = null;
var HidD_GetFeature = null;
var HidD_SetFeature = null;
try { HidD_SetOutputReport = Module.getExportByName('hid.dll', 'HidD_SetOutputReport'); } catch(e) {}
try { HidD_GetFeature = Module.getExportByName('hid.dll', 'HidD_GetFeature'); } catch(e) {}
try { HidD_SetFeature = Module.getExportByName('hid.dll', 'HidD_SetFeature'); } catch(e) {}

const handlePaths = {};

function getPath(h) {
    const key = h.toString();
    if (handlePaths[key] !== undefined) return handlePaths[key];
    try {
        const buf = Memory.alloc(520);
        const fn = new NativeFunction(GetFinalPathNameByHandleW, 'uint32',
            ['pointer', 'pointer', 'uint32', 'uint32']);
        const len = fn(h, buf, 260, 0);
        const path = len > 0 ? buf.readUtf16String() : '';
        handlePaths[key] = path;
        return path;
    } catch(e) {
        handlePaths[key] = '';
        return '';
    }
}

function dumpBytes(ptr, len) {
    try {
        const n = Math.min(len, 141);
        const bytes = Array.from(new Uint8Array(ptr.readByteArray(n)));
        return bytes.map(b => ('0' + b.toString(16)).slice(-2)).join(' ');
    } catch(e) {
        return '<read error>';
    }
}

function isHidPath(path) {
    const p = path.toLowerCase();
    return p.indexOf('hid') !== -1 || p.indexOf('054c') !== -1 || p.indexOf('0ce6') !== -1;
}

// NtWriteFile — lowest level file write
Interceptor.attach(NtWriteFile, {
    onEnter(args) {
        // NtWriteFile(FileHandle, Event, ApcRoutine, ApcContext, IoStatusBlock, Buffer, Length, ...)
        const handle = args[0];
        const buf = args[5];
        const len = args[6].toInt32();
        const path = getPath(handle);
        if (isHidPath(path) && len >= 4) {
            send({ api: 'NtWriteFile', dir: 'OUT', ts: Date.now(), len: len,
                   path: path, hex: dumpBytes(buf, len) });
        }
    }
});

// NtDeviceIoControlFile — catches DeviceIoControl, HidD_SetOutputReport internals
Interceptor.attach(NtDeviceIoControlFile, {
    onEnter(args) {
        // NtDeviceIoControlFile(FileHandle, Event, ApcRoutine, ApcCtx, IoStatus,
        //   IoControlCode, InputBuffer, InputBufferLength, OutputBuffer, OutputBufferLength)
        const handle = args[0];
        const ioctl = args[5].toInt32() >>> 0;
        const inBuf = args[6];
        const inLen = args[7].toInt32();
        const path = getPath(handle);
        if (isHidPath(path) && inLen >= 4) {
            send({ api: 'NtDeviceIoControlFile', dir: 'OUT', ts: Date.now(),
                   ioctl: '0x' + ioctl.toString(16).padStart(8, '0'),
                   len: inLen, path: path, hex: dumpBytes(inBuf, inLen) });
        }
    }
});

// HidD_SetOutputReport — if DSX uses this directly
if (HidD_SetOutputReport) {
    Interceptor.attach(HidD_SetOutputReport, {
        onEnter(args) {
            const handle = args[0];
            const buf = args[1];
            const len = args[2].toInt32();
            send({ api: 'HidD_SetOutputReport', dir: 'OUT', ts: Date.now(),
                   len: len, path: getPath(handle), hex: dumpBytes(buf, len) });
        }
    });
}

if (HidD_SetFeature) {
    Interceptor.attach(HidD_SetFeature, {
        onEnter(args) {
            const handle = args[0];
            const buf = args[1];
            const len = args[2].toInt32();
            send({ api: 'HidD_SetFeature', dir: 'OUT', ts: Date.now(),
                   len: len, path: getPath(handle), hex: dumpBytes(buf, len) });
        }
    });
}

if (HidD_GetFeature) {
    Interceptor.attach(HidD_GetFeature, {
        onEnter(args) {
            this.handle = args[0];
            this.buf = args[1];
            this.len = args[2].toInt32();
        },
        onLeave(retval) {
            if (retval.toInt32() !== 0) {
                send({ api: 'HidD_GetFeature', dir: 'IN', ts: Date.now(),
                       len: this.len, path: getPath(this.handle),
                       hex: dumpBytes(this.buf, this.len) });
            }
        }
    });
}
"""


def on_message(msg, data):
    if msg["type"] == "send":
        p = msg["payload"]
        d = p["dir"]
        arrow = ">>>" if d == "OUT" else "<<<"
        ioctl = f" IOCTL={p['ioctl']}" if "ioctl" in p else ""
        print(f"[{p['ts']}] {arrow} {p['api']}{ioctl} len={p['len']}")
        print(f"  PATH: {p['path']}")
        print(f"  DATA: {p['hex']}")
        print()
    elif msg["type"] == "error":
        print(f"[ERROR] {msg['description']}", file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        print("Usage: python frida_dsx_sniff.py <PID>")
        print()
        print("Find DSX PID with:")
        print("  Get-Process DSX | Select-Object Id, WorkingSet64")
        sys.exit(1)

    pid = int(sys.argv[1])
    print(f"Attaching to PID {pid}...")
    session = frida.attach(pid)
    script = session.create_script(SCRIPT_CODE)
    script.on("message", on_message)
    script.load()

    print(f"Hooked DSX PID {pid} — all HID APIs covered:")
    print("  - NtWriteFile (raw writes)")
    print("  - NtDeviceIoControlFile (IOCTL)")
    print("  - HidD_SetOutputReport / SetFeature / GetFeature")
    print()
    print("Activate haptics in DSX now! Ctrl+C to stop.")
    print("=" * 72)

    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    sys.stdin.read()


if __name__ == "__main__":
    main()
