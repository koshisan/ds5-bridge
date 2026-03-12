#!/usr/bin/env python3
"""DS5 Bridge Client - Bridges a local DualSense controller to a remote host over TCP."""

import socket
import struct
import threading
import time
import tkinter as tk
from tkinter import ttk

from ds5_hid import (
    DS5Device,
    DS5InputState,
    enumerate_ds5_devices,
    parse_input_report,
)
from protocol import (
    MSG_OUTPUT_REPORT,
    MSG_PING,
    frame_controller_info,
    frame_input_report,
    frame_pong,
    read_frame,
)

MAX_LOG_LINES = 200
MOTION_SUPPRESS_MS = 500
TRIGGER_CHANGE_THRESHOLD = 5
STICK_CHANGE_THRESHOLD = 10
RECONNECT_INTERVAL = 2.0
KEEPALIVE_INTERVAL = 5.0


class DS5BridgeClient:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("DS5 Bridge Client")
        self.root.geometry("700x600")
        self.root.minsize(600, 500)

        self.devices: list[DS5Device] = []
        self.active_device: DS5Device | None = None
        self.running = False
        self.sock: socket.socket | None = None
        self.server_connected = False

        # Previous state for change detection
        self.prev_state: DS5InputState | None = None
        self.last_motion_log_time: float = 0

        # Threads
        self.hid_thread: threading.Thread | None = None
        self.tcp_thread: threading.Thread | None = None
        self.tcp_reconnect_thread: threading.Thread | None = None

        self._build_gui()
        self._refresh_devices()

    def _build_gui(self):
        # --- Controller Selection Frame ---
        ctrl_frame = ttk.LabelFrame(self.root, text="Controller", padding=8)
        ctrl_frame.pack(fill="x", padx=10, pady=(10, 5))

        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(
            ctrl_frame, textvariable=self.device_var, state="readonly", width=50
        )
        self.device_combo.pack(side="left", fill="x", expand=True, padx=(0, 5))

        self.refresh_btn = ttk.Button(
            ctrl_frame, text="Refresh", command=self._refresh_devices
        )
        self.refresh_btn.pack(side="left")

        self.ctrl_status = ttk.Label(ctrl_frame, text="  Not Connected", foreground="gray")
        self.ctrl_status.pack(side="left", padx=(10, 0))

        # --- Server Connection Frame ---
        srv_frame = ttk.LabelFrame(self.root, text="Server", padding=8)
        srv_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(srv_frame, text="IP:").pack(side="left")
        self.ip_var = tk.StringVar(value="127.0.0.1")
        self.ip_entry = ttk.Entry(srv_frame, textvariable=self.ip_var, width=18)
        self.ip_entry.pack(side="left", padx=(2, 10))

        ttk.Label(srv_frame, text="Port:").pack(side="left")
        self.port_var = tk.StringVar(value="5555")
        self.port_entry = ttk.Entry(srv_frame, textvariable=self.port_var, width=7)
        self.port_entry.pack(side="left", padx=(2, 10))

        self.srv_status = ttk.Label(srv_frame, text="  Not Connected", foreground="gray")
        self.srv_status.pack(side="left", padx=(10, 0))

        # --- Controls Frame ---
        ctrl_btn_frame = ttk.Frame(self.root, padding=(10, 5))
        ctrl_btn_frame.pack(fill="x")

        self.start_btn = ttk.Button(
            ctrl_btn_frame, text="Start Bridge", command=self._toggle_bridge
        )
        self.start_btn.pack(side="left")

        # --- Event Log ---
        log_frame = ttk.LabelFrame(self.root, text="Event Log", padding=8)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        self.log_text = tk.Text(
            log_frame, height=20, state="disabled", wrap="word",
            font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="#d4d4d4"
        )
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _log(self, msg: str):
        """Thread-safe log to the event log widget."""
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        self.root.after(0, self._append_log, line)

    def _append_log(self, line: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line)
        # Trim to MAX_LOG_LINES
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > MAX_LOG_LINES:
            self.log_text.delete("1.0", f"{line_count - MAX_LOG_LINES}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _refresh_devices(self):
        self.devices = enumerate_ds5_devices()
        names = [d.display_name for d in self.devices]
        self.device_combo["values"] = names if names else ["No controllers found"]
        if names:
            self.device_combo.current(0)
        else:
            self.device_var.set("No controllers found")
        self._log(f"Found {len(self.devices)} controller(s)")

    def _set_ctrl_status(self, connected: bool):
        if connected:
            self.ctrl_status.config(text="  Connected", foreground="green")
        else:
            self.ctrl_status.config(text="  Not Connected", foreground="gray")

    def _set_srv_status(self, connected: bool):
        if connected:
            self.srv_status.config(text="  Connected", foreground="green")
        else:
            self.srv_status.config(text="  Not Connected", foreground="gray")

    def _toggle_bridge(self):
        if self.running:
            self._stop_bridge()
        else:
            self._start_bridge()

    def _start_bridge(self):
        # Validate device selection
        idx = self.device_combo.current()
        if idx < 0 or idx >= len(self.devices):
            self._log("ERROR: No controller selected")
            return

        device = self.devices[idx]
        if not device.open():
            self._log(f"ERROR: Failed to open {device.product_name}")
            return

        self.active_device = device
        self.running = True
        self.prev_state = None
        self.last_motion_log_time = 0

        self._log(f"Opened {device.display_name}")
        self.root.after(0, self._set_ctrl_status, True)

        # Disable controls
        self.start_btn.config(text="Stop Bridge")
        self.device_combo.config(state="disabled")
        self.refresh_btn.config(state="disabled")
        self.ip_entry.config(state="disabled")
        self.port_entry.config(state="disabled")

        # Start HID reader thread
        self.hid_thread = threading.Thread(target=self._hid_read_loop, daemon=True)
        self.hid_thread.start()

        # Start TCP connection thread
        self.tcp_reconnect_thread = threading.Thread(
            target=self._tcp_connect_loop, daemon=True
        )
        self.tcp_reconnect_thread.start()

    def _stop_bridge(self):
        self.running = False
        self._log("Stopping bridge...")

        # Close TCP
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        self.server_connected = False

        # Close HID
        if self.active_device:
            self.active_device.close()
            self.active_device = None

        self.root.after(0, self._set_ctrl_status, False)
        self.root.after(0, self._set_srv_status, False)

        # Re-enable controls
        self.start_btn.config(text="Start Bridge")
        self.device_combo.config(state="readonly")
        self.refresh_btn.config(state="normal")
        self.ip_entry.config(state="normal")
        self.port_entry.config(state="normal")

        self._log("Bridge stopped")

    def _hid_read_loop(self):
        """Background thread: read HID input reports and forward to TCP."""
        while self.running and self.active_device:
            report = self.active_device.read_input(timeout_ms=10)
            if report is None:
                continue

            # Check for device disconnection (empty read after timeout)
            if len(report) == 0:
                continue

            # Parse and log events
            is_bt = self.active_device.is_bt or False
            state = parse_input_report(report, is_bt)
            if state:
                self._detect_and_log_changes(state)
                self.prev_state = state

            # Forward to TCP if connected
            if self.server_connected and self.sock:
                try:
                    self.sock.sendall(frame_input_report(report))
                except Exception:
                    self.server_connected = False
                    self.root.after(0, self._set_srv_status, False)
                    self._log("Server connection lost (send failed)")

        # If we exited because device disconnected
        if self.running:
            self._log("Controller disconnected")
            self.root.after(0, self._set_ctrl_status, False)
            self.root.after(0, self._stop_bridge)

    def _detect_and_log_changes(self, state: DS5InputState):
        """Compare current state to previous and log significant changes."""
        prev = self.prev_state

        if prev is None:
            # First report, log battery
            self._log(f"Battery: level={state.battery_level}, status={state.battery_status}")
            return

        # Button changes
        for name in state.buttons:
            cur = state.buttons[name]
            old = prev.buttons.get(name, False)
            if cur != old:
                action = "pressed" if cur else "released"
                self._log(f"Button: {name} {action}")

        # DPad
        if state.dpad != prev.dpad:
            self._log(f"DPad: {state.dpad}")

        # Triggers (only log when change > threshold)
        if abs(state.l2_analog - prev.l2_analog) > TRIGGER_CHANGE_THRESHOLD:
            self._log(f"L2 Trigger: {state.l2_analog}")
        if abs(state.r2_analog - prev.r2_analog) > TRIGGER_CHANGE_THRESHOLD:
            self._log(f"R2 Trigger: {state.r2_analog}")

        # Sticks (only log when change > threshold)
        if (
            abs(state.lx - prev.lx) > STICK_CHANGE_THRESHOLD
            or abs(state.ly - prev.ly) > STICK_CHANGE_THRESHOLD
        ):
            self._log(f"Left Stick: ({state.lx}, {state.ly})")
        if (
            abs(state.rx - prev.rx) > STICK_CHANGE_THRESHOLD
            or abs(state.ry - prev.ry) > STICK_CHANGE_THRESHOLD
        ):
            self._log(f"Right Stick: ({state.rx}, {state.ry})")

        # Touchpad (only touch start/end)
        if state.touch0_active != prev.touch0_active:
            action = "Touch started" if state.touch0_active else "Touch ended"
            self._log(f"Touchpad 0: {action}")
        if state.touch1_active != prev.touch1_active:
            action = "Touch started" if state.touch1_active else "Touch ended"
            self._log(f"Touchpad 1: {action}")

        # Gyro/Accel - suppress continuous logging
        motion = (
            abs(state.gyro_pitch - prev.gyro_pitch) > 100
            or abs(state.gyro_yaw - prev.gyro_yaw) > 100
            or abs(state.gyro_roll - prev.gyro_roll) > 100
            or abs(state.accel_x - prev.accel_x) > 100
            or abs(state.accel_y - prev.accel_y) > 100
            or abs(state.accel_z - prev.accel_z) > 100
        )
        if motion:
            now = time.time() * 1000
            if now - self.last_motion_log_time > MOTION_SUPPRESS_MS:
                self._log("Motion detected")
                self.last_motion_log_time = now

    def _tcp_connect_loop(self):
        """Background thread: maintain TCP connection to host, with auto-reconnect."""
        ip = self.ip_var.get()
        try:
            port = int(self.port_var.get())
        except ValueError:
            self._log("ERROR: Invalid port number")
            return

        while self.running:
            if not self.server_connected:
                self._log(f"Connecting to {ip}:{port}...")
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(RECONNECT_INTERVAL)
                    s.connect((ip, port))
                    s.settimeout(5.0)
                    self.sock = s
                    self.server_connected = True
                    self.root.after(0, self._set_srv_status, True)
                    self._log(f"Connected to server {ip}:{port}")

                    # Send controller info
                    if self.active_device:
                        info_frame = frame_controller_info(
                            self.active_device.product_name,
                            self.active_device.connection_mode,
                        )
                        self.sock.sendall(info_frame)

                    # Start receiver thread
                    self.tcp_thread = threading.Thread(
                        target=self._tcp_recv_loop, daemon=True
                    )
                    self.tcp_thread.start()
                    self.tcp_thread.join()  # Wait until recv loop exits

                except (ConnectionRefusedError, OSError, TimeoutError):
                    self._log(f"Server unreachable, retrying in {RECONNECT_INTERVAL}s...")
                    self.server_connected = False
                    self.root.after(0, self._set_srv_status, False)
                    if self.sock:
                        try:
                            self.sock.close()
                        except Exception:
                            pass
                        self.sock = None

            # Wait before reconnecting
            for _ in range(int(RECONNECT_INTERVAL * 10)):
                if not self.running:
                    return
                time.sleep(0.1)

    def _tcp_recv_loop(self):
        """Receive messages from host server."""
        while self.running and self.server_connected and self.sock:
            try:
                result = read_frame(self.sock)
            except Exception:
                break

            if result is None:
                break

            msg_type, payload = result

            if msg_type == MSG_OUTPUT_REPORT:
                # Write output report to controller
                if self.active_device:
                    ok = self.active_device.write_output(payload)
                    report_len = len(payload)
                    self._log(
                        f"Output report received ({report_len} bytes) - "
                        f"{'written' if ok else 'write failed'}"
                    )
            elif msg_type == MSG_PING:
                # Respond with pong
                try:
                    if self.sock:
                        self.sock.sendall(frame_pong())
                except Exception:
                    break

        self.server_connected = False
        self.root.after(0, self._set_srv_status, False)
        if self.running:
            self._log("Server connection lost")

    def _on_close(self):
        """Handle window close."""
        if self.running:
            self._stop_bridge()
        self.root.destroy()


def main():
    root = tk.Tk()
    DS5BridgeClient(root)
    root.mainloop()


if __name__ == "__main__":
    main()
