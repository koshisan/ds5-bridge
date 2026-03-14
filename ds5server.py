"""DS5 Bridge Server - System Tray App for Gaming PC.

Manages DS5Virtual driver, audio loopback capture, and haptic forwarding.
"""
import sys
import os
import json
import time
import socket
import threading
import subprocess
import ctypes
from pathlib import Path

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    print("pip install pystray pillow")
    sys.exit(1)

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

DEFAULT_CONFIG = {
    'client_ip': '192.168.81.94',
    'haptic_port': 5556,
    'gain': 500.0,
    'threshold': 0.009,
    'buffer_size': 256,
    'autostart': False,
    'driver_enabled': True,
    'audio_driver_enabled': True,
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


class DS5Server:
    def __init__(self):
        self.config = load_config()
        self.running = False
        self.capture_thread = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.packets_sent = 0
        self.last_peak = 0.0
        self.send_until = 0.0
        self.icon = None
        self._hid_enabled = False
        self._audio_enabled = False
        self._status_lock = threading.Lock()
        self._refresh_status()

    def _refresh_status(self):
        """Refresh driver status in background."""
        def _do():
            hid = self.is_driver_enabled(DRIVER_HWID)
            audio = self.is_driver_enabled(AUDIO_HWID)
            with self._status_lock:
                self._hid_enabled = hid
                self._audio_enabled = audio
        threading.Thread(target=_do, daemon=True).start()

    # --- Driver Management ---
    def _run_elevated(self, cmd):
        """Run a command with admin privileges."""
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
            return result.returncode == 0, result.stdout + result.stderr
        except Exception as e:
            return False, str(e)

    def is_driver_enabled(self, hwid):
        try:
            result = subprocess.run(
                ['powershell', '-Command',
                 f'Get-PnpDevice | Where-Object {{ $_.HardwareID -contains "{hwid}" }} | Select-Object -ExpandProperty Status'],
                capture_output=True, text=True, timeout=5)
            status = result.stdout.strip()
            return status == 'OK'
        except:
            return False

    def _get_instance_id(self, hwid):
        try:
            result = subprocess.run(
                ['powershell', '-Command',
                 f'Get-PnpDevice | Where-Object {{ $_.HardwareID -contains "{hwid}" }} | Select-Object -ExpandProperty InstanceId'],
                capture_output=True, text=True, timeout=5)
            return result.stdout.strip()
        except:
            return None

    def enable_driver(self, hwid):
        iid = self._get_instance_id(hwid)
        if iid:
            return self._run_elevated(f'pnputil /enable-device "{iid}"')
        return False, f"Device {hwid} not found"

    def disable_driver(self, hwid):
        iid = self._get_instance_id(hwid)
        if iid:
            return self._run_elevated(f'pnputil /disable-device "{iid}"')
        return False, f"Device {hwid} not found"

    # --- Audio Capture ---
    def _find_loopback(self):
        p = pyaudio.PyAudio()
        ds5_lb = None
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if ('2- DualSense' in info['name'] or '2-DualSense' in info['name']) and info.get('isLoopbackDevice'):
                ds5_lb = info
                break
        return p, ds5_lb

    @staticmethod
    def _s16_to_u8(s16):
        """Convert signed 16-bit to unsigned 8-bit (same as Sony's conversion)."""
        return ((s16 >> 8) + 128) & 0xFF

    def _capture_loop(self):
        from scipy.signal import resample

        p, ds5_lb = self._find_loopback()
        if not ds5_lb:
            print("[DS5Server] DualSense loopback not found!")
            p.terminate()
            return

        channels = int(ds5_lb['maxInputChannels'])
        rate = int(ds5_lb['defaultSampleRate'])
        sample_buffer = bytearray()
        seq = 0
        target = (self.config['client_ip'], self.config['haptic_port'])
        target_samples = 3000  # DS5 haptic sample rate

        print(f"[DS5Server] Capture: {channels}ch {rate}Hz S16 -> {target}")

        def send_packet():
            nonlocal seq
            if len(sample_buffer) < 64:
                return
            audio_data = bytes(sample_buffer[:64])
            del sample_buffer[:64]
            packet = bytes([0x32, seq & 0xFF]) + audio_data
            self.sock.sendto(packet, target)
            seq = (seq + 1) & 0xFF
            self.packets_sent += 1

        def callback(in_data, frame_count, time_info, status):
            if not self.running:
                return (None, pyaudio.paComplete)

            # Parse S16 interleaved samples
            samples = np.frombuffer(in_data, dtype=np.int16).reshape(-1, channels)
            left = samples[:, 0].astype(np.float64)
            right = samples[:, 1].astype(np.float64) if channels >= 2 else left

            # Peak detection (normalized to 0-1 range for display)
            peak = max(np.max(np.abs(left)), np.max(np.abs(right))) / 32768.0
            self.last_peak = peak

            # Resample 48kHz -> 3kHz
            target_len = int(len(left) * target_samples / rate)
            if target_len > 0:
                left_ds = resample(left, target_len)
                right_ds = resample(right, target_len)

                # S16 -> u8 conversion (Sony style)
                for i in range(target_len):
                    l_s16 = int(np.clip(left_ds[i], -32768, 32767))
                    r_s16 = int(np.clip(right_ds[i], -32768, 32767))
                    sample_buffer.append(self._s16_to_u8(l_s16))
                    sample_buffer.append(self._s16_to_u8(r_s16))

            now = time.time()
            if peak > self.config['threshold']:
                self.send_until = now + 1.0

            if now < self.send_until:
                while len(sample_buffer) >= 64:
                    send_packet()
            else:
                sample_buffer.clear()

            return (None, pyaudio.paContinue)

        try:
            stream = p.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=ds5_lb['index'],
                frames_per_buffer=256,
                stream_callback=callback
            )
            stream.start_stream()
            while self.running and stream.is_active():
                time.sleep(0.1)
            stream.stop_stream()
            stream.close()
        except Exception as e:
            print(f"[DS5Server] Capture error: {e}")
        finally:
            p.terminate()
            print("[DS5Server] Capture stopped")

    def start_capture(self):
        if self.running:
            return
        self.running = True
        self.packets_sent = 0
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()
        print("[DS5Server] Capture started")

    def stop_capture(self):
        self.running = False
        if self.capture_thread:
            self.capture_thread.join(timeout=3)
        print("[DS5Server] Capture stopped")

    # --- Autostart ---
    def set_autostart(self, enabled):
        import winreg
        key_path = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            if enabled:
                # Use pythonw.exe for no console window
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
            print(f"[DS5Server] Autostart error: {e}")

    # --- Tray Icon ---
    def _create_icon(self, color='green'):
        img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        colors = {'green': (0, 200, 0), 'red': (200, 0, 0), 'yellow': (200, 200, 0)}
        c = colors.get(color, (128, 128, 128))
        # Draw a gamepad-like shape
        draw.rounded_rectangle([8, 16, 56, 48], radius=8, fill=c)
        draw.ellipse([16, 22, 28, 34], fill=(255, 255, 255))  # left stick
        draw.ellipse([36, 22, 48, 34], fill=(255, 255, 255))  # right stick
        return img

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem(
                lambda text: f'Client: {self.config["client_ip"]}', None, enabled=False),
            pystray.MenuItem(
                lambda text: f'Packets: {self.packets_sent} | Peak: {self.last_peak:.4f}', None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda text: 'Stop Capture' if self.running else 'Start Capture',
                self._toggle_capture),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda text: f'HID Driver [{"ON" if self._hid_enabled else "OFF"}]',
                pystray.Menu(
                    pystray.MenuItem('Enable', lambda: self._set_driver(DRIVER_HWID, True)),
                    pystray.MenuItem('Disable', lambda: self._set_driver(DRIVER_HWID, False)),
                )),
            pystray.MenuItem(
                lambda text: f'Audio Driver [{"ON" if self._audio_enabled else "OFF"}]',
                pystray.Menu(
                    pystray.MenuItem('Enable', lambda: self._set_driver(AUDIO_HWID, True)),
                    pystray.MenuItem('Disable', lambda: self._set_driver(AUDIO_HWID, False)),
                )),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Gain',
                pystray.Menu(
                    pystray.MenuItem('100', lambda: self._set_gain(100),
                        checked=lambda item: self.config['gain'] == 100),
                    pystray.MenuItem('200', lambda: self._set_gain(200),
                        checked=lambda item: self.config['gain'] == 200),
                    pystray.MenuItem('500', lambda: self._set_gain(500),
                        checked=lambda item: self.config['gain'] == 500),
                    pystray.MenuItem('1000', lambda: self._set_gain(1000),
                        checked=lambda item: self.config['gain'] == 1000),
                )),
            pystray.MenuItem('Threshold',
                pystray.Menu(
                    pystray.MenuItem('0.005', lambda: self._set_threshold(0.005),
                        checked=lambda item: self.config['threshold'] == 0.005),
                    pystray.MenuItem('0.009', lambda: self._set_threshold(0.009),
                        checked=lambda item: self.config['threshold'] == 0.009),
                    pystray.MenuItem('0.015', lambda: self._set_threshold(0.015),
                        checked=lambda item: self.config['threshold'] == 0.015),
                )),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Autostart', self._toggle_autostart,
                checked=lambda item: self.config.get('autostart', False)),
            pystray.MenuItem('Quit', self._quit),
        )

    def _toggle_capture(self):
        if self.running:
            self.stop_capture()
            self.icon.icon = self._create_icon('yellow')
        else:
            self.start_capture()
            self.icon.icon = self._create_icon('green')

    def _set_driver(self, hwid, enable):
        if enable:
            ok, msg = self.enable_driver(hwid)
        else:
            ok, msg = self.disable_driver(hwid)
        print(f"[DS5Server] {'Enable' if enable else 'Disable'} {hwid}: {ok} - {msg.strip()}")
        self._refresh_status()

    def _set_gain(self, val):
        self.config['gain'] = float(val)
        save_config(self.config)

    def _set_threshold(self, val):
        self.config['threshold'] = val
        save_config(self.config)

    def _toggle_autostart(self):
        self.set_autostart(not self.config.get('autostart', False))

    def _quit(self):
        self.stop_capture()
        self.icon.stop()

    def run(self):
        save_config(self.config)
        self.icon = pystray.Icon(
            'DS5Bridge',
            self._create_icon('yellow'),
            'DS5 Bridge Server',
            menu=self._build_menu()
        )

        # Auto-start capture
        self.start_capture()
        self.icon.icon = self._create_icon('green')

        print("[DS5Server] Tray app running")
        self.icon.run()


if __name__ == '__main__':
    server = DS5Server()
    server.run()
