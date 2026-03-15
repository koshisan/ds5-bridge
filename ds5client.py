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
    'autostart': False,
}

# --- CRC ---
def ds5_bt_crc32(data):
    return zlib.crc32(bytes([0xA2]) + data) & 0xFFFFFFFF

def ds5_crc32_payload(seed_bytes, data_bytes):
    return zlib.crc32(bytes(seed_bytes) + bytes(data_bytes)) & 0xFFFFFFFF


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

    # 0x80/0x81 subcommands (BT only — need CRC)
    if is_bt:
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
                payload = bytearray(64)
                payload[0] = 0x80
                payload[1] = sub1
                payload[2] = sub2
                crc = ds5_crc32_payload([0x53, 0x80], payload[1:60])
                struct.pack_into('<I', payload, 60, crc)
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

        if proto == 'tcp':
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(3.0)
            try:
                self.sock.connect((host, port))
            except Exception as e:
                self.log(f'TCP connect failed: {e}')
                self.sock.close()
                self.sock = None
                return
            self.sock.settimeout(None)
            self._is_tcp = True
        else:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(('0.0.0.0', client_port))
            self._is_tcp = False

        self.target = (host, port)
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
        if self.sock:
            try: self.sock.close()
            except: pass
            self.sock = None
        self.log('Bridge stopped')

    def disconnect(self):
        """Disconnect from DS5."""
        self.stop()
        if self.dev:
            try: self.dev.close()
            except: pass
            self.dev = None
        self.connected = False
        self.hw_info = {}
        self.log('DS5 disconnected')

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

                if self._is_tcp:
                    self.sock.sendall(bytes(report))
                else:
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
                    self.log(f'Input error: {e}')
                break

    def _output_loop(self):
        """Receive from server, write to DS5."""
        seq = 0
        while self.running:
            try:
                self.sock.settimeout(1.0)
                if self._is_tcp:
                    data = self.sock.recv(512)
                    if not data:
                        self.log('TCP connection closed by server')
                        break
                else:
                    data, addr = self.sock.recvfrom(512)
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

                # Haptic audio (0x32)
                if data[0] == 0x32:
                    self._handle_haptic(data)
                    continue

                # Output report (0x02)
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
                    self.dev.write(bytes(data))

                self.packets_recv += 1

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
            response = self.dev.get_feature_report(report_id, 256)
            if response:
                pkt = bytes([0x04, report_id]) + bytes(response)
                if self._is_tcp:
                    self.sock.sendall(pkt)
                else:
                    self.sock.sendto(pkt, self.target)
                self.features_handled += 1
        except Exception as e:
            self.log(f'Feature GET 0x{report_id:02X} error: {e}')

    def _handle_feature_set(self, data):
        report_id = data[1]
        try:
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

    def _handle_haptic(self, data):
        if not self.is_bt:
            return
        audio_samples = data[2:66]
        if len(audio_samples) < 64:
            audio_samples = audio_samples + bytes(64 - len(audio_samples))

        seq = self._haptic_seq
        REPORT_ID = 0x32
        payload_size = 136  # 141 - 1 report_id - 4 crc

        pkt_0x11 = bytes([
            (0x11 & 0x3F) | (1 << 7), 7,
            0b11111110, 0, 0, 0, 0, seq & 0xFF, 0
        ])
        pkt_0x12_header = bytes([
            (0x12 & 0x3F) | (1 << 7), 64,
        ])
        packets = pkt_0x11 + pkt_0x12_header + bytes(audio_samples)
        payload = packets.ljust(payload_size, b'\x00')
        tag_seq = (seq & 0x0F) << 4
        report_body = bytes([tag_seq]) + payload
        crc_data = bytes([REPORT_ID]) + report_body
        crc = ds5_bt_crc32(crc_data)
        report = bytes([REPORT_ID]) + report_body + struct.pack('<I', crc)
        self.dev.write(report)
        self._haptic_seq = (seq + 1) & 0x0F


class DS5ClientGUI:
    """Tkinter GUI with tabbed interface."""

    def __init__(self):
        self.client = DS5Client(log_callback=self._log)
        self._log_buffer = []

        self.root = tk.Tk()
        self.root.title('DS5 Bridge Client')
        self.root.geometry('520x460')
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
        else:
            self.lbl_ds5.config(text='Not connected', foreground='red')
            for lbl in self.hw_labels.values():
                lbl.config(text='-')

        self.root.after(500, self._update_loop)

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
