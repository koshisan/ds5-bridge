"""DS5 (DualSense) HID parsing and communication."""

import hid
import logging
logger = logging.getLogger(__name__)

# Device IDs
DS5_VID = 0x054C
DS5_PID = 0x0CE6
DS5_EDGE_PID = 0x0DF2

PRODUCT_IDS = {DS5_PID, DS5_EDGE_PID}
PRODUCT_NAMES = {
    DS5_PID: "DualSense",
    DS5_EDGE_PID: "DualSense Edge",
}

# DPad direction lookup (low nibble of button byte 0)
DPAD_DIRECTIONS = {
    0: "Up",
    1: "Up-Right",
    2: "Right",
    3: "Down-Right",
    4: "Down",
    5: "Down-Left",
    6: "Left",
    7: "Up-Left",
    8: "Released",
}

# Button bit masks for button bytes
# Byte 8 high nibble (bits 4-7)
BTN_SQUARE = (8, 4)
BTN_CROSS = (8, 5)
BTN_CIRCLE = (8, 6)
BTN_TRIANGLE = (8, 7)

# Byte 9
BTN_L1 = (9, 0)
BTN_R1 = (9, 1)
BTN_L2_DIGITAL = (9, 2)
BTN_R2_DIGITAL = (9, 3)
BTN_SHARE = (9, 4)
BTN_OPTIONS = (9, 5)
BTN_L3 = (9, 6)
BTN_R3 = (9, 7)

# Byte 10
BTN_PS = (10, 0)
BTN_TOUCHPAD = (10, 1)
BTN_MUTE = (10, 2)

BUTTON_MAP = {
    "Square": BTN_SQUARE,
    "Cross": BTN_CROSS,
    "Circle": BTN_CIRCLE,
    "Triangle": BTN_TRIANGLE,
    "L1": BTN_L1,
    "R1": BTN_R1,
    "L2": BTN_L2_DIGITAL,
    "R2": BTN_R2_DIGITAL,
    "Share": BTN_SHARE,
    "Options": BTN_OPTIONS,
    "L3": BTN_L3,
    "R3": BTN_R3,
    "PS": BTN_PS,
    "Touchpad": BTN_TOUCHPAD,
    "Mute": BTN_MUTE,
}


class DS5Device:
    """Represents a detected DS5 HID device."""

    def __init__(self, hid_info: dict):
        self.path: bytes = hid_info["path"]
        self.vid: int = hid_info["vendor_id"]
        self.pid: int = hid_info["product_id"]
        self.product_name: str = PRODUCT_NAMES.get(self.pid, "Unknown")
        self.serial: str = hid_info.get("serial_number", "") or ""
        self.interface: int = hid_info.get("interface_number", -1)
        self._usage_page: int = hid_info.get("usage_page", 0)
        self.is_bt: bool | None = None  # Determined on open
        self.last_error: str = ''
        self.device: hid.Device | None = None

    @property
    def display_name(self) -> str:
        conn = ""
        if self.is_bt is not None:
            conn = " (BT)" if self.is_bt else " (USB)"
        return f"{self.product_name}{conn}"

    def open(self) -> bool:
        """Open the HID device. Returns True on success."""
        try:
            self.device = hid.Device(path=self.path)
            logger.info(f"Opened device at {self.path}")
            # Read one report to determine USB vs BT
            test_report = self.device.read(64, timeout=100)
            if test_report:
                # BT reports start with 0x31, USB with 0x01
                if len(test_report) > 0:
                    self.is_bt = test_report[0] == 0x31
                else:
                    self.is_bt = False
            else:
                # Default to USB if we can't determine
                self.is_bt = False
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Failed to open device: {e}")
            self.device = None
            return False

    def close(self):
        """Close the HID device."""
        if self.device:
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None

    def read_input(self, timeout_ms: int = 10) -> bytes | None:
        """Read an input report. Returns None on timeout or error."""
        if not self.device:
            return None
        try:
            data = self.device.read(128, timeout=timeout_ms)
            if data:
                return bytes(data)
            return None
        except Exception:
            return None

    def write_output(self, report: bytes) -> bool:
        """Write an output report to the device."""
        if not self.device:
            return False
        try:
            self.device.write(report)
            return True
        except Exception:
            return False

    @property
    def connection_mode(self) -> str:
        return "BT" if self.is_bt else "USB"


class DS5InputState:
    """Parsed state from a DS5 input report."""

    def __init__(self):
        self.lx: int = 128
        self.ly: int = 128
        self.rx: int = 128
        self.ry: int = 128
        self.l2_analog: int = 0
        self.r2_analog: int = 0
        self.buttons: dict[str, bool] = {name: False for name in BUTTON_MAP}
        self.dpad: str = "Released"
        self.touch0_active: bool = False
        self.touch0_id: int = 0
        self.touch0_x: int = 0
        self.touch0_y: int = 0
        self.touch1_active: bool = False
        self.touch1_id: int = 0
        self.touch1_x: int = 0
        self.touch1_y: int = 0
        self.gyro_pitch: int = 0
        self.gyro_yaw: int = 0
        self.gyro_roll: int = 0
        self.accel_x: int = 0
        self.accel_y: int = 0
        self.accel_z: int = 0
        self.battery_level: int = 0
        self.battery_status: int = 0


def parse_input_report(data: bytes, is_bt: bool) -> DS5InputState | None:
    """Parse a DS5 input report into a DS5InputState."""
    if not data:
        return None

    # Validate report ID
    if is_bt:
        if len(data) < 3 or data[0] != 0x31:
            return None
        offset = 2  # Skip report ID + padding byte
    else:
        if len(data) < 1 or data[0] != 0x01:
            return None
        offset = 1  # Skip report ID

    if len(data) < offset + 54:
        return None

    d = data[offset:]
    state = DS5InputState()

    # Sticks (bytes 0-3 relative to offset)
    state.lx = d[0]
    state.ly = d[1]
    state.rx = d[2]
    state.ry = d[3]

    # Triggers (bytes 4-5)
    state.l2_analog = d[4]
    state.r2_analog = d[5]

    # Buttons (bytes 7-9 relative to data after offset)
    # Note: byte 6 is a counter
    btn0 = d[7]  # DPad in low nibble, face buttons in high nibble
    btn1 = d[8]  # Shoulder buttons etc.
    btn2 = d[9]  # PS, touchpad, mute

    # DPad
    dpad_val = btn0 & 0x0F
    state.dpad = DPAD_DIRECTIONS.get(dpad_val, "Released")

    # Face buttons (high nibble of btn0)
    for name, (byte_idx_raw, bit) in BUTTON_MAP.items():
        # Map raw byte indices to our offset variables
        if byte_idx_raw == 8:
            state.buttons[name] = bool(btn0 & (1 << bit))
        elif byte_idx_raw == 9:
            state.buttons[name] = bool(btn1 & (1 << bit))
        elif byte_idx_raw == 10:
            state.buttons[name] = bool(btn2 & (1 << bit))

    # Gyro (bytes 15-20 relative to offset, 3x int16 LE)
    state.gyro_pitch = int.from_bytes(d[15:17], "little", signed=True)
    state.gyro_yaw = int.from_bytes(d[17:19], "little", signed=True)
    state.gyro_roll = int.from_bytes(d[19:21], "little", signed=True)

    # Accelerometer (bytes 21-26)
    state.accel_x = int.from_bytes(d[21:23], "little", signed=True)
    state.accel_y = int.from_bytes(d[23:25], "little", signed=True)
    state.accel_z = int.from_bytes(d[25:27], "little", signed=True)

    # Touchpad (bytes 32-39 relative to offset)
    if len(d) > 39:
        # Touch point 0
        t0 = d[32:36]
        state.touch0_active = not bool(t0[0] & 0x80)
        state.touch0_id = t0[0] & 0x7F
        state.touch0_x = t0[1] | ((t0[2] & 0x0F) << 8)
        state.touch0_y = ((t0[2] & 0xF0) >> 4) | (t0[3] << 4)

        # Touch point 1
        t1 = d[36:40]
        state.touch1_active = not bool(t1[0] & 0x80)
        state.touch1_id = t1[0] & 0x7F
        state.touch1_x = t1[1] | ((t1[2] & 0x0F) << 8)
        state.touch1_y = ((t1[2] & 0xF0) >> 4) | (t1[3] << 4)

    # Battery (byte 52 relative to offset)
    if len(d) > 52:
        state.battery_level = d[52] & 0x0F
        state.battery_status = (d[52] >> 4) & 0x0F

    return state


def enumerate_ds5_devices() -> list[DS5Device]:
    """Find all connected DS5/DS5 Edge controllers."""
    devices = []
    seen_paths = set()
    for info in hid.enumerate(DS5_VID):
        if info["product_id"] not in PRODUCT_IDS:
            continue
        path = info["path"]
        if path in seen_paths:
            continue
        seen_paths.add(path)
        devices.append(DS5Device(info))
    return devices
