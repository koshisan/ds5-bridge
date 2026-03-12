#!/usr/bin/env python3
"""DS5 Bridge Host - Creates a virtual DS5 controller and receives input from the bridge client.

This runs on the gaming/streaming PC. It:
1. Creates a virtual DualSense HID device via SwDeviceCreate
2. Listens for TCP connections from the bridge client
3. Forwards input reports to the virtual controller driver
4. Forwards output reports (haptics, adaptive triggers, LED) back to the client
"""

import ctypes
import ctypes.wintypes as wintypes
import json
import logging
import socket
import struct
import sys
import threading
import time
from pathlib import Path

from protocol import (
    MSG_CONTROLLER_INFO,
    MSG_INPUT_REPORT,
    MSG_OUTPUT_REPORT,
    frame_message,
    frame_ping,
    read_frame,
)

logger = logging.getLogger(__name__)

# ============================================================
# Windows API declarations for Software Device creation
# ============================================================

# Device interface GUID (must match DS5Virtual driver's Public.h)
GUID_DEVINTERFACE_DS5Virtual = "{46354ffb-fe2d-4b6f-a48f-a7839aa9bd40}"

# IOCTL codes (must match Queue.c)
FILE_DEVICE_UNKNOWN = 0x00000022
METHOD_BUFFERED = 0
FILE_ANY_ACCESS = 0

def CTL_CODE(device_type, function, method, access):
    return (device_type << 16) | (access << 14) | (function << 2) | method

IOCTL_DS5_UPDATE_INPUT = CTL_CODE(FILE_DEVICE_UNKNOWN, 0x800, METHOD_BUFFERED, FILE_ANY_ACCESS)
IOCTL_DS5_READ_OUTPUT = CTL_CODE(FILE_DEVICE_UNKNOWN, 0x801, METHOD_BUFFERED, FILE_ANY_ACCESS)

# ============================================================
# SwDevice API (swdevice.dll) - Windows 8.1+
# ============================================================

HSWDEVICE = ctypes.c_void_p
HRESULT = ctypes.c_long
PCWSTR = ctypes.c_wchar_p

class SW_DEVICE_CREATE_INFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.ULONG),
        ("pszInstanceId", PCWSTR),
        ("pszzHardwareIds", PCWSTR),     # Multi-sz string
        ("pszzCompatibleIds", PCWSTR),    # Multi-sz string
        ("pContainerId", ctypes.c_void_p),
        ("CapabilityFlags", wintypes.ULONG),
        ("pszDeviceDescription", PCWSTR),
        ("pszDeviceLocation", PCWSTR),
        ("pSecurityDescriptor", ctypes.c_void_p),
    ]

# SwDeviceCreate callback type
SW_DEVICE_CREATE_CALLBACK = ctypes.CFUNCTYPE(
    None,           # void return
    HSWDEVICE,      # hSwDevice
    HRESULT,        # CreateResult
    ctypes.c_void_p,# pContext
    PCWSTR,         # pszDeviceInstanceId
)

# SW_DEVICE_CAPABILITIES flags
SWDeviceCapabilitiesRemovable = 0x00000001
SWDeviceCapabilitiesSilentInstall = 0x00000002
SWDeviceCapabilitiesNoDisplayInUI = 0x00000004
SWDeviceCapabilitiesDriverRequired = 0x00000008


class VirtualDS5Device:
    """Manages the virtual DS5 HID device via SwDeviceCreate."""

    def __init__(self):
        self.h_device: ctypes.c_void_p = None
        self.device_handle = None  # File handle for IOCTLs
        self._create_event = threading.Event()
        self._create_result: int = 0
        self._device_instance_id: str = ""

        # Load swdevice.dll
        try:
            self.swdevice = ctypes.WinDLL("swdevice.dll")
        except OSError:
            logger.error("Failed to load swdevice.dll - Windows 8.1+ required")
            raise

    def create(self) -> bool:
        """Create the virtual software device. Returns True on success."""

        # Hardware ID must match the INF file
        hardware_ids = "Root\\DS5Virtual\0\0"  # Double null terminated multi-sz

        create_info = SW_DEVICE_CREATE_INFO()
        create_info.cbSize = ctypes.sizeof(SW_DEVICE_CREATE_INFO)
        create_info.pszInstanceId = "DS5Bridge_Virtual_0"
        create_info.pszzHardwareIds = hardware_ids
        create_info.pszzCompatibleIds = None
        create_info.pContainerId = None
        create_info.CapabilityFlags = (
            SWDeviceCapabilitiesSilentInstall |
            SWDeviceCapabilitiesDriverRequired
        )
        create_info.pszDeviceDescription = "DualSense Virtual Controller"
        create_info.pszDeviceLocation = "DS5 Bridge"
        create_info.pSecurityDescriptor = None

        # Callback for device creation
        @SW_DEVICE_CREATE_CALLBACK
        def create_callback(hSwDevice, createResult, pContext, pszDeviceInstanceId):
            self._create_result = createResult
            if pszDeviceInstanceId:
                self._device_instance_id = ctypes.wstring_at(pszDeviceInstanceId)
            self._create_event.set()

        # Keep reference to prevent GC
        self._callback = create_callback

        h_device = HSWDEVICE()
        hr = self.swdevice.SwDeviceCreate(
            PCWSTR("DS5Bridge"),                    # pszEnumeratorName
            PCWSTR("HTREE\\ROOT\\0"),               # pszParentDeviceInstance
            ctypes.byref(create_info),              # pCreateInfo
            0,                                       # cPropertyCount
            None,                                    # pProperties
            create_callback,                         # pCallback
            None,                                    # pContext
            ctypes.byref(h_device),                 # phSwDevice
        )

        if hr < 0:
            logger.error(f"SwDeviceCreate failed: HRESULT=0x{hr & 0xFFFFFFFF:08X}")
            return False

        self.h_device = h_device

        # Wait for callback
        if not self._create_event.wait(timeout=10.0):
            logger.error("SwDeviceCreate callback timeout")
            return False

        if self._create_result < 0:
            logger.error(
                f"Device creation failed: HRESULT=0x{self._create_result & 0xFFFFFFFF:08X}"
            )
            return False

        logger.info(f"Virtual DS5 device created: {self._device_instance_id}")
        return True

    def open_device_interface(self) -> bool:
        """Open a handle to the device interface for sending IOCTLs."""
        import time

        # Wait for device interface to become available
        for attempt in range(20):
            path = self._find_device_interface()
            if path:
                try:
                    kernel32 = ctypes.windll.kernel32
                    GENERIC_READ = 0x80000000
                    GENERIC_WRITE = 0x40000000
                    FILE_SHARE_READ = 0x00000001
                    FILE_SHARE_WRITE = 0x00000002
                    OPEN_EXISTING = 3

                    self.device_handle = kernel32.CreateFileW(
                        path,
                        GENERIC_READ | GENERIC_WRITE,
                        FILE_SHARE_READ | FILE_SHARE_WRITE,
                        None,
                        OPEN_EXISTING,
                        0,
                        None,
                    )

                    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
                    if self.device_handle == INVALID_HANDLE_VALUE:
                        err = kernel32.GetLastError()
                        logger.warning(f"CreateFile failed (attempt {attempt+1}): error={err}")
                        self.device_handle = None
                    else:
                        logger.info(f"Opened device interface: {path}")
                        return True
                except Exception as e:
                    logger.warning(f"Failed to open device (attempt {attempt+1}): {e}")

            time.sleep(0.5)

        logger.error("Failed to open device interface after retries")
        return False

    def _find_device_interface(self) -> str | None:
        """Find the device interface path using SetupAPI."""
        try:
            setupapi = ctypes.WinDLL("setupapi.dll")
            cfgmgr32 = ctypes.WinDLL("cfgmgr32.dll")

            # Parse GUID
            import re
            guid_str = GUID_DEVINTERFACE_DS5Virtual.strip("{}")
            parts = guid_str.split("-")

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", wintypes.DWORD),
                    ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_byte * 8),
                ]

            guid = GUID()
            guid.Data1 = int(parts[0], 16)
            guid.Data2 = int(parts[1], 16)
            guid.Data3 = int(parts[2], 16)
            data4_hex = parts[3] + parts[4]
            for i in range(8):
                guid.Data4[i] = int(data4_hex[i*2:i*2+2], 16)

            DIGCF_PRESENT = 0x00000002
            DIGCF_DEVICEINTERFACE = 0x00000010

            hDevInfo = setupapi.SetupDiGetClassDevsW(
                ctypes.byref(guid),
                None,
                None,
                DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
            )

            INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
            if hDevInfo == INVALID_HANDLE_VALUE:
                return None

            class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("InterfaceClassGuid", GUID),
                    ("Flags", wintypes.DWORD),
                    ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
                ]

            iface_data = SP_DEVICE_INTERFACE_DATA()
            iface_data.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)

            if not setupapi.SetupDiEnumDeviceInterfaces(
                hDevInfo, None, ctypes.byref(guid), 0, ctypes.byref(iface_data)
            ):
                setupapi.SetupDiDestroyDeviceInfoList(hDevInfo)
                return None

            # Get required size
            required_size = wintypes.DWORD()
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hDevInfo, ctypes.byref(iface_data), None, 0, ctypes.byref(required_size), None
            )

            # Allocate and get detail
            class SP_DEVICE_INTERFACE_DETAIL_DATA(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("DevicePath", ctypes.c_wchar * (required_size.value // 2)),
                ]

            detail = SP_DEVICE_INTERFACE_DETAIL_DATA()
            detail.cbSize = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6

            if setupapi.SetupDiGetDeviceInterfaceDetailW(
                hDevInfo, ctypes.byref(iface_data), ctypes.byref(detail),
                required_size.value, None, None
            ):
                path = detail.DevicePath
                setupapi.SetupDiDestroyDeviceInfoList(hDevInfo)
                return path

            setupapi.SetupDiDestroyDeviceInfoList(hDevInfo)
        except Exception as e:
            logger.warning(f"SetupAPI enumeration failed: {e}")

        return None

    def send_input_report(self, report: bytes) -> bool:
        """Send an input report to the virtual controller via IOCTL."""
        if not self.device_handle:
            return False

        kernel32 = ctypes.windll.kernel32
        input_buf = (ctypes.c_byte * len(report))(*report)
        bytes_returned = wintypes.DWORD()

        result = kernel32.DeviceIoControl(
            self.device_handle,
            IOCTL_DS5_UPDATE_INPUT,
            input_buf,
            len(report),
            None,
            0,
            ctypes.byref(bytes_returned),
            None,
        )
        return bool(result)

    def read_output_report(self, timeout_ms: int = 100) -> bytes | None:
        """Read an output report from the virtual controller (blocking)."""
        if not self.device_handle:
            return None

        kernel32 = ctypes.windll.kernel32
        output_buf = (ctypes.c_byte * 48)()  # DS5 output report = 48 bytes
        bytes_returned = wintypes.DWORD()

        result = kernel32.DeviceIoControl(
            self.device_handle,
            IOCTL_DS5_READ_OUTPUT,
            None,
            0,
            output_buf,
            48,
            ctypes.byref(bytes_returned),
            None,
        )

        if result and bytes_returned.value > 0:
            return bytes(output_buf[:bytes_returned.value])
        return None

    def destroy(self):
        """Destroy the virtual device."""
        if self.device_handle:
            ctypes.windll.kernel32.CloseHandle(self.device_handle)
            self.device_handle = None

        if self.h_device:
            try:
                self.swdevice.SwDeviceClose(self.h_device)
            except Exception:
                pass
            self.h_device = None
            logger.info("Virtual DS5 device destroyed")


class DS5BridgeHost:
    """TCP server that receives controller data and feeds the virtual DS5."""

    def __init__(self, bind_ip: str = "0.0.0.0", port: int = 5555):
        self.bind_ip = bind_ip
        self.port = port
        self.virtual_device: VirtualDS5Device | None = None
        self.running = False
        self.client_sock: socket.socket | None = None

    def start(self):
        """Start the host: create virtual device and listen for connections."""
        print("=" * 60)
        print("  DS5 Bridge Host")
        print("=" * 60)

        # Step 1: Create virtual DS5 device
        print("\n[1/3] Creating virtual DualSense controller...")
        self.virtual_device = VirtualDS5Device()

        if not self.virtual_device.create():
            print("FAILED: Could not create virtual device.")
            print("Make sure the DS5Virtual driver is installed:")
            print("  pnputil /add-driver DS5Virtual.inf")
            return False

        print("  Virtual device created!")

        # Step 2: Open device interface
        print("\n[2/3] Opening device interface...")
        if not self.virtual_device.open_device_interface():
            print("FAILED: Could not open device interface.")
            self.virtual_device.destroy()
            return False

        print("  Device interface opened!")

        # Step 3: Start TCP server
        print(f"\n[3/3] Listening on {self.bind_ip}:{self.port}...")
        print("  Waiting for bridge client connection...")
        print()

        self.running = True

        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.bind_ip, self.port))
            server.listen(1)
            server.settimeout(1.0)

            while self.running:
                try:
                    client, addr = server.accept()
                    print(f"Client connected: {addr}")
                    self.client_sock = client
                    self._handle_client(client)
                    print(f"Client disconnected: {addr}")
                except socket.timeout:
                    continue

        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            self.running = False
            if self.virtual_device:
                self.virtual_device.destroy()
            server.close()

        return True

    def _handle_client(self, client: socket.socket):
        """Handle a connected bridge client."""
        client.settimeout(5.0)

        # Start output report forwarding thread
        output_thread = threading.Thread(
            target=self._output_forward_loop, args=(client,), daemon=True
        )
        output_thread.start()

        # Start keepalive thread
        keepalive_thread = threading.Thread(
            target=self._keepalive_loop, args=(client,), daemon=True
        )
        keepalive_thread.start()

        report_count = 0
        start_time = time.time()

        while self.running:
            try:
                result = read_frame(client)
            except Exception:
                break

            if result is None:
                break

            msg_type, payload = result

            if msg_type == MSG_INPUT_REPORT:
                # Forward to virtual device
                if self.virtual_device:
                    self.virtual_device.send_input_report(payload)
                    report_count += 1
                    if report_count % 1000 == 0:
                        elapsed = time.time() - start_time
                        rate = report_count / elapsed if elapsed > 0 else 0
                        print(f"  [{report_count} reports, {rate:.0f}/s]")

            elif msg_type == MSG_CONTROLLER_INFO:
                try:
                    info = json.loads(payload.decode("utf-8"))
                    print(f"  Controller: {info.get('controller_type', '?')} "
                          f"({info.get('connection_mode', '?')})")
                except Exception:
                    pass

        self.client_sock = None

    def _output_forward_loop(self, client: socket.socket):
        """Read output reports from virtual device and forward to client."""
        while self.running and self.client_sock == client:
            if not self.virtual_device:
                time.sleep(0.1)
                continue

            report = self.virtual_device.read_output_report(timeout_ms=100)
            if report:
                try:
                    client.sendall(frame_message(MSG_OUTPUT_REPORT, report))
                except Exception:
                    break

            time.sleep(0.001)  # Prevent busy loop

    def _keepalive_loop(self, client: socket.socket):
        """Send periodic keepalive pings."""
        while self.running and self.client_sock == client:
            try:
                client.sendall(frame_ping())
            except Exception:
                break
            time.sleep(5.0)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    bind_ip = "0.0.0.0"
    port = 5555

    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    if len(sys.argv) > 2:
        bind_ip = sys.argv[2]

    host = DS5BridgeHost(bind_ip=bind_ip, port=port)
    host.start()


if __name__ == "__main__":
    main()
