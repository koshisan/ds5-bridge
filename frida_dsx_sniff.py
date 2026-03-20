#!/usr/bin/env python3
"""
Frida-based HID sniffer for DSX.exe (32-bit compatible)
Uses frida.core (spawn/attach) with the same API that frida-trace uses.

Usage:
    pip install frida-tools
    python frida_dsx_sniff.py <PID>
"""

import frida
import sys
import signal

# Use frida-trace compatible approach — inject via session, not Module.getExportByName
SCRIPT_CODE = """
'use strict';

// WriteFile — main write path
var pWriteFile = Module.getExportByName('kernel32.dll', 'WriteFile');
Interceptor.attach(pWriteFile, {
    onEnter: function(args) {
        var len = args[2].toInt32();
        if (len >= 10 && len <= 548) {
            console.log('>>> WriteFile len=' + len);
            console.log(hexdump(args[1], {length: Math.min(len, 141)}));
        }
    }
});
console.log('[+] WriteFile hooked');

// DeviceIoControl
var pDevIoCtl = Module.getExportByName('kernel32.dll', 'DeviceIoControl');
Interceptor.attach(pDevIoCtl, {
    onEnter: function(args) {
        var ioctl = args[1].toInt32() >>> 0;
        var inLen = args[3].toInt32();
        if (inLen >= 10 && inLen <= 548) {
            console.log('>>> DeviceIoControl IOCTL=0x' + ioctl.toString(16).padStart(8, '0') + ' len=' + inLen);
            console.log(hexdump(args[2], {length: Math.min(inLen, 141)}));
        }
    }
});
console.log('[+] DeviceIoControl hooked');

// HidD_SetOutputReport
try {
    var pSetOutput = Module.getExportByName('hid.dll', 'HidD_SetOutputReport');
    Interceptor.attach(pSetOutput, {
        onEnter: function(args) {
            var len = args[2].toInt32();
            console.log('>>> HidD_SetOutputReport len=' + len);
            console.log(hexdump(args[1], {length: Math.min(len, 141)}));
        }
    });
    console.log('[+] HidD_SetOutputReport hooked');
} catch(e) {
    console.log('[-] HidD_SetOutputReport: ' + e.message);
}

// HidD_SetFeature
try {
    var pSetFeat = Module.getExportByName('hid.dll', 'HidD_SetFeature');
    Interceptor.attach(pSetFeat, {
        onEnter: function(args) {
            var len = args[2].toInt32();
            console.log('>>> HidD_SetFeature len=' + len);
            console.log(hexdump(args[1], {length: Math.min(len, 141)}));
        }
    });
    console.log('[+] HidD_SetFeature hooked');
} catch(e) {
    console.log('[-] HidD_SetFeature: ' + e.message);
}

console.log('\\n[*] All hooks ready — activate DSX haptics now!');
console.log('=' .repeat(72));
"""


def on_message(msg, data):
    if msg["type"] == "error":
        print(f"[ERROR] {msg['description']}", file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        print("Usage: python frida_dsx_sniff.py <PID>")
        print("  Get-Process DSX | Select-Object Id")
        sys.exit(1)

    pid = int(sys.argv[1])
    print(f"Attaching to PID {pid} (arch-aware)...")

    session = frida.attach(pid)
    script = session.create_script(SCRIPT_CODE, runtime="v8")
    script.on("message", on_message)
    script.load()

    print("Ctrl+C to stop.\n")
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    sys.stdin.read()


if __name__ == "__main__":
    main()
