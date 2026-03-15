"""DS5 Bridge Client - GUI App with tabbed interface + tray icon."""
import sys
import os
import json
import time
import socket
import struct
import threading
import zlib
import queue
from pathlib import Path
from datetime import datetime

import tkinter as tk
from tkinter import ttk, scrolledtext

try:
    import hid
except ImportError:
    print("pip install hidapi")
    sys.exit(1)

# --- Constants ---
DS5_VID = 0x054C
# Feature reports that don't work over USB (need static fallback from driver)
USB_UNSUPPORTED_FEATURES = {0x08, 0x80, 0x82, 0xF0}
DS5_PIDS = {0x0CE6, 0x0DF2}
DEFAULT_PORT = 5555
USB_REPORT_SIZE = 64

CONFIG_DIR = Path(os.environ.get('APPDATA', '')) / 'DS5Bridge'
CONFIG_FILE = CONFIG_DIR / 'client_config.json'

DEFAULT_CONFIG = {
    'server_host': '192.168.81.88',
    'server_port': 5555,
    'client_port': 0,  # 0 = random ephemeral port
    'protocol': 'udp',  # udp or tcp
    'haptic_gain': 2.0,
    'haptic_mode': 'raw',  # raw or resample
    'haptic_timed': True,
    'autostart': False,
    'debug_output_reports': False,
}

# --- CRC ---
def ds5_bt_crc32(data):
    return zlib.crc32(bytes([0xA2]) + data) & 0xFFFFFFFF

def ds5_crc32_payload(seed_bytes, data_bytes):
    return zlib.crc32(bytes(seed_bytes) + bytes(data_bytes)) & 0xFFFFFFFF



# --- Output Report Decoder ---
_FLAG0_BITS = [
    (0x01, 'Motor(graceful)'),
    (0x02, 'Motor(instant)'),
    (0x04, 'RightTrigger'),
    (0x08, 'LeftTrigger'),
    (0x10, 'HeadphoneVol'),
    (0x20, 'SpeakerToggle'),
    (0x40, 'MicVol'),
    (0x80, 'MicToggle'),
]
_FLAG1_BITS = [
    (0x01, 'MicLED'),
    (0x02, 'AudioMute'),
    (0x04, 'Lightbar'),
    (0x08, 'AllLEDsOff'),
    (0x10, 'PlayerLEDs'),
    (0x20, 'Unk0x20'),
    (0x40, 'MotorPower'),
    (0x80, 'Unk0x80'),
]
_TRIGGER_MODES = {
    0x00: 'Off', 0x01: 'Resistance', 0x02: 'Section',
    0x05: 'Disengage', 0x06: 'Vibrate', 0x21: 'Calibrate',
    0x22: 'Unk0x22', 0x25: 'VibResist', 0x26: 'VibSection',
    0xFC: 'Debug',
}

def decode_output_report(data):
    """Decode USB output report (0x02) into human-readable fields."""
    if len(data) < 48:
        return f'[OUTPUT] Too short ({len(data)}B): {data[:20].hex(" ")}'

    # data[0]=0x02, data[1]=flag0, data[2]=flag1
    f0, f1 = data[1], data[2]

    flags0 = '+'.join(name for bit, name in _FLAG0_BITS if f0 & bit) or 'None'
    flags1 = '+'.join(name for bit, name in _FLAG1_BITS if f1 & bit) or 'None'

    parts = [f'Flags: [{flags0}] [{flags1}]']

    # Motors
    if f0 & 0x03:
        parts.append(f'Motor R={data[3]} L={data[4]}')

    # Right trigger (bytes 11-21)
    if f0 & 0x04:
        mode = data[11]
        mode_name = _TRIGGER_MODES.get(mode, f'0x{mode:02X}')
        params = data[12:22]
        parts.append(f'RTrig: {mode_name} P={params.hex()}')

    # Left trigger (bytes 22-32)
    if f0 & 0x08:
        mode = data[22]
        mode_name = _TRIGGER_MODES.get(mode, f'0x{mode:02X}')
        params = data[23:33]
        parts.append(f'LTrig: {mode_name} P={params.hex()}')

    # Audio
    if f0 & 0x10:
        parts.append(f'HeadVol={data[5]}')
    if f0 & 0x20 or f0 & 0x40:
        parts.append(f'SpkVol={data[6]} MicVol={data[7]}')

    # Mic LED
    if f1 & 0x01:
        parts.append(f'MicLED={data[9]}')

    # Mute
    if f1 & 0x02:
        m = data[10]
        mute = []
        if m & 0x10: mute.append('Mic')
        if m & 0x40: mute.append('Audio')
        parts.append(f'Mute={"+".join(mute) if mute else "None"}')

    # Motor/effect power reduction
    if f1 & 0x40:
        p = data[37]
        parts.append(f'Power: Motor={p & 0x0F}/7 Trigger={p >> 4}/7')

    # Player LEDs
    if f1 & 0x10:
        parts.append(f'PlayerLED=0x{data[44]:02X} Bright={data[43]}')

    # Lightbar
    if f1 & 0x04:
        parts.append(f'Light=({data[45]},{data[46]},{data[47]})')

    return ' | '.join(parts)


# --- Config ---
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


# --- DS5 Hardware Info ---
def read_ds5_info(dev, is_bt):
    """Read hardware info from DS5 via feature reports."""
    info = {}

    # 0x20: Firmware/build date
    try:
        r = dev.get_feature_report(0x20, 64)
        if r and len(r) >= 16:
            info['firmware_date'] = bytes(r[1:17]).decode('ascii', errors='replace').strip('\x00')
    except: pass

    # 0x09: MAC address
    try:
        r = dev.get_feature_report(0x09, 64)
        if r and len(r) >= 7:
            mac = ':'.join(f'{b:02X}' for b in r[1:7])
            info['mac'] = mac
    except: pass

    # 0x22: Hardware info
    try:
        r = dev.get_feature_report(0x22, 64)
        if r and len(r) >= 4:
            info['hw_version'] = f'{r[1]}.{r[2]}.{r[3]}'
    except: pass

    # 0x80/0x81 subcommands
    subcmds = [
        (0x01, 0x13, 'serial'),
        (0x09, 0x02, 'pcba_mac'),
        (0x01, 0x18, 'battery_barcode'),
        (0x01, 0x1a, 'vcm_barcode_l'),
        (0x01, 0x1c, 'vcm_barcode_r'),
        (0x01, 0x15, 'board_info'),
    ]
    for sub1, sub2, key in subcmds:
        try:
            if is_bt:
                payload = bytearray(64)
                payload[0] = 0x80
                payload[1] = sub1
                payload[2] = sub2
                crc = ds5_crc32_payload([0x53, 0x80], payload[1:60])
                struct.pack_into('<I', payload, 60, crc)
                dev.send_feature_report(bytes(payload))
            else:
                # USB: no CRC needed
                payload = bytearray(64)
                payload[0] = 0x80
                payload[1] = sub1
                payload[2] = sub2
                dev.send_feature_report(bytes(payload))
            time.sleep(0.03)
            resp = dev.get_feature_report(0x81, 64)
            if resp and len(resp) >= 5 and resp[1] == sub1 and resp[2] == sub2:
                data = bytes(resp[4:])
                if key in ('serial', 'battery_barcode', 'vcm_barcode_l', 'vcm_barcode_r'):
                    info[key] = data.split(b'\x00')[0].decode('ascii', errors='replace')
                elif key == 'board_info':
                    info['board_version'] = f'{data[0]}.{data[1]}'
                    info['color_id'] = f'0x{data[3]:02X}{data[2]:02X}'
                elif key == 'pcba_mac':
                    info['pcba_mac'] = ':'.join(f'{b:02X}' for b in data[:6])
        except: pass

    return info


class DS5Client:
    """Core client logic — manages HID device, UDP socket, and bridge threads."""

    def __init__(self, log_callback=None):
        self.log_cb = log_callback or (lambda msg: print(msg))
        self.config = load_config()

        # State
        self.dev = None
        self.dev_info = None
        self.hw_info = {}
        self.is_bt = False
        self.connected = False
        self.running = False
        self.sock = None
        self._tcp_sock = None
        self._is_tcp = False
        self.server_alive = False
        self._last_server_rx = 0

        # Stats
        self.packets_sent = 0
        self.packets_recv = 0  # output reports from server
        self.features_handled = 0
        self.send_rate = 0.0
        self._rate_count = 0
        self._rate_time = time.monotonic()
        self.haptic_peak = 0.0
        self.haptic_peak_hold = 0.0
        self._haptic_peak_time = 0.0
        self.haptic_count = 0
        self.haptic_waveform = None
        self.haptic_input_peak = 0.0

    def log(self, msg):
        ts = datetime.now().strftime('%H:%M:%S')
        self.log_cb(f'[{ts}] {msg}')

    def find_and_open(self):
        """Find DS5 and open HID device."""
        for info in hid.enumerate(DS5_VID):
            if info['product_id'] in DS5_PIDS:
                self.dev_info = info
                break
        else:
            return False, 'DualSense not found'

        name = 'DualSense Edge' if self.dev_info['product_id'] == 0x0DF2 else 'DualSense'
        self.dev = hid.device()
        try:
            self.dev.open_path(self.dev_info['path'])
        except Exception as e:
            self.dev = None
            return False, f'Failed to open {name}: {e}'

        test = self.dev.read(128, 1000)
        if not test:
            self.dev.close()
            self.dev = None
            return False, 'No data from controller'

        self.is_bt = len(test) > 64
        conn = 'Bluetooth' if self.is_bt else 'USB'
        self.log(f'{name} opened ({conn}, {len(test)}B reports)')

        # Read hardware info
        self.log('Reading hardware info...')
        self.hw_info = read_ds5_info(self.dev, self.is_bt)
        self.connected = True
        return True, f'{name} ({conn})'

    def start(self):
        """Start bridge threads."""
        if not self.dev or self.running:
            return

        host = self.config['server_host']
        port = self.config['server_port']
        client_port = self.config.get('client_port', 0)
        proto = self.config.get('protocol', 'udp')

        # Input is always UDP
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('0.0.0.0', client_port))
        self.target = (host, port)

        # Return channel: UDP (same socket) or TCP (separate connection)
        self._is_tcp = (proto == 'tcp')
        self._tcp_sock = None
        if self._is_tcp:
            try:
                self._tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._tcp_sock.settimeout(3.0)
                self._tcp_sock.connect((host, port))
                self._tcp_sock.settimeout(None)
                self.log(f'TCP return channel connected to {host}:{port}')
            except Exception as e:
                self.log(f'TCP return channel failed: {e} - falling back to UDP')
                self._tcp_sock = None
                self._is_tcp = False
        self.running = True
        self.server_alive = False
        self._last_server_rx = 0
        self.packets_sent = 0
        self.packets_recv = 0
        self.features_handled = 0

        local_port = self.sock.getsockname()[1]
        self.log(f'Bridge started -> {host}:{port} (local :{local_port})')

        # Output receiver thread
        self._out_thread = threading.Thread(target=self._output_loop, daemon=True)
        self._out_thread.start()

        # Input sender thread
        self._in_thread = threading.Thread(target=self._input_loop, daemon=True)
        self._in_thread.start()

    def stop(self):
        """Stop bridge."""
        self.running = False
        self._haptic_sender_running = False
        if self._tcp_sock:
            try: self._tcp_sock.close()
            except: pass
            self._tcp_sock = None
        if self.sock:
            try: self.sock.close()
            except: pass
            self.sock = None
        self.log('Bridge stopped')

    def disconnect(self):
        """Disconnect from DS5."""
        if self._usb_audio_stream:
            try:
                self._usb_audio_stream.stop_stream()
                self._usb_audio_stream.close()
            except: pass
            self._usb_audio_stream = None
        if self._usb_audio_pa:
            try: self._usb_audio_pa.terminate()
            except: pass
            self._usb_audio_pa = None
        self.stop()
        if self.dev:
            try: self.dev.close()
            except: pass
            self.dev = None
        self.connected = False
        self.hw_info = {}
        self.log('DS5 disconnected')

    def _try_reconnect(self):
        """Auto-reconnect to DS5 in background."""
        def loop():
            attempt = 0
            while self.running and not self.connected:
                attempt += 1
                time.sleep(2)
                if self.dev:
                    try: self.dev.close()
                    except: pass
                    self.dev = None
                self.log(f'Reconnecting DS5... (attempt {attempt})')
                ok, msg = self.find_and_open()
                if ok:
                    self.log('DS5 reconnected!')
                    # Restart input thread
                    self._in_thread = threading.Thread(target=self._input_loop, daemon=True)
                    self._in_thread.start()
                    return
            if not self.running:
                self.log('Reconnect cancelled (bridge stopped)')
        threading.Thread(target=loop, daemon=True).start()

    def _input_loop(self):
        """Read from DS5, send to server."""
        while self.running:
            try:
                data = self.dev.read(128, 50)
                if not data:
                    continue

                report = bytearray(USB_REPORT_SIZE)
                report[0] = 0x01

                if self.is_bt:
                    src = data[2:] if data[0] == 0x31 else data[1:]
                else:
                    src = data[1:] if data[0] == 0x01 else data
                copy_len = min(len(src), USB_REPORT_SIZE - 1)
                report[1:1 + copy_len] = src[:copy_len]

                self.sock.sendto(bytes(report), self.target)
                self.packets_sent += 1

                # Rate calculation
                self._rate_count += 1
                now = time.monotonic()
                dt = now - self._rate_time
                if dt >= 2.0:
                    self.send_rate = self._rate_count / dt
                    self._rate_count = 0
                    self._rate_time = now

            except Exception as e:
                if self.running:
                    self.log(f'DS5 disconnected: {e}')
                    self.connected = False
                    self._try_reconnect()
                break

    def _output_loop(self):
        """Receive from server, write to DS5."""
        seq = 0
        while self.running:
            try:
                if self._is_tcp and self._tcp_sock:
                    self._tcp_sock.settimeout(1.0)
                    data = self._tcp_sock.recv(512)
                    if not data:
                        self.log('TCP return channel closed by server')
                        break
                else:
                    self.sock.settimeout(1.0)
                    data, addr = self.sock.recvfrom(2048)
                if len(data) < 2:
                    continue
                self.server_alive = True
                self._last_server_rx = time.monotonic()

                # Feature GET request
                if data[0] == 0x03:
                    self._handle_feature_get(data)
                    continue

                # Feature SET
                if data[0] == 0x05:
                    self._handle_feature_set(data)
                    continue

                # Haptic audio: 0x32 (old u8 format) or 0x40 (raw s16 stream)
                if data[0] in (0x32, 0x40):
                    self._handle_haptic(data)
                    continue

                # Unknown prefix - log it
                if data[0] not in (0x02, 0x03, 0x05):
                    self.log(f'Unknown packet: 0x{data[0]:02X} len={len(data)}')

                # Output report
                if self.is_bt:
                    bt_out = bytearray(78)
                    bt_out[0] = 0x31
                    bt_out[1] = seq
                    bt_out[2] = 0x10
                    usb_payload = data[1:] if len(data) > 1 else b''
                    copy_len = min(len(usb_payload), 71)
                    bt_out[3:3 + copy_len] = usb_payload[:copy_len]
                    crc = ds5_bt_crc32(bytes(bt_out[:74]))
                    struct.pack_into('<I', bt_out, 74, crc)
                    self.dev.write(bytes(bt_out))
                    seq = (seq + 16) & 0xFF
                else:
                    # USB: write output report directly (already USB format)
                    try:
                        self.dev.write(bytes(data))
                    except Exception:
                        pass  # USB may reject some reports

                self.packets_recv += 1
                if self.config.get('debug_output_reports', False):
                    self.log(decode_output_report(data))

            except socket.timeout:
                continue
            except ConnectionResetError:
                continue
            except Exception as e:
                if self.running:
                    self.log(f'Output error: {e}')
                break

    def _handle_feature_get(self, data):
        report_id = data[1]
        try:
            if not self.is_bt and report_id in USB_UNSUPPORTED_FEATURES:
                self.log(f'Feature GET 0x{report_id:02X}: unsupported on USB, skipping (driver uses static fallback)')
                return
            response = self.dev.get_feature_report(report_id, 64 if not self.is_bt else 256)
            if response:
                resp_bytes = bytes(response)
                self.log(f'Feature GET 0x{report_id:02X}: {len(resp_bytes)}B [{resp_bytes[:8].hex(" ")}...]')
                pkt = bytes([0x04, report_id]) + resp_bytes
                if self._is_tcp and self._tcp_sock:
                    self._tcp_sock.sendall(pkt)
                else:
                    self.sock.sendto(pkt, self.target)
                self.features_handled += 1
            else:
                self.log(f'Feature GET 0x{report_id:02X}: empty response')
        except Exception as e:
            self.log(f'Feature GET 0x{report_id:02X} error: {e}')

    def _handle_feature_set(self, data):
        report_id = data[1]
        try:
            if not self.is_bt and report_id in USB_UNSUPPORTED_FEATURES:
                self.log(f'Feature SET 0x{report_id:02X}: unsupported on USB, skipping')
                return
            self.log(f'Feature SET 0x{report_id:02X}: {len(data)}B [{data[:8].hex(" ")}...]')
            if self.is_bt:
                buf = bytearray(64)
                buf[0] = report_id
                pdata = data[2:]
                buf[1:1+len(pdata)] = pdata[:63]
                crc = ds5_crc32_payload([0x53, report_id], buf[1:60])
                struct.pack_into('<I', buf, 60, crc)
                self.dev.send_feature_report(bytes(buf))
            else:
                self.dev.send_feature_report(data[1:])
            self.features_handled += 1
        except Exception as e:
            self.log(f'Feature SET 0x{report_id:02X} error: {e}')

    _haptic_seq = 0
    _haptic_sender_running = False
    _haptic_s16_buffer = None
    _haptic_lock = None

    def _start_haptic_sender(self):
        if self._haptic_sender_running:
            return
        self._haptic_sender_running = True
        self._haptic_s16_buffer = bytearray()
        self._haptic_u8_buffer = bytearray()
        self._haptic_lock = threading.Lock()
        t = threading.Thread(target=self._haptic_send_loop, daemon=True)
        t.start()
        self.log('Haptic sender started')

    def _send_report_0x32(self, audio_data):
        """Build and send a single Report 0x32."""
        seq = self._haptic_seq
        REPORT_ID = 0x32
        pkt_0x11 = bytes([
            (0x11 & 0x3F) | (1 << 7), 7,
            0b11111110, 0, 0, 0, 0, seq & 0xFF, 0
        ])
        pkt_0x12_header = bytes([(0x12 & 0x3F) | (1 << 7), 64])
        packets = pkt_0x11 + pkt_0x12_header + bytes(audio_data)
        payload = packets.ljust(136, b'\x00')
        tag_seq = (seq & 0x0F) << 4
        report_body = bytes([tag_seq]) + payload
        crc = ds5_bt_crc32(bytes([REPORT_ID]) + report_body)
        report = bytes([REPORT_ID]) + report_body + struct.pack('<I', crc)
        self.dev.write(report)
        self._haptic_seq = (seq + 1) & 0x0F

    def _update_peak(self, audio_data):
        """Update waveform + peak meter."""
        self.haptic_waveform = list(audio_data[:64])
        peak = max(abs(b - 128) for b in audio_data[:64]) / 128.0
        self.haptic_peak = peak
        self.haptic_count += 1
        now = time.monotonic()
        if peak >= self.haptic_peak_hold:
            self.haptic_peak_hold = peak
            self._haptic_peak_time = now
        elif now - self._haptic_peak_time > 1.5:
            self.haptic_peak_hold = max(self.haptic_peak_hold - 0.01, peak)

    def _resample_chunk(self, chunk):
        """Resample s16 stereo chunk to 32 u8 stereo samples."""
        import numpy as np
        from scipy.signal import resample as sp_resample
        n = len(chunk) // 4
        left = np.zeros(n, dtype=np.float64)
        right = np.zeros(n, dtype=np.float64)
        for i in range(n):
            l, r = struct.unpack_from('<hh', chunk, i * 4)
            left[i] = l
            right[i] = r
        left_ds = sp_resample(left, 32)
        right_ds = sp_resample(right, 32)
        gain = self.config.get('haptic_gain', 4)
        audio = bytearray(64)
        for i in range(32):
            l_s16 = int(np.clip(left_ds[i], -32768, 32767))
            r_s16 = int(np.clip(right_ds[i], -32768, 32767))
            import numpy as np
            l_out = int(np.clip((l_s16 * gain) >> 8, -128, 127)) + 128
            r_out = int(np.clip((r_s16 * gain) >> 8, -128, 127)) + 128
            audio[i*2] = l_out & 0xFF
            audio[i*2+1] = r_out & 0xFF
        return audio

    def _haptic_send_loop(self):
        """Timed sender: fires every 10.67ms when timed=True."""
        INTERVAL_NS = 10_666_666
        INPUT_BYTES_PER_TICK = 512 * 4  # 512 s16 stereo samples = 10.67ms @ 48kHz
        next_ns = time.monotonic_ns()

        while self.running and self._haptic_sender_running:
            timed = self.config.get('haptic_timed', True)
            mode = self.config.get('haptic_mode', 'raw')

            if timed:
                next_ns += INTERVAL_NS
                now = time.monotonic_ns()
                wait = next_ns - now
                if wait > 2_000_000:
                    time.sleep((wait - 1_000_000) / 1_000_000_000)
                while time.monotonic_ns() < next_ns:
                    pass
                if time.monotonic_ns() - next_ns > 100_000_000:
                    next_ns = time.monotonic_ns()
            else:
                time.sleep(0.001)

            if mode == 'resample':
                with self._haptic_lock:
                    if len(self._haptic_s16_buffer) >= INPUT_BYTES_PER_TICK:
                        chunk = bytes(self._haptic_s16_buffer[:INPUT_BYTES_PER_TICK])
                        del self._haptic_s16_buffer[:INPUT_BYTES_PER_TICK]
                    elif not timed and len(self._haptic_s16_buffer) >= 64:
                        chunk = bytes(self._haptic_s16_buffer)
                        self._haptic_s16_buffer.clear()
                    else:
                        continue
                audio = self._resample_chunk(chunk)
                self._update_peak(audio)
                self._send_report_0x32(audio)

            else:  # raw
                with self._haptic_lock:
                    if len(self._haptic_u8_buffer) >= 64:
                        audio = bytes(self._haptic_u8_buffer[:64])
                        del self._haptic_u8_buffer[:64]
                    else:
                        continue
                self._update_peak(audio)
                self._send_report_0x32(audio)

    _usb_audio_stream = None
    _usb_audio_pa = None

    def _start_usb_audio(self):
        """Open USB audio output stream to DS5 speaker."""
        if self._usb_audio_stream:
            return True
        try:
            import pyaudiowpatch as pyaudio
            self._usb_audio_pa = pyaudio.PyAudio()
            # Find DS5 USB speaker (output, not loopback)
            ds5_out = None
            for i in range(self._usb_audio_pa.get_device_count()):
                info = self._usb_audio_pa.get_device_info_by_index(i)
                name = info.get('name', '')
                if ('DualSense' in name or 'Wireless Controller' in name) and info['maxOutputChannels'] >= 2 and not info.get('isLoopbackDevice'):
                    ds5_out = info
                    break
            if not ds5_out:
                self.log('USB: DS5 speaker not found')
                return False
            # Open 4ch/48kHz/S16 to match DS5 USB format
            channels = min(int(ds5_out['maxOutputChannels']), 4)
            self._usb_audio_stream = self._usb_audio_pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=48000,
                output=True,
                output_device_index=ds5_out['index'],
                frames_per_buffer=256)
            self._usb_channels = channels
            self.log(f'USB: Opened DS5 speaker ({ds5_out["name"]}, {channels}ch, 48kHz)')
            return True
        except Exception as e:
            self.log(f'USB audio error: {e}')
            return False

    def _handle_haptic(self, data):
        if data[0] == 0x40:
            raw_s16 = data[2:]

            # Input peak (raw s16, before any processing)
            if len(raw_s16) >= 4:
                max_val = 0
                for i in range(0, min(len(raw_s16), 512), 2):
                    val = abs(int.from_bytes(raw_s16[i:i+2], 'little', signed=True))
                    if val > max_val:
                        max_val = val
                self.haptic_input_peak = max_val / 32768.0

            if not self.is_bt:
                # USB mode: forward s16 directly to DS5 speaker
                if self._start_usb_audio():
                    try:
                        gain = self.config.get('haptic_gain', 2.0)
                        n_samples = len(raw_s16) // 4
                        if self._usb_channels == 4:
                            out = bytearray(n_samples * 8)
                            for i in range(n_samples):
                                l = int.from_bytes(raw_s16[i*4:i*4+2], 'little', signed=True)
                                r = int.from_bytes(raw_s16[i*4+2:i*4+4], 'little', signed=True)
                                l = max(-32768, min(32767, int(l * gain)))
                                r = max(-32768, min(32767, int(r * gain)))
                                out[i*8+4:i*8+6] = l.to_bytes(2, 'little', signed=True)
                                out[i*8+6:i*8+8] = r.to_bytes(2, 'little', signed=True)
                            self._usb_audio_stream.write(bytes(out))
                        else:
                            out = bytearray(n_samples * 4)
                            for i in range(n_samples):
                                l = int.from_bytes(raw_s16[i*4:i*4+2], 'little', signed=True)
                                r = int.from_bytes(raw_s16[i*4+2:i*4+4], 'little', signed=True)
                                l = max(-32768, min(32767, int(l * gain)))
                                r = max(-32768, min(32767, int(r * gain)))
                                out[i*4:i*4+2] = l.to_bytes(2, 'little', signed=True)
                                out[i*4+2:i*4+4] = r.to_bytes(2, 'little', signed=True)
                            self._usb_audio_stream.write(bytes(out))
                        self.haptic_count += 1
                    except Exception as e:
                        self.log(f'USB write error: {e}')
                return

            # BT mode: existing logic
            if not self._haptic_sender_running:
                self._start_haptic_sender()

            mode = self.config.get('haptic_mode', 'raw')
            with self._haptic_lock:
                if mode == 'resample':
                    self._haptic_s16_buffer.extend(raw_s16)
                    if len(self._haptic_s16_buffer) > 48000 * 4:
                        del self._haptic_s16_buffer[:len(self._haptic_s16_buffer) - 48000 * 4]
                else:
                    self._haptic_u8_buffer.extend(raw_s16[:64])
                    if len(self._haptic_u8_buffer) > 12000:
                        del self._haptic_u8_buffer[:len(self._haptic_u8_buffer) - 12000]

        elif data[0] == 0x32:
            if self.is_bt:
                if not self._haptic_sender_running:
                    self._start_haptic_sender()
                audio = data[2:66]
                if len(audio) < 64:
                    audio = audio + bytes(64 - len(audio))
                with self._haptic_lock:
                    self._haptic_u8_buffer.extend(audio)


class DS5ClientGUI:
    """Tkinter GUI with tabbed interface."""

    def __init__(self):
        self.client = DS5Client(log_callback=self._log)
        self._log_buffer = []

        self.root = tk.Tk()
        self.root.title('DS5 Bridge Client')
        self.root.geometry('520x520')
        self.root.resizable(False, False)
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

        # Try to set icon
        try:
            self.root.iconbitmap(default='')
        except: pass

        self._build_ui()
        self._update_loop()

        # Auto-connect
        self.root.after(500, self._auto_connect)

    def _build_ui(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill='both', expand=True, padx=5, pady=5)

        # === Tab 1: Status ===
        tab_status = ttk.Frame(notebook, padding=10)
        notebook.add(tab_status, text=' Status ')

        # Server connection
        srv_frame = ttk.LabelFrame(tab_status, text='Server Connection', padding=8)
        srv_frame.pack(fill='x', pady=(0, 8))

        self.lbl_server = ttk.Label(srv_frame, text='Not connected')
        self.lbl_server.grid(row=0, column=0, sticky='w')
        self.lbl_stats = ttk.Label(srv_frame, text='', foreground='gray')
        self.lbl_stats.grid(row=1, column=0, sticky='w')

        btn_frame = ttk.Frame(srv_frame)
        btn_frame.grid(row=0, column=1, rowspan=2, sticky='e', padx=(20, 0))
        self.btn_connect = ttk.Button(btn_frame, text='Start', width=10, command=self._toggle_bridge)
        self.btn_connect.pack(side='left', padx=2)
        self.btn_reconnect = ttk.Button(btn_frame, text='Reconnect DS5', width=14, command=self._reconnect)
        self.btn_reconnect.pack(side='left', padx=2)
        srv_frame.columnconfigure(0, weight=1)

        # Physical DS5
        ds5_frame = ttk.LabelFrame(tab_status, text='Physical DualSense', padding=8)
        ds5_frame.pack(fill='x', pady=(0, 8))

        self.lbl_ds5 = ttk.Label(ds5_frame, text='Searching...')
        self.lbl_ds5.grid(row=0, column=0, sticky='w', columnspan=2)

        # Hardware info (2 columns)
        self.hw_labels = {}
        hw_fields = [
            ('serial', 'Serial'),
            ('mac', 'MAC'),
            ('firmware_date', 'Firmware'),
            ('hw_version', 'HW Version'),
            ('board_version', 'Board'),
            ('color_id', 'Color ID'),
            ('battery_barcode', 'Battery BC'),
            ('vcm_barcode_l', 'VCM L'),
            ('vcm_barcode_r', 'VCM R'),
        ]
        for i, (key, label) in enumerate(hw_fields):
            row = 1 + i // 2
            col = (i % 2) * 2
            ttk.Label(ds5_frame, text=f'{label}:', foreground='gray').grid(row=row, column=col, sticky='w', padx=(0, 4))
            lbl = ttk.Label(ds5_frame, text='-')
            lbl.grid(row=row, column=col+1, sticky='w', padx=(0, 16))
            self.hw_labels[key] = lbl

        ds5_frame.columnconfigure(1, weight=1)
        ds5_frame.columnconfigure(3, weight=1)

        # Haptic Peak Meter
        haptic_frame = ttk.LabelFrame(tab_status, text='Haptic Audio', padding=8)
        haptic_frame.pack(fill='x', pady=(0, 8))

        self.lbl_haptic = ttk.Label(haptic_frame, text='No data')
        self.lbl_haptic.pack(anchor='w')

        self.peak_canvas = tk.Canvas(haptic_frame, height=24, bg='#1a1a1a', highlightthickness=0)
        self.peak_canvas.pack(fill='x', pady=(4, 0))

        self.wave_canvas = tk.Canvas(haptic_frame, height=60, bg='#1a1a1a', highlightthickness=0)
        self.wave_canvas.pack(fill='x', pady=(4, 0))

        # === Tab 2: Config ===
        tab_config = ttk.Frame(notebook, padding=10)
        notebook.add(tab_config, text=' Config ')

        cfg_frame = ttk.LabelFrame(tab_config, text='Server', padding=8)
        cfg_frame.pack(fill='x', pady=(0, 8))

        ttk.Label(cfg_frame, text='Host:').grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.entry_host = ttk.Entry(cfg_frame, width=20)
        self.entry_host.insert(0, self.client.config['server_host'])
        self.entry_host.grid(row=0, column=1, sticky='w')

        ttk.Label(cfg_frame, text='Port:').grid(row=0, column=2, sticky='w', padx=(16, 8))
        self.entry_port = ttk.Entry(cfg_frame, width=8)
        self.entry_port.insert(0, str(self.client.config['server_port']))
        self.entry_port.grid(row=0, column=3, sticky='w')

        ttk.Button(cfg_frame, text='Save', width=8, command=self._save_config).grid(
            row=0, column=4, padx=(16, 0))

        # Client settings
        client_frame = ttk.LabelFrame(tab_config, text='Client', padding=8)
        client_frame.pack(fill='x', pady=(0, 8))

        ttk.Label(client_frame, text='Local Port:').grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.entry_client_port = ttk.Entry(client_frame, width=8)
        self.entry_client_port.insert(0, str(self.client.config.get('client_port', 0)))
        self.entry_client_port.grid(row=0, column=1, sticky='w')
        ttk.Label(client_frame, text='(0 = random)', foreground='gray').grid(row=0, column=2, sticky='w', padx=(8, 0))

        ttk.Label(client_frame, text='Protocol:').grid(row=1, column=0, sticky='w', padx=(0, 8), pady=(4, 0))
        self.proto_var = tk.StringVar(value=self.client.config.get('protocol', 'udp'))
        proto_frame = ttk.Frame(client_frame)
        proto_frame.grid(row=1, column=1, columnspan=2, sticky='w', pady=(4, 0))
        ttk.Radiobutton(proto_frame, text='UDP', variable=self.proto_var, value='udp').pack(side='left', padx=(0, 12))
        ttk.Radiobutton(proto_frame, text='TCP', variable=self.proto_var, value='tcp').pack(side='left')

        # Haptic Gain
        haptic_frame = ttk.LabelFrame(tab_config, text='Haptic Audio', padding=8)
        haptic_frame.pack(fill='x', pady=(0, 8))

        ttk.Label(haptic_frame, text='Gain:').grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.gain_var = tk.DoubleVar(value=self.client.config.get('haptic_gain', 2.0))
        self.gain_slider = tk.Scale(haptic_frame, from_=0.5, to=8.0, resolution=0.1,
                                     orient='horizontal', variable=self.gain_var,
                                     command=self._update_gain, length=250)
        self.gain_slider.grid(row=0, column=1, sticky='w')
        self.lbl_gain = ttk.Label(haptic_frame, text=f'x{self.gain_var.get():.1f}')
        self.lbl_gain.grid(row=0, column=2, padx=(8, 0))

        ttk.Label(haptic_frame, text='Mode:').grid(row=1, column=0, sticky='w', padx=(0, 8), pady=(4, 0))
        mode_frame = ttk.Frame(haptic_frame)
        mode_frame.grid(row=1, column=1, columnspan=2, sticky='w', pady=(4, 0))
        self.haptic_mode_var = tk.StringVar(value=self.client.config.get('haptic_mode', 'raw'))
        ttk.Radiobutton(mode_frame, text='Raw S16 bytes', variable=self.haptic_mode_var, value='raw',
                        command=self._update_haptic_mode).pack(side='left', padx=(0, 12))
        ttk.Radiobutton(mode_frame, text='Resample 48k>3k', variable=self.haptic_mode_var, value='resample',
                        command=self._update_haptic_mode).pack(side='left')

        self.haptic_timed_var = tk.BooleanVar(value=self.client.config.get('haptic_timed', True))
        ttk.Checkbutton(haptic_frame, text='Timed sending (10.67ms)', variable=self.haptic_timed_var,
                       command=self._update_haptic_timed).grid(row=2, column=0, columnspan=3, sticky='w', pady=(4, 0))

        # Debug
        dbg_frame = ttk.LabelFrame(tab_config, text='Debug', padding=8)
        dbg_frame.pack(fill='x', pady=(0, 8))

        self.debug_output_var = tk.BooleanVar(value=self.client.config.get('debug_output_reports', False))
        ttk.Checkbutton(dbg_frame, text='Log incoming output reports (decoded)', variable=self.debug_output_var,
                       command=lambda: self._save_debug('debug_output_reports', self.debug_output_var.get())).grid(row=0, column=0, sticky='w')

        # Autostart
        opt_frame = ttk.LabelFrame(tab_config, text='Options', padding=8)
        opt_frame.pack(fill='x', pady=(0, 8))

        self.autostart_var = tk.BooleanVar(value=self.client.config.get('autostart', False))
        ttk.Checkbutton(opt_frame, text='Start with Windows', variable=self.autostart_var,
                       command=self._toggle_autostart).grid(row=0, column=0, sticky='w')

        # === Tab 3: Log ===
        tab_log = ttk.Frame(notebook, padding=5)
        notebook.add(tab_log, text=' Log ')

        self.log_text = scrolledtext.ScrolledText(tab_log, height=20, state='disabled',
                                                   font=('Consolas', 9), wrap='word')
        self.log_text.pack(fill='both', expand=True)

        btn_clear = ttk.Button(tab_log, text='Clear', command=self._clear_log)
        btn_clear.pack(anchor='e', pady=(4, 0))

    def _log(self, msg):
        self._log_buffer.append(msg)

    def _flush_log(self):
        if not self._log_buffer:
            return
        self.log_text.config(state='normal')
        for msg in self._log_buffer:
            self.log_text.insert('end', msg + '\n')
        self.log_text.see('end')
        self.log_text.config(state='disabled')
        self._log_buffer.clear()

    def _clear_log(self):
        self.log_text.config(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.config(state='disabled')

    def _update_loop(self):
        self._flush_log()

        # Server status
        if self.client.running:
            host = self.client.config['server_host']
            port = self.client.config['server_port']
            proto = self.client.config.get('protocol', 'udp').upper()
            if self.client.server_alive:
                idle = time.monotonic() - self.client._last_server_rx
                if idle < 5.0:
                    self.lbl_server.config(text=f'{proto} {host}:{port} - Server active', foreground='green')
                else:
                    self.lbl_server.config(text=f'{proto} {host}:{port} - Server idle ({idle:.0f}s)', foreground='orange')
            else:
                self.lbl_server.config(text=f'{proto} {host}:{port} - Waiting for server...', foreground='#cc8800')
            self.lbl_stats.config(
                text=f'TX: {self.client.packets_sent}  |  RX: {self.client.packets_recv}  |  '
                     f'Features: {self.client.features_handled}  |  {self.client.send_rate:.0f} pkt/s')
            self.btn_connect.config(text='Stop')
        else:
            self.lbl_server.config(text='Not connected', foreground='gray')
            self.lbl_stats.config(text='')
            self.btn_connect.config(text='Start')

        # DS5 status
        if self.client.connected:
            conn = 'Bluetooth' if self.client.is_bt else 'USB'
            name = 'DualSense Edge' if self.client.dev_info and self.client.dev_info['product_id'] == 0x0DF2 else 'DualSense'
            self.lbl_ds5.config(text=f'{name} ({conn})', foreground='green')
            for key, lbl in self.hw_labels.items():
                val = self.client.hw_info.get(key, '-')
                lbl.config(text=val if val else '-')
        elif self.client.running:
            self.lbl_ds5.config(text='Reconnecting...', foreground='orange')
        else:
            self.lbl_ds5.config(text='Not connected', foreground='red')
            for lbl in self.hw_labels.values():
                lbl.config(text='-')

        # Haptic peak meter
        if self.client.haptic_count > 0:
            rate = self.client.haptic_count / max(1, time.monotonic() - self.client._rate_time)
            self.lbl_haptic.config(
                text=f'Packets: {self.client.haptic_count}  |  '
                     f'In: {self.client.haptic_input_peak:.3f}  |  '
                     f'Out: {self.client.haptic_peak:.3f}  |  '
                     f'Hold: {self.client.haptic_peak_hold:.3f}')
            self._draw_peak_meter()
        else:
            self.lbl_haptic.config(text='No haptic data')

        # Waveform
        wf = self.client.haptic_waveform
        if wf and len(wf) >= 4:
            cv = self.wave_canvas
            cv.delete('all')
            w = cv.winfo_width() or 480
            h = cv.winfo_height() or 60
            mid = h // 2
            cv.create_line(0, mid, w, mid, fill='#333333')
            # Draw L channel (even bytes) and R channel (odd bytes) - auto-scaled
            n = len(wf) // 2
            centered = [wf[i] - 128 for i in range(len(wf))]
            max_val = max(abs(v) for v in centered) if centered else 1
            if max_val < 1:
                max_val = 1
            pts_l = []
            pts_r = []
            for i in range(n):
                x = int(i * w / n)
                yl = mid - int((centered[i*2] / max_val) * mid * 0.85)
                yr = mid - int((centered[i*2+1] / max_val) * mid * 0.85)
                pts_l.append((x, yl))
                pts_r.append((x, yr))
            if len(pts_l) >= 2:
                cv.create_line(*[c for p in pts_l for c in p], fill='#66aaff', width=1)
                cv.create_line(*[c for p in pts_r for c in p], fill='#33cc66', width=1)
                cv.create_text(w - 4, 4, anchor='ne', text=f'u8 +/-{max_val}', fill='#555555', font=('Consolas', 7))

        self.root.after(50, self._update_loop)

    def _draw_peak_meter(self):
        cv = self.peak_canvas
        cv.delete('all')
        w = cv.winfo_width() or 480
        h = cv.winfo_height() or 24

        # Current level bar
        level = min(self.client.haptic_peak, 1.0)
        bar_w = int(level * w)
        if level < 0.5:
            color = '#00cc44'
        elif level < 0.8:
            color = '#cccc00'
        else:
            color = '#cc3300'
        if bar_w > 0:
            cv.create_rectangle(0, 0, bar_w, h, fill=color, outline='')

        # Peak hold line
        hold = min(self.client.haptic_peak_hold, 1.0)
        hold_x = int(hold * w)
        if hold_x > 0:
            cv.create_line(hold_x, 0, hold_x, h, fill='white', width=2)

        # Scale markers
        for pct in (0.25, 0.5, 0.75):
            x = int(pct * w)
            cv.create_line(x, h - 4, x, h, fill='#444444')

    def _auto_connect(self):
        def do():
            ok, msg = self.client.find_and_open()
            if ok:
                self.client.start()
            else:
                self.client.log(msg)
        threading.Thread(target=do, daemon=True).start()

    def _toggle_bridge(self):
        if self.client.running:
            self.client.stop()
        else:
            if not self.client.connected:
                def do():
                    ok, msg = self.client.find_and_open()
                    if ok:
                        self.client.start()
                    else:
                        self.client.log(msg)
                threading.Thread(target=do, daemon=True).start()
            else:
                self.client.start()

    def _reconnect(self):
        def do():
            self.client.disconnect()
            time.sleep(0.5)
            ok, msg = self.client.find_and_open()
            if ok and self.client.running is False:
                self.client.start()
            elif not ok:
                self.client.log(msg)
        threading.Thread(target=do, daemon=True).start()

    def _save_config(self):
        self.client.config['server_host'] = self.entry_host.get().strip()
        try:
            self.client.config['server_port'] = int(self.entry_port.get().strip())
        except ValueError:
            pass
        try:
            self.client.config['client_port'] = int(self.entry_client_port.get().strip())
        except ValueError:
            pass
        self.client.config['protocol'] = self.proto_var.get()
        save_config(self.client.config)
        self.client.log('Config saved (restart bridge to apply)')

    def _update_haptic_mode(self):
        self.client.config['haptic_mode'] = self.haptic_mode_var.get()
        save_config(self.client.config)

    def _update_haptic_timed(self):
        self.client.config['haptic_timed'] = self.haptic_timed_var.get()
        save_config(self.client.config)

    def _update_gain(self, val):
        g = float(val)
        self.client.config['haptic_gain'] = g
        self.lbl_gain.config(text=f'x{g:.1f}')
        save_config(self.client.config)

    def _save_debug(self, key, val):
        self.client.config[key] = val
        save_config(self.client.config)

    def _toggle_autostart(self):
        enabled = self.autostart_var.get()
        try:
            import winreg
            key_path = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            if enabled:
                exe = sys.executable.replace('python.exe', 'pythonw.exe')
                script = os.path.abspath(__file__)
                winreg.SetValueEx(key, 'DS5Client', 0, winreg.REG_SZ, f'"{exe}" "{script}"')
            else:
                try: winreg.DeleteValue(key, 'DS5Client')
                except FileNotFoundError: pass
            winreg.CloseKey(key)
            self.client.config['autostart'] = enabled
            save_config(self.client.config)
            self.client.log(f'Autostart {"enabled" if enabled else "disabled"}')
        except Exception as e:
            self.client.log(f'Autostart error: {e}')

    def _on_close(self):
        self.client.disconnect()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    app = DS5ClientGUI()
    app.run()
