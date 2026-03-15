"""Test: threaded BT write - separate write thread from timing."""
import sys
import time
import struct
import zlib
import math
import threading
import queue
import hid

DS5_VID = 0x054C
DS5_PIDS = {0x0CE6, 0x0DF2}

dev_info = None
for info in hid.enumerate(DS5_VID):
    if info['product_id'] in DS5_PIDS:
        dev_info = info
        break
dev = hid.device()
dev.open_path(dev_info['path'])
test = dev.read(128, 500)
if len(test) <= 64:
    print("Not BT!")
    sys.exit(1)

def ds5_bt_crc32(data):
    return zlib.crc32(bytes([0xA2]) + data) & 0xFFFFFFFF

write_queue = queue.Queue(maxsize=8)
write_count = [0]

def writer_thread():
    while True:
        report = write_queue.get()
        if report is None:
            break
        try:
            dev.write(report)
            write_count[0] += 1
        except:
            pass

wt = threading.Thread(target=writer_thread, daemon=True)
wt.start()

def make_sine_packet(offset, seq, freq=200):
    REPORT_ID = 0x32
    PAYLOAD_SIZE = 136
    audio = bytearray(64)
    for i in range(32):
        t = (offset + i) / 3000.0
        val = int(64 * math.sin(2 * math.pi * freq * t)) + 128
        audio[i*2] = max(0, min(255, val))
        audio[i*2+1] = max(0, min(255, val))
    
    pkt_0x11 = bytes([
        (0x11 & 0x3F) | (1 << 7), 7,
        0b11111110, 0, 0, 0, 0, seq & 0xFF, 0
    ])
    pkt_0x12_header = bytes([(0x12 & 0x3F) | (1 << 7), 64])
    packet_data = pkt_0x11 + pkt_0x12_header + bytes(audio)
    payload = packet_data.ljust(PAYLOAD_SIZE, b'\x00')
    tag_seq = (seq & 0x0F) << 4
    report_body = bytes([tag_seq]) + payload
    crc = ds5_bt_crc32(bytes([REPORT_ID]) + report_body)
    return bytes([REPORT_ID]) + report_body + struct.pack('<I', crc)

# Test: queue reports at 93.75 Hz, writer sends ASAP
print("=== Threaded write, 93.75 Hz feeder (5s) ===")
seq = 0
offset = 0
start = time.perf_counter()
next_send = start
duration = 5.0
queued = 0
dropped = 0

while time.perf_counter() - start < duration:
    now = time.perf_counter()
    if now >= next_send:
        report = make_sine_packet(offset, seq)
        try:
            write_queue.put_nowait(report)
            queued += 1
        except queue.Full:
            dropped += 1
        seq = (seq + 1) & 0x0F
        offset += 32
        next_send += 1.0 / 93.75
        if next_send < now - 0.1:
            next_send = now
    else:
        time.sleep(0.0005)

time.sleep(0.5)
elapsed = time.perf_counter() - start
print(f"Queued: {queued}, Written: {write_count[0]}, Dropped: {dropped}")
print(f"Write rate: {write_count[0]/elapsed:.1f} Hz")
print(f"Did it feel smooth?")

write_queue.put(None)
dev.close()
