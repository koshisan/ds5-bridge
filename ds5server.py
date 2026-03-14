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
        self.ffmpeg_status = 'idle'
        self.ffmpeg_proc = None

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

    # --- Audio Capture (ffmpeg) ---
    def _find_ds5_speaker(self):
        """Find DualSense virtual speaker name for ffmpeg."""
        try:
            result = subprocess.run(
                ['powershell', '-Command',
                 'Get-AudioDevice -List | Where-Object { $_.Name -like "*DualSense*" -and $_.Type -eq "Playback" } | Select-Object -ExpandProperty Name'],
                capture_output=True, text=True, timeout=5)
            name = result.stdout.strip()
            if name:
                return name
        except:
            pass
        # Fallback: hardcoded name
        return "Lautsprecher (2- DualSense Wireless Controller)"

    def _build_ffmpeg_cmd(self):
        """Build ffmpeg command for WASAPI loopback capture."""
        # Use dshow audio device for loopback
        gain = self.config['gain'] / 100.0  # normalize: config 500 = ffmpeg volume=5
        return [
            'ffmpeg', '-hide_banner', '-loglevel', 'warning',
            '-f', 'dshow',
            '-audio_buffer_size', '50',
            '-i', f'audio=virtual-audio-capturer',
            '-af', f'volume={gain:.1f}',
            '-ac', '2',
            '-ar', '3000',
            '-f', 'u8',
            'pipe:1'
        ]

    def _capture_loop(self):
        PACKET_SIZE = 64  # 32 stereo samples
        SILENCE_CENTER = 128
        SILENCE_THRESHOLD = 3  # uint8 deviation from center
        target = (self.config['client_ip'], self.config['haptic_port'])
        seq = 0
        restart_delay = 1.0
        max_restart_delay = 30.0

        while self.running:
            # Try WASAPI loopback via ffmpeg
            # First try: direct WASAPI (Windows built-in)
            speaker_name = self._find_ds5_speaker()
            
            # ffmpeg WASAPI loopback capture
            cmd = [
                'ffmpeg', '-hide_banner', '-loglevel', 'warning',
                '-f', 'dshow',
                '-i', f'audio=@device_cm_{{0.0.0.00000000}}.{{*}}',
                '-af', f'volume={self.config["gain"] / 100.0:.1f}',
                '-ac', '2', '-ar', '3000', '-f', 'u8', 'pipe:1'
            ]
            
            # Simpler approach: use the virtual audio cable loopback
            # ffmpeg can capture from a specific audio device via dshow
            cmd = [
                'ffmpeg', '-hide_banner', '-loglevel', 'error',
                '-f', 'dshow',
                '-audio_buffer_size', '50',
                '-i', f'audio=CABLE Output (VB-Audio Virtual Cable)',
                '-af', f'volume={self.config["gain"] / 100.0:.1f}',
                '-ac', '2', '-ar', '3000', '-f', 'u8', 'pipe:1'
            ]

            # Actually: use PowerShell to find the loopback device
            # For now, use pyaudiowpatch just for device discovery, ffmpeg for processing
            # Simplest: pipe from pyaudiowpatch raw capture to ffmpeg for resampling
            
            # CLEANEST APPROACH: Use ffmpeg with wasapi (if available) or audiotap
            # ffmpeg on Windows doesn't have wasapi input natively
            # Use the approach: capture raw with pyaudiowpatch, pipe to ffmpeg for resample
            
            # Actually simplest: just use ffmpeg with the virtual cable loopback name
            # But we don't have a virtual cable...
            
            # OK, let's keep pyaudiowpatch for capture but pipe raw PCM to ffmpeg for resample
            self._capture_loop_hybrid(target, seq)
            
            if not self.running:
                break
                
            print(f"[DS5Server] Restarting capture in {restart_delay:.0f}s...")
            time.sleep(restart_delay)
            restart_delay = min(restart_delay * 2, max_restart_delay)

    def _capture_loop_hybrid(self, target, seq):
        """Capture via pyaudiowpatch, resample via ffmpeg subprocess."""
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            print("[DS5Server] pip install pyaudiowpatch")
            return

        p = pyaudio.PyAudio()
        ds5_lb = None
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if ('2- DualSense' in info['name'] or '2-DualSense' in info['name']) and info.get('isLoopbackDevice'):
                ds5_lb = info
                break

        if not ds5_lb:
            print("[DS5Server] DualSense loopback not found!")
            p.terminate()
            return

        channels = int(ds5_lb['maxInputChannels'])
        rate = int(ds5_lb['defaultSampleRate'])

        # Start ffmpeg for resampling: stdin=raw f32le 48kHz 2ch -> stdout=u8 3kHz 2ch
        gain = self.config['gain'] / 100.0
        ffmpeg_cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-f', 'f32le', '-ar', str(rate), '-ac', str(channels),
            '-i', 'pipe:0',
            '-af', f'volume={gain:.1f}',
            '-ac', '2', '-ar', '3000',
            '-f', 'u8',
            'pipe:1'
        ]

        print(f"[DS5Server] ffmpeg: {' '.join(ffmpeg_cmd)}")
        self.ffmpeg_status = 'starting'
        
        try:
            ffproc = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
        except FileNotFoundError:
            print("[DS5Server] ffmpeg not found! Install ffmpeg and add to PATH.")
            self.ffmpeg_status = 'error: not found'
            p.terminate()
            return

        self.ffmpeg_status = 'running'
        self.ffmpeg_proc = ffproc
        PACKET_SIZE = 64
        send_until = 0.0

        # Reader thread: read ffmpeg stdout and send UDP
        def reader():
            nonlocal seq, send_until
            while self.running and ffproc.poll() is None:
                try:
                    data = ffproc.stdout.read(PACKET_SIZE)
                    if not data or len(data) < PACKET_SIZE:
                        break

                    # Silence gate: check if any sample deviates from center
                    has_signal = any(abs(b - 128) > 3 for b in data)

                    now = time.time()
                    if has_signal:
                        send_until = now + 1.0
                        # Calculate peak for display
                        self.last_peak = max(abs(b - 128) for b in data) / 128.0

                    if now < send_until:
                        packet = bytes([0x32, seq & 0xFF]) + data
                        self.sock.sendto(packet, target)
                        seq = (seq + 1) & 0xFF
                        self.packets_sent += 1
                except Exception as e:
                    print(f"[DS5Server] Reader error: {e}")
                    break

            self.ffmpeg_status = 'stopped'

        reader_thread = threading.Thread(target=reader, daemon=True)
        reader_thread.start()

        # Writer: capture audio and feed to ffmpeg stdin
        def callback(in_data, frame_count, time_info, status):
            if not self.running:
                return (None, pyaudio.paComplete)
            try:
                if ffproc.poll() is None:
                    ffproc.stdin.write(in_data)
            except (BrokenPipeError, OSError):
                return (None, pyaudio.paComplete)
            return (None, pyaudio.paContinue)

        try:
            stream = p.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=ds5_lb['index'],
                frames_per_buffer=256,
                stream_callback=callback
            )
            stream.start_stream()
            print(f"[DS5Server] Capture active: {channels}ch {rate}Hz -> ffmpeg -> {target}")

            while self.running and stream.is_active() and ffproc.poll() is None:
                time.sleep(0.1)

            stream.stop_stream()
            stream.close()
        except Exception as e:
            print(f"[DS5Server] Capture error: {e}")
        finally:
            try:
                ffproc.stdin.close()
            except:
                pass
            ffproc.terminate()
            try:
                ffproc.wait(timeout=3)
            except:
                ffproc.kill()
            reader_thread.join(timeout=2)
            p.terminate()
            self.ffmpeg_status = 'stopped'
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
                lambda text: f'Packets: {self.packets_sent} | Peak: {self.last_peak:.3f} | ffmpeg: {self.ffmpeg_status}', None, enabled=False),
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
        self.ffmpeg_status = 'idle'
        self.ffmpeg_proc = None

    def _set_gain(self, val):
        self.config['gain'] = float(val)
        save_config(self.config)
        # Restart capture with new gain
        if self.running:
            self.stop_capture()
            time.sleep(0.5)
            self.start_capture()
            self.icon.icon = self._create_icon('green')

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
