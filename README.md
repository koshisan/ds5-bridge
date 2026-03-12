# DS5 Bridge

**DualSense controller TCP bridge for remote gaming — with ALL features.**

Bridges a physical DualSense (DS5/DS5 Edge) controller over TCP to a remote host, bypassing Sunshine/Moonlight's ViGEmBus limitation. Full support for adaptive triggers, HD haptics, gyro, touchpad, and LEDs.

## Why?

Sunshine on Windows uses ViGEmBus which can only emulate Xbox 360 / DS4 controllers. This means:
- ❌ No adaptive triggers
- ❌ No HD haptics  
- ❌ No proper rumble
- ❌ DS5 features are lost

Meanwhile, the Web Gamepad API in Chrome can access ALL DS5 features over Bluetooth on Windows. The hardware supports it — the software stack just doesn't.

DS5 Bridge solves this by tunneling raw HID reports directly between client and host over TCP.

## Architecture

```
┌─────── CLIENT (this app) ───────┐         ┌──────── HOST (future) ────────┐
│                                  │         │                               │
│  DualSense Controller (BT/USB)   │         │  Virtual DS5 (VHF Driver)     │
│       ↕ HID Reports             │   TCP   │       ↕ HID Reports          │
│  DS5 Bridge Client              ├────────→│  DS5 Bridge Server            │
│       (Python + tkinter GUI)     │         │       (feeds games)          │
│                                  │         │                               │
└──────────────────────────────────┘         └───────────────────────────────┘
```

## Requirements

- Python 3.8+
- `hidapi` library

## Installation

```bash
pip install hidapi
```

## Usage

```bash
python client.py
```

1. **Select Controller** — dropdown shows detected DualSense devices
2. **Enter Server IP** — host running the DS5 Bridge Server (default port: 5555)
3. **Start** — begins forwarding HID reports
4. **Event Log** — shows button presses, trigger changes, touchpad events (gyro is throttled to avoid log flood)

## Supported Controllers

| Controller | VID | PID |
|-----------|-----|-----|
| DualSense | 054C | 0CE6 |
| DualSense Edge | 054C | 0DF2 |

## Platform Notes

### Windows (Bluetooth)
Works out of the box — no special drivers needed. Windows HID stack handles BT-connected DualSense natively.

### Windows (USB)
May require WinUSB driver. Use [Zadig](https://zadig.akeo.ie/) to replace the default HID driver with WinUSB for the DS5 device.

### Linux
May need udev rules for non-root HID access:
```bash
echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="054c", ATTRS{idProduct}=="0ce6", MODE="0666"' | sudo tee /etc/udev/rules.d/99-dualsense.rules
echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="054c", ATTRS{idProduct}=="0df2", MODE="0666"' | sudo tee -a /etc/udev/rules.d/99-dualsense.rules
sudo udevadm control --reload-rules
```

## TCP Protocol

Simple length-prefixed framing:
- **4 bytes:** payload length (big-endian)
- **1 byte:** message type
- **N bytes:** payload

| Type | Direction | Description |
|------|-----------|-------------|
| 0x01 | Client→Host | HID Input Report (raw bytes) |
| 0x02 | Host→Client | HID Output Report (triggers, haptics, LEDs) |
| 0x03 | Client→Host | Controller Info (JSON) |
| 0x04 | Both | Ping/Pong keepalive |

## Project Status

- [x] Client app with GUI
- [x] HID input report parsing (buttons, sticks, triggers, gyro, touchpad, battery)
- [x] TCP protocol with framing
- [ ] Host server (Windows VHF kernel driver)
- [ ] Output report forwarding (adaptive triggers, haptics)
- [ ] Sunshine/Moonlight integration

## Related

- [Virtual DS5 Driver](../projects/virtual-ds5-driver.md) — the host-side kernel driver (planned)
- [hid-playstation](https://github.com/torvalds/linux/blob/master/drivers/hid/hid-playstation.c) — Linux kernel DS5 driver (reference implementation)
- [DS5 Data Structures](https://controllers.fandom.com/wiki/Sony_DualSense/Data_Structures) — complete HID report documentation

## License

MIT
