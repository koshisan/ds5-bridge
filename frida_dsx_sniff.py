#!/usr/bin/env python3
"""
Frida-based HID sniffer for DSX.exe (32-bit compatible)

Usage:
    pip install frida-tools
    python frida_dsx_sniff.py <PID>
"""

import frida
import sys
import signal
import subprocess

SCRIPT_CODE = """
'use strict';

function hookExport(name, handler) {
    var addr = Module.findExportByName(null, name);
    if (addr) {
        Interceptor.attach(addr, handler);
        console.log('[+] ' + name + ' hooked at ' + addr);
    } else {
        console.log('[-] ' + name + ' not found');
    }
}

hookExport('WriteFile', {
    onEnter: function(args) {
        var len = args[2].toInt32();
        if (len >= 10 && len <= 548) {
            console.log('>>> WriteFile len=' + len);
            console.log(hexdump(args[1], {length: Math.min(len, 141)}));
        }
    }
});

hookExport('DeviceIoControl', {
    onEnter: function(args) {
        var ioctl = args[1].toInt32() >>> 0;
        var inLen = args[3].toInt32();
        if (inLen >= 10 && inLen <= 548) {
            console.log('>>> DeviceIoControl IOCTL=0x' + ioctl.toString(16).padStart(8, '0') + ' len=' + inLen);
            console.log(hexdump(args[2], {length: Math.min(inLen, 141)}));
        }
    }
});

hookExport('HidD_SetOutputReport', {
    onEnter: function(args) {
        var len = args[2].toInt32();
        console.log('>>> HidD_SetOutputReport len=' + len);
        console.log(hexdump(args[1], {length: Math.min(len, 141)}));
    }
});

hookExport('HidD_SetFeature', {
    onEnter: function(args) {
        var len = args[2].toInt32();
        console.log('>>> HidD_SetFeature len=' + len);
        console.log(hexdump(args[1], {length: Math.min(len, 141)}));
    }
});

console.log('\\n[*] Ready — activate DSX haptics!');
console.log('='.repeat(72));
"""


def on_message(msg, data):
    if msg["type"] == "log":
        print(msg["payload"])
    elif msg["type"] == "error":
        print(f"[ERROR] {msg['description']}", file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        print("Usage: python frida_dsx_sniff.py <PID>")
        sys.exit(1)

    pid = int(sys.argv[1])
    print(f"Attaching to PID {pid}...")

    session = frida.attach(pid)
    script = session.create_script(SCRIPT_CODE)
    script.on("message", on_message)
    script.load()

    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    sys.stdin.read()


if __name__ == "__main__":
    main()
