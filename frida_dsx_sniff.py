#!/usr/bin/env python3
"""
Frida-based HID sniffer for DSX.exe
Hooks WriteFile on HID device handles to capture what DSX sends to the DualSense.

Usage:
    pip install frida-tools
    # Find DSX PID:
    #   Get-Process DSX | Select-Object Id, WorkingSet64
    python frida_dsx_sniff.py <PID>
"""

import frida
import sys
import signal

SCRIPT_CODE = r"""
'use strict';
const WriteFile = Module.getExportByName('kernel32.dll', 'WriteFile');
const ReadFile = Module.getExportByName('kernel32.dll', 'ReadFile');
const GetFinalPathNameByHandleW = Module.getExportByName('kernel32.dll', 'GetFinalPathNameByHandleW');

const handlePaths = {};

function getHandlePath(h) {
    const key = h.toString();
    if (handlePaths[key]) return handlePaths[key];
    const buf = Memory.alloc(520);
    const len = new NativeFunction(
        GetFinalPathNameByHandleW, 'uint32',
        ['pointer', 'pointer', 'uint32', 'uint32']
    )(h, buf, 260, 0);
    const path = len > 0 ? buf.readUtf16String() : '??';
    handlePaths[key] = path;
    return path;
}

function hexdump(ptr, len) {
    const bytes = Array.from(new Uint8Array(ptr.readByteArray(Math.min(len, 141))));
    return bytes.map(b => ('0' + b.toString(16)).slice(-2)).join(' ');
}

// Hook WriteFile — captures output reports (haptics, LED, etc.)
Interceptor.attach(WriteFile, {
    onEnter(args) {
        this.handle = args[0];
        this.buf = args[1];
        this.len = args[2].toInt32();
        const path = getHandlePath(this.handle);
        if (path.toLowerCase().indexOf('hid') !== -1 && this.len >= 10) {
            send({
                dir: 'OUT',
                ts: Date.now(),
                len: this.len,
                path: path,
                hex: hexdump(this.buf, this.len)
            });
        }
    }
});

// Hook ReadFile — captures input reports (optional, for full picture)
Interceptor.attach(ReadFile, {
    onEnter(args) {
        this.handle = args[0];
        this.buf = args[1];
        this.len = args[2].toInt32();
        this.path = getHandlePath(this.handle);
        this.isHid = this.path.toLowerCase().indexOf('hid') !== -1;
    },
    onLeave(retval) {
        if (this.isHid && retval.toInt32() !== 0 && this.len >= 10) {
            send({
                dir: 'IN',
                ts: Date.now(),
                len: this.len,
                path: this.path,
                hex: hexdump(this.buf, this.len)
            });
        }
    }
});
"""


def on_message(msg, data):
    if msg["type"] == "send":
        p = msg["payload"]
        direction = p["dir"]
        arrow = ">>>" if direction == "OUT" else "<<<"
        print(f"[{p['ts']}] {arrow} {direction} len={p['len']}  {p['path']}")
        print(f"  {p['hex']}")
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

    print(f"Hooked DSX PID {pid}")
    print("Activate haptics in DSX now! Ctrl+C to stop.")
    print("=" * 72)

    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    sys.stdin.read()


if __name__ == "__main__":
    main()
