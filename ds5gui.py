"""DS5 Bridge Server - GUI App with system tray."""
import sys
import os
import json
import time
import socket
import threading
import subprocess
import ctypes
from pathlib import Path

import tkinter as tk
from tkinter import ttk

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

try:
    import pyaudiowpatch as pyaudio
    import numpy as np
except ImportError:
    print("pip install pyaudiowpatch numpy scipy")
    sys.exit(1)

# --- Config ---
CONFIG_DIR = Path(os.environ.get('APPDATA', '')) / 'DS5Bridge'
CONFIG_FILE = CONFIG_DIR / 'config.json'
DRIVER_HWID = 'ROOT\\VID_054C&PID_0CE6'
AUDIO_HWID = 'ROOT\\DualSenseAudio'
DS5_SHARED_MEMORY_NAME = "Global\\DS5VirtualStatus"

DEFAULT_CONFIG = {
    'client_ip': '192.168.81.94',
    'gain': 500.0,
    'threshold': 0.009,
    'autostart': False,
    'auto_enable_hid': True,
    'auto_capture': True,
    'idle_timeout': 10,
}

def load_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            cfg = DEFAULT_CONFIG.copy()
            cfg.update(json.load(f))
            return cfg
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2)


class DS5SharedStatus(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ('version', ctypes.c_uint32),
        ('size', ctypes.c_uint32),
        ('udp_port', ctypes.c_uint16),
        ('client_ip', ctypes.c_uint8 * 4),
        ('client_port', ctypes.c_uint16),
        ('last_seen', ctypes.c_int64),
        ('packets_in', ctypes.c_uint32),
        ('packets_out', ctypes.c_uint32),
        ('driver_active', ctypes.c_uint8),
        ('reserved', ctypes.c_uint8 * 32),
    ]



class DS5Server:
    def __init__(self):
        self.config = load_config()
        self.capturing = False
        self.capture_thread = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.packets_sent = 0
        self._handoff_status = 'starting...'
        self.last_peak = 0.0
        self.send_until = 0.0
        self.wave_source = None  # last raw s16 block (pre-resample)
        self.wave_output = None  # last u8 block (post-convert)
        self.source_peak = 0.0
        self.output_peak = 0.0

    # --- Shared Memory ---
    def read_shared_status(self):
        try:
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.OpenFileMappingW.restype = ctypes.c_void_p
            kernel32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p]
            kernel32.MapViewOfFile.restype = ctypes.c_void_p
            kernel32.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
            kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

            handle = kernel32.OpenFileMappingW(0x0004, False, DS5_SHARED_MEMORY_NAME)
            if not handle:
                return None
            ptr = kernel32.MapViewOfFile(handle, 0x0004, 0, 0, ctypes.sizeof(DS5SharedStatus))
            if not ptr:
                kernel32.CloseHandle(handle)
                return None
            status = DS5SharedStatus.from_address(ptr)
            result = {
                'version': status.version,
                'udp_port': status.udp_port,
                'client_ip': f'{status.client_ip[0]}.{status.client_ip[1]}.{status.client_ip[2]}.{status.client_ip[3]}',
                'client_port': status.client_port,
                'last_seen': status.last_seen,
                'packets_in': status.packets_in,
                'packets_out': status.packets_out,
                'driver_active': bool(status.driver_active),
            }
            kernel32.UnmapViewOfFile(ptr)
            kernel32.CloseHandle(handle)
            return result
        except Exception:
            return None


    def set_disconnect(self, disconnected):
        """Set disconnect flag in shared memory."""
        try:
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.OpenFileMappingW.restype = ctypes.c_void_p
            kernel32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p]
            kernel32.MapViewOfFile.restype = ctypes.c_void_p
            kernel32.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
            kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

            handle = kernel32.OpenFileMappingW(0x000F001F, False, DS5_SHARED_MEMORY_NAME)
            if not handle:
                return False
            ptr = kernel32.MapViewOfFile(handle, 0x001F, 0, 0, ctypes.sizeof(DS5SharedStatus))
            if not ptr:
                kernel32.CloseHandle(handle)
                return False
            status = DS5SharedStatus.from_address(ptr)
            status.disconnect = 1 if disconnected else 0
            kernel32.UnmapViewOfFile(ptr)
            kernel32.CloseHandle(handle)
            print(f"[DS5] Disconnect flag set to {disconnected}")
            return True
        except Exception as e:
            print(f"[DS5] set_disconnect error: {e}")
            return False

    # --- Port Listener ---
    def _start_listener(self):
        """Background thread: grab UDP port when driver is off, release when driver is on."""
        self._listener_running = True
        self._standby_sock = None
        t = threading.Thread(target=self._listener_loop, daemon=True)
        t.start()

    def _listener_loop(self):
        port = 5555
        while getattr(self, '_listener_running', False):
            hid_on = self.is_driver_enabled(DRIVER_HWID)

            if hid_on:
                # Driver loaded — release port if we have it
                if self._standby_sock:
                    print("[DS5] Driver active, releasing port")
                    self._standby_sock.close()
                    self._standby_sock = None
                self._handoff_status = 'port -> driver'
                time.sleep(2)
                continue

            # Driver not loaded — grab port if we don't have it
            if not self._standby_sock:
                try:
                    self._standby_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    self._standby_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    self._standby_sock.settimeout(2.0)
                    self._standby_sock.bind(('0.0.0.0', port))
                    print(f"[DS5] Standby: grabbed port {port}")
                except OSError as e:
                    self._standby_sock = None
                    if e.errno == 10048:
                        self._handoff_status = f'port {port} in use, retry...'
                    else:
                        self._handoff_status = f'bind error: {e}'
                    time.sleep(2)
                    continue

            # Listen for client
            self._handoff_status = f'listening :{port}'
            try:
                data, addr = self._standby_sock.recvfrom(1024)
                if len(data) >= 64:
                    print(f"[DS5] Client {addr[0]}:{addr[1]} detected!")
                    self._on_client_detected()
            except socket.timeout:
                pass
            except Exception as e:
                print(f"[DS5] Listen error: {e}")
                self._standby_sock.close()
                self._standby_sock = None
                time.sleep(1)

    def _on_client_detected(self):
        """Client packet received — auto-enable things as configured."""
        # Clear disconnect flag
        self.set_disconnect(False)

        # Release port first
        if self._standby_sock:
            self._standby_sock.close()
            self._standby_sock = None
        time.sleep(0.3)

        if self.config.get('auto_enable_hid', True):
            if not self.is_driver_enabled(DRIVER_HWID):
                print("[DS5] Auto-enabling HID driver...")
                self._handoff_status = 'loading HID driver...'
                self.enable_driver(DRIVER_HWID)
                time.sleep(2)

        if self.config.get('auto_capture', True):
            if not self.capturing:
                print("[DS5] Auto-starting capture...")
                self.start_capture()

    # --- Idle Monitor ---
    def _start_idle_monitor(self):
        self._idle_running = True
        t = threading.Thread(target=self._idle_loop, daemon=True)
        t.start()

    def _idle_loop(self):
        timeout = self.config.get('idle_timeout', 10)
        while getattr(self, '_idle_running', False):
            shared = self.read_shared_status()
            if shared and shared['driver_active'] and shared['packets_in'] > 0:
                now_ms = int(time.time() * 1000)
                idle_s = (now_ms - shared['last_seen']) / 1000.0

                if idle_s > timeout:
                    self._handoff_status = f'idle {idle_s:.0f}s, waiting to unload...'

                    if self.config.get('auto_capture', True) and self.capturing:
                        print("[DS5] Auto-stopping capture (idle)")
                        self.stop_capture()

                    if self.config.get('auto_enable_hid', True):
                        self.set_disconnect(True)
                        time.sleep(1)
                        ok, msg = self.disable_driver(DRIVER_HWID)
                        if ok:
                            print(f"[DS5] HID driver unloaded after {idle_s:.0f}s idle")
                            self._handoff_status = 'standby (unloaded)'
                        else:
                            print(f"[DS5] Virtual disconnect (driver busy)")
                            self._handoff_status = 'disconnected (device busy)'
                            time.sleep(5)
                else:
                    self._handoff_status = f'active, idle {idle_s:.0f}s / {timeout}s'
            time.sleep(1)

    # --- Driver Management ---
    def _get_instance_id(self, hwid):
        try:
            result = subprocess.run(
                ['powershell', '-Command',
                 f'Get-PnpDevice | Where-Object {{ $_.HardwareID -contains "{hwid}" }} | Select-Object -ExpandProperty InstanceId'],
                capture_output=True, text=True, errors='replace', timeout=5)
            return result.stdout.strip()
        except:
            return None

    def is_driver_enabled(self, hwid):
        try:
            result = subprocess.run(
                ['powershell', '-Command',
                 f'Get-PnpDevice | Where-Object {{ $_.HardwareID -contains "{hwid}" }} | Select-Object -ExpandProperty Status'],
                capture_output=True, text=True, errors='replace', timeout=5)
            return result.stdout.strip() == 'OK'
        except:
            return False

    def enable_driver(self, hwid):
        iid = self._get_instance_id(hwid)
        if iid:
            r = subprocess.run(f'pnputil /enable-device "{iid}"', capture_output=True, text=True, errors='replace', shell=True)
            return r.returncode == 0, r.stdout + r.stderr
        return False, "Not found"

    def disable_driver(self, hwid):
        iid = self._get_instance_id(hwid)
        if iid:
            r = subprocess.run(f'pnputil /disable-device "{iid}"', capture_output=True, text=True, errors='replace', shell=True)
            return r.returncode == 0, r.stdout + r.stderr
        return False, "Not found"

    # --- Audio Capture ---
    @staticmethod
    def _s16_to_u8(s16):
        return ((s16 >> 8) + 128) & 0xFF

    def _capture_loop(self):
        p = pyaudio.PyAudio()
        ds5_lb = None
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if ('2- DualSense' in info['name'] or '2-DualSense' in info['name']) and info.get('isLoopbackDevice'):
                ds5_lb = info
                break

        if not ds5_lb:
            print("[DS5] DualSense loopback not found!")
            p.terminate()
            return

        channels = int(ds5_lb['maxInputChannels'])
        rate = int(ds5_lb['defaultSampleRate'])
        seq = [0]
        target = None
        target_check = 0.0

        self._capture_info = f"Loopback: {channels}ch S16 {rate}Hz | Haptic: ch{'3+4' if channels >= 4 else '1+2'} | Streaming raw S16 to client"
        print(f"[DS5] Capture: {channels}ch {rate}Hz S16 -> streaming raw to client")

        def callback(in_data, frame_count, time_info, status):
            if not self.capturing:
                return (None, pyaudio.paComplete)
            samples = np.frombuffer(in_data, dtype=np.int16).reshape(-1, channels)
            if channels >= 4:
                left = samples[:, 2]
                right = samples[:, 3]
            else:
                left = samples[:, 0]
                right = samples[:, 1] if channels >= 2 else samples[:, 0]

            peak = max(int(np.max(np.abs(left))), int(np.max(np.abs(right)))) / 32768.0
            self.last_peak = peak

            # Waveform capture (raw s16)
            self.wave_source = [int(v) for v in left[:32]]
            self.source_peak = peak

            now = time.time()
            if peak > self.config['threshold']:
                self.send_until = now + 1.0

            # Update target from shared memory (every ~1s, not every callback)
            nonlocal target, target_check
            if now - target_check > 1.0:
                shared = self.read_shared_status()
                if shared and shared['client_port'] > 0:
                    target = (shared['client_ip'], shared['client_port'])
                target_check = now

            if now < self.send_until and target:
                # Stream raw s16 stereo (ch3+4) to client
                # Packet: [0x40, seq, s16_L0, s16_R0, s16_L1, s16_R1, ...]
                import struct as st
                raw = bytearray(2 + len(left) * 4)
                raw[0] = 0x40
                raw[1] = seq[0] & 0xFF
                seq[0] = (seq[0] + 1) & 0xFF
                offset = 2
                for i in range(len(left)):
                    st.pack_into('<hh', raw, offset, int(left[i]), int(right[i]))
                    offset += 4
                self.sock.sendto(bytes(raw), target)
                self.packets_sent += 1

                # Output waveform = just the raw s16 (conversion happens at client now)
                self.wave_output = [int(v) for v in left[:32]]
                self.output_peak = peak
            else:
                self.wave_output = None
                self.output_peak = 0.0

            return (None, pyaudio.paContinue)

        try:
            stream = p.open(format=pyaudio.paInt16, channels=channels, rate=rate,
                          input=True, input_device_index=ds5_lb['index'],
                          frames_per_buffer=256, stream_callback=callback)
            stream.start_stream()
            while self.capturing and stream.is_active():
                time.sleep(0.1)
            stream.stop_stream()
            stream.close()
        except Exception as e:
            print(f"[DS5] Capture error: {e}")
        finally:
            p.terminate()
            print("[DS5] Capture stopped")

    def start_capture(self):
        if self.capturing:
            return
        self.capturing = True
        self.packets_sent = 0
        self._handoff_status = 'starting...'
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()

    def stop_capture(self):
        self.capturing = False
        if self.capture_thread:
            self.capture_thread.join(timeout=3)

    # --- Autostart ---
    def set_autostart(self, enabled):
        import winreg
        key_path = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            if enabled:
                exe = sys.executable.replace('python.exe', 'pythonw.exe')
                script = os.path.abspath(__file__)
                winreg.SetValueEx(key, 'DS5Bridge', 0, winreg.REG_SZ, f'"{exe}" "{script}"')
            else:
                try:
                    winreg.DeleteValue(key, 'DS5Bridge')
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
            self.config['autostart'] = enabled
            save_config(self.config)
        except Exception as e:
            print(f"[DS5] Autostart error: {e}")



class DS5GUI:
    def __init__(self):
        self.server = DS5Server()
        self.root = tk.Tk()
        self.root.title("DS5 Bridge Server")
        self.root.geometry("580x520")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._update_loop()

        # Startup check: disable HID driver if no client connected
        if self.server.config.get('auto_enable_hid', True):
            shared = self.server.read_shared_status()
            if shared and shared['driver_active'] and shared['packets_in'] == 0:
                print("[DS5] No client at startup, disabling HID driver...")
                self.server.disable_driver(DRIVER_HWID)
            elif shared and shared['driver_active'] and shared['packets_in'] > 0:
                now_ms = int(time.time() * 1000)
                idle_s = (now_ms - shared['last_seen']) / 1000.0
                if idle_s > self.server.config.get('idle_timeout', 10):
                    print(f"[DS5] Client idle {idle_s:.0f}s at startup, disabling HID driver...")
                    self.server.set_disconnect(True)
                    time.sleep(1)
                    self.server.disable_driver(DRIVER_HWID)

        # Start port listener and idle monitor
        self.server._start_listener()
        self.server._start_idle_monitor()

        # Auto-start capture only if HID driver is active
        if self.server.config.get('auto_capture', True) and self.server.is_driver_enabled(DRIVER_HWID):
            self.server.start_capture()

    def _build_ui(self):
        # --- Status Frame ---
        status_frame = ttk.LabelFrame(self.root, text="Status", padding=10)
        status_frame.pack(fill='x', padx=10, pady=5)

        self.lbl_capture = ttk.Label(status_frame, text="Capture: OFF")
        self.lbl_capture.grid(row=0, column=0, sticky='w')

        self.lbl_packets = ttk.Label(status_frame, text="Packets: 0")
        self.lbl_packets.grid(row=0, column=1, sticky='w', padx=20)

        self.lbl_peak = ttk.Label(status_frame, text="Peak: 0.000")
        self.lbl_peak.grid(row=0, column=2, sticky='w')

        self.lbl_capture_info = ttk.Label(status_frame, text="", foreground='gray')
        self.lbl_capture_info.grid(row=1, column=0, sticky='w', columnspan=3)

        # --- Driver Shared Memory ---
        driver_frame = ttk.LabelFrame(self.root, text="Driver (Shared Memory)", padding=10)
        driver_frame.pack(fill='x', padx=10, pady=5)

        self.lbl_driver = ttk.Label(driver_frame, text="Driver: checking...")
        self.lbl_driver.grid(row=0, column=0, sticky='w', columnspan=2)

        self.lbl_handoff = ttk.Label(driver_frame, text="Handoff: -", foreground='gray')
        self.lbl_handoff.grid(row=3, column=0, sticky='w', columnspan=2)

        self.lbl_client = ttk.Label(driver_frame, text="Client: -")
        self.lbl_client.grid(row=1, column=0, sticky='w', columnspan=2)

        self.lbl_driver_pkts = ttk.Label(driver_frame, text="In: 0 | Out: 0")
        self.lbl_driver_pkts.grid(row=2, column=0, sticky='w', columnspan=2)

        # --- Drivers ---
        drv_frame = ttk.LabelFrame(self.root, text="Drivers", padding=10)
        drv_frame.pack(fill='x', padx=10, pady=5)

        # HID Driver
        ttk.Label(drv_frame, text="HID (DS5Virtual)", font=('', 9, 'bold')).grid(row=0, column=0, sticky='w', columnspan=3)
        self.lbl_hid = ttk.Label(drv_frame, text="Status: checking...")
        self.lbl_hid.grid(row=1, column=0, sticky='w')
        self.lbl_hid_info = ttk.Label(drv_frame, text="", foreground='gray')
        self.lbl_hid_info.grid(row=2, column=0, sticky='w', columnspan=3)
        ttk.Button(drv_frame, text="Enable", width=8,
                   command=lambda: self._driver_action(DRIVER_HWID, True)).grid(row=1, column=1, padx=5)
        ttk.Button(drv_frame, text="Disable", width=8,
                   command=lambda: self._driver_action(DRIVER_HWID, False)).grid(row=1, column=2)
        self.auto_hid_var = tk.BooleanVar(value=self.server.config.get('auto_enable_hid', True))
        ttk.Checkbutton(drv_frame, text="Auto-Enable", variable=self.auto_hid_var,
                       command=lambda: self._save_auto('auto_enable_hid', self.auto_hid_var.get())).grid(row=1, column=3, padx=10)

        ttk.Separator(drv_frame, orient='horizontal').grid(row=3, column=0, columnspan=4, sticky='ew', pady=5)

        # Audio Driver
        ttk.Label(drv_frame, text="Audio (DualSense Speaker)", font=('', 9, 'bold')).grid(row=4, column=0, sticky='w', columnspan=3)
        self.lbl_audio = ttk.Label(drv_frame, text="Status: checking...")
        self.lbl_audio.grid(row=5, column=0, sticky='w')
        self.lbl_audio_info = ttk.Label(drv_frame, text="", foreground='gray')
        self.lbl_audio_info.grid(row=6, column=0, sticky='w', columnspan=3)
        ttk.Button(drv_frame, text="Enable", width=8,
                   command=lambda: self._driver_action(AUDIO_HWID, True)).grid(row=5, column=1, padx=5)
        ttk.Button(drv_frame, text="Disable", width=8,
                   command=lambda: self._driver_action(AUDIO_HWID, False)).grid(row=5, column=2)


        # --- Capture ---
        cap_frame = ttk.LabelFrame(self.root, text="Haptic Capture", padding=10)
        cap_frame.pack(fill='x', padx=10, pady=5)

        self.btn_capture = ttk.Button(cap_frame, text="Stop Capture", command=self._toggle_capture)
        self.btn_capture.grid(row=0, column=0)

        ttk.Label(cap_frame, text="Threshold:").grid(row=0, column=1, padx=(20, 5))
        self.threshold_var = tk.StringVar(value=str(self.server.config['threshold']))
        ttk.Combobox(cap_frame, textvariable=self.threshold_var, values=['0.005', '0.009', '0.015'],
                     width=6, state='readonly').grid(row=0, column=2)
        self.threshold_var.trace_add('write', lambda *a: self._update_threshold())

        self.auto_capture_var = tk.BooleanVar(value=self.server.config.get('auto_capture', True))
        ttk.Checkbutton(cap_frame, text="Auto-Start", variable=self.auto_capture_var,
                       command=lambda: self._save_auto('auto_capture', self.auto_capture_var.get())).grid(row=0, column=3, padx=10)

        # --- Bottom ---
        bot_frame = ttk.Frame(self.root, padding=10)
        bot_frame.pack(fill='x', padx=10)

        self.autostart_var = tk.BooleanVar(value=self.server.config.get('autostart', False))
        ttk.Checkbutton(bot_frame, text="Start with Windows", variable=self.autostart_var,
                       command=self._toggle_autostart).pack(side='left')
        ttk.Button(bot_frame, text="Quit", command=self._quit).pack(side='right')

    def _update_loop(self):
        """Periodic UI update."""
        # Capture status
        if self.server.capturing:
            self.lbl_capture.config(text="Capture: ON", foreground='green')
            self.btn_capture.config(text="Stop Capture")
        else:
            self.lbl_capture.config(text="Capture: OFF", foreground='red')
            self.btn_capture.config(text="Start Capture")

        self.lbl_packets.config(text=f"Packets: {self.server.packets_sent}")
        self.lbl_peak.config(text=f"Peak: {self.server.last_peak:.3f}")

        # Capture format info
        if hasattr(self.server, '_capture_info'):
            self.lbl_capture_info.config(text=self.server._capture_info)

        # Shared memory
        try:
            shared = self.server.read_shared_status()
            if shared and shared['driver_active']:
                self.lbl_driver.config(text="Driver: ACTIVE", foreground='green')
                self.lbl_client.config(text=f"Client: {shared['client_ip']}:{shared['client_port']}")
                self.lbl_driver_pkts.config(text=f"In: {shared['packets_in']} | Out: {shared['packets_out']}")
            else:
                self.lbl_driver.config(text="Driver: inactive", foreground='gray')
                self.lbl_client.config(text="Client: -")
                self.lbl_driver_pkts.config(text="In: 0 | Out: 0")
        except Exception:
            self.lbl_driver.config(text="Driver: no shared memory", foreground='gray')

        # Handoff status
        if hasattr(self.server, '_handoff_status'):
            self.lbl_handoff.config(text=f"Handoff: {self.server._handoff_status}")

        # Refresh driver status every 5 seconds
        self._update_count = getattr(self, '_update_count', 0) + 1
        if self._update_count % 5 == 0:
            self._refresh_drivers()

        self.root.after(1000, self._update_loop)


    def _driver_action(self, hwid, enable):
        def do():
            if enable:
                ok, msg = self.server.enable_driver(hwid)
            else:
                ok, msg = self.server.disable_driver(hwid)
            print(f"[DS5] {'Enable' if enable else 'Disable'} {hwid}: {ok}")
            self._refresh_drivers()
        threading.Thread(target=do, daemon=True).start()

    def _save_auto(self, key, val):
        self.server.config[key] = val
        save_config(self.server.config)

    def _get_driver_details(self, hwid):
        """Get driver name, version, date via PowerShell."""
        try:
            result = subprocess.run(
                ['powershell', '-Command',
                 f'Get-PnpDevice | Where-Object {{ $_.HardwareID -contains "{hwid}" }} | Select-Object Status, FriendlyName, InstanceId | ConvertTo-Json'],
                capture_output=True, text=True, errors='replace', timeout=8)
            info = json.loads(result.stdout) if result.stdout.strip() else {}
            name = info.get('FriendlyName', '?')
            status = info.get('Status', '?')
            iid = info.get('InstanceId', '')

            # Get driver version + date
            ver_result = subprocess.run(
                ['powershell', '-Command',
                 f'Get-PnpDeviceProperty -InstanceId "{iid}" -KeyName DEVPKEY_Device_DriverVersion, DEVPKEY_Device_DriverDate 2>$null | Select-Object KeyName, Data | ConvertTo-Json'],
                capture_output=True, text=True, errors='replace', timeout=8)
            props = json.loads(ver_result.stdout) if ver_result.stdout.strip() else []
            if not isinstance(props, list):
                props = [props]
            version = '?'
            date = '?'
            for p in props:
                if 'DriverVersion' in p.get('KeyName', ''):
                    version = p.get('Data', '?')
                if 'DriverDate' in p.get('KeyName', ''):
                    d = str(p.get('Data', ''))
                    if '/Date(' in d:
                        import re
                        m = re.search(r'/Date\((\d+)', d)
                        if m:
                            from datetime import datetime
                            date = datetime.fromtimestamp(int(m.group(1)) / 1000).strftime('%Y-%m-%d')
                    elif d:
                        date = d[:10]
            return status, name, version, date
        except Exception:
            return '?', '?', '?', '?'

    def _refresh_drivers(self):
        def do():
            for hwid, lbl_status, lbl_info in [
                (DRIVER_HWID, self.lbl_hid, self.lbl_hid_info),
                (AUDIO_HWID, self.lbl_audio, self.lbl_audio_info),
            ]:
                try:
                    status, name, version, date = self._get_driver_details(hwid)
                    is_on = status == 'OK'
                    self.root.after(0, lambda l=lbl_status, s=is_on: l.config(
                        text=f"Status: {'ON' if s else 'OFF'}",
                        foreground='green' if s else 'red'))
                    self.root.after(0, lambda l=lbl_info, n=name, v=version, d=date: l.config(
                        text=f"{n}  |  v{v}  |  {d}"))
                except Exception:
                    self.root.after(0, lambda l=lbl_status: l.config(text="Status: ?", foreground='gray'))
                    self.root.after(0, lambda l=lbl_info: l.config(text="(query failed)"))
        threading.Thread(target=do, daemon=True).start()

    def _toggle_capture(self):
        if self.server.capturing:
            self.server.stop_capture()
        else:
            self.server.start_capture()

    def _update_threshold(self):
        try:
            self.server.config['threshold'] = float(self.threshold_var.get())
            save_config(self.server.config)
        except ValueError:
            pass

    def _toggle_autostart(self):
        self.server.set_autostart(self.autostart_var.get())

    def _on_close(self):
        self.root.withdraw()  # Hide instead of close

    def _quit(self):
        self.server.stop_capture()
        self.root.destroy()

    def run(self):
        self._refresh_drivers()
        self.root.mainloop()




if __name__ == '__main__':
    try:
        gui = DS5GUI()
        gui.run()
    except Exception as e:
        import traceback
        traceback.print_exc()
        input("Press Enter to exit...")

