#!/usr/bin/env python3
"""
Frida-based sniffer for DSX.exe — DUMP EVERYTHING.
No filtering, just raw syscall interception.

Usage:
    pip install frida-tools
    python frida_dsx_sniff.py <PID>
"""

import frida
import sys
import signal

SCRIPT_CODE = r"""
'use strict';

function toHex(ptr, len) {
    try {
        var n = len < 64 ? len : 64;
        var arr = [];
        for (var i = 0; i < n; i++) {
            var b = ptr.add(i).readU8();
            arr.push(('0' + b.toString(16)).slice(-2));
        }
        return arr.join(' ');
    } catch(e) {
        return '<err>';
    }
}

// Hook NtWriteFile
try {
    var pNtWriteFile = Module.getExportByName('ntdll.dll', 'NtWriteFile');
    Interceptor.attach(pNtWriteFile, {
        onEnter: function(args) {
            var len = args[6].toInt32();
            if (len > 0 && len < 1000) {
                send({ api: 'NtWriteFile', len: len, hex: toHex(args[5], len) });
            }
        }
    });
    send({ api: 'HOOK', msg: 'NtWriteFile OK' });
} catch(e) {
    send({ api: 'HOOK_ERR', msg: 'NtWriteFile: ' + e.message });
}

// Hook NtDeviceIoControlFile
try {
    var pNtIoctl = Module.getExportByName('ntdll.dll', 'NtDeviceIoControlFile');
    Interceptor.attach(pNtIoctl, {
        onEnter: function(args) {
            var ioctl = args[5].toInt32() >>> 0;
            var inLen = args[7].toInt32();
            if (inLen > 0 && inLen < 1000) {
                send({ api: 'NtDeviceIoControlFile',
                       ioctl: '0x' + ioctl.toString(16).padStart(8, '0'),
                       len: inLen, hex: toHex(args[6], inLen) });
            }
        }
    });
    send({ api: 'HOOK', msg: 'NtDeviceIoControlFile OK' });
} catch(e) {
    send({ api: 'HOOK_ERR', msg: 'NtDeviceIoControlFile: ' + e.message });
}

// Hook WriteFile (kernel32)
try {
    var pWriteFile = Module.getExportByName('kernel32.dll', 'WriteFile');
    Interceptor.attach(pWriteFile, {
        onEnter: function(args) {
            var len = args[2].toInt32();
            if (len > 0 && len < 1000) {
                send({ api: 'WriteFile', len: len, hex: toHex(args[1], len) });
            }
        }
    });
    send({ api: 'HOOK', msg: 'WriteFile OK' });
} catch(e) {
    send({ api: 'HOOK_ERR', msg: 'WriteFile: ' + e.message });
}

// Hook DeviceIoControl (kernel32)
try {
    var pDevIoCtl = Module.getExportByName('kernel32.dll', 'DeviceIoControl');
    Interceptor.attach(pDevIoCtl, {
        onEnter: function(args) {
            var ioctl = args[1].toInt32() >>> 0;
            var inLen = args[3].toInt32();
            if (inLen > 0 && inLen < 1000) {
                send({ api: 'DeviceIoControl',
                       ioctl: '0x' + ioctl.toString(16).padStart(8, '0'),
                       len: inLen, hex: toHex(args[2], inLen) });
            }
        }
    });
    send({ api: 'HOOK', msg: 'DeviceIoControl OK' });
} catch(e) {
    send({ api: 'HOOK_ERR', msg: 'DeviceIoControl: ' + e.message });
}

// Hook HidD_ functions
var hidFuncs = ['HidD_SetOutputReport', 'HidD_GetFeature', 'HidD_SetFeature',
                'HidD_GetInputReport', 'HidD_GetAttributes'];
hidFuncs.forEach(function(name) {
    try {
        var addr = Module.getExportByName('hid.dll', name);
        Interceptor.attach(addr, {
            onEnter: function(args) {
                var len = args[2].toInt32();
                send({ api: name, len: len, hex: toHex(args[1], len) });
            }
        });
        send({ api: 'HOOK', msg: name + ' OK' });
    } catch(e) {
        send({ api: 'HOOK_ERR', msg: name + ': ' + e.message });
    }
});

// Hook WSASend / send (in case they use raw sockets/L2CAP)
try {
    var pSend = Module.getExportByName('ws2_32.dll', 'send');
    Interceptor.attach(pSend, {
        onEnter: function(args) {
            var len = args[2].toInt32();
            if (len > 0 && len < 1000) {
                send({ api: 'ws2_send', len: len, hex: toHex(args[1], len) });
            }
        }
    });
    send({ api: 'HOOK', msg: 'ws2_send OK' });
} catch(e) {
    send({ api: 'HOOK_ERR', msg: 'ws2_send: ' + e.message });
}

try {
    var pWSASend = Module.getExportByName('ws2_32.dll', 'WSASend');
    Interceptor.attach(pWSASend, {
        onEnter: function(args) {
            send({ api: 'WSASend', msg: 'called' });
        }
    });
    send({ api: 'HOOK', msg: 'WSASend OK' });
} catch(e) {
    send({ api: 'HOOK_ERR', msg: 'WSASend: ' + e.message });
}

send({ api: 'READY', msg: 'All hooks installed' });
"""

counter = {}

def on_message(msg, data):
    if msg["type"] == "send":
        p = msg["payload"]
        api = p["api"]

        if api in ("HOOK", "HOOK_ERR", "READY"):
            prefix = "✅" if api == "HOOK" else ("❌" if api == "HOOK_ERR" else "🎯")
            print(f"  {prefix} {p['msg']}")
            return

        # Count calls per API
        counter[api] = counter.get(api, 0) + 1
        ioctl = f" IOCTL={p['ioctl']}" if "ioctl" in p else ""
        print(f"[{counter[api]:5d}] {api}{ioctl} len={p.get('len','?')}")
        print(f"        {p.get('hex','')}")

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

    print()
    print("Waiting for calls... Ctrl+C to stop.")
    print("=" * 72)

    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    sys.stdin.read()


if __name__ == "__main__":
    main()
