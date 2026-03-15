"""Test: send Report 0x32 at different rates, measure what the DS5 accepts."""
import sys
import time
import struct
import zlib
import math
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

def send_report(dev, audio_data, seq):
    REPORT_ID = 0x32
    PAYLOAD_SIZE = 136
    pkt_0x11 = bytes([
        (0x11 & 0x3F) | (1 << 7), 7,
        0b11111110, 0, 0, 0, 0, seq & 0xFF, 0
    ])
    pkt_0x12_header = bytes([(0x12 & 0x3F) | (1 << 7), 64])
    packet_data = pkt_0x11 + pkt_0x12_header + audio_data
    payload = packet_data.ljust(PAYLOAD_SIZE, b'\x00')
    tag_seq = (seq & 0x0F) << 4
    report_body = bytes([tag_seq]) + payload
    crc = ds5_bt_crc32(bytes([REPORT_ID]) + report_body)
    report = bytes([REPORT_ID]) + report_body + struct.pack('<I', crc)
    dev.write(report)

# Generate 200Hz sine as u8
def make_sine_packet(offset, freq=200):
    audio = bytearray(64)
    for i in range(32):
        t = (offset + i) / 3000.0
        val = int(64 * math.sin(2 * math.pi * freq * t)) + 128
        audio[i*2] = max(0, min(255, val))
        audio[i*2+1] = max(0, min(255, val))
    return bytes(audio)

# Test different send rates
rates = [50, 93.75, 100, 150, 200, 300, 500, 750, 1000]

for target_rate in rates:
    interval = 1.0 / target_rate
    seq = 0
    sample_offset = 0
    packets = 0
    errors = 0
    duration = 2.0
    
    start = time.perf_counter()
    next_send = start
    
    while time.perf_counter() - start < duration:
        now = time.perf_counter()
        if now >= next_send:
            try:
                audio = make_sine_packet(sample_offset)
                send_report(dev, audio, seq)
                seq = (seq + 1) & 0x0F
                sample_offset += 32
                packets += 1
            except Exception as e:
                errors += 1
            next_send += interval
            # Catch up if behind
            if next_send < now - 0.1:
                next_send = now
    
    elapsed = time.perf_counter() - start
    actual_rate = packets / elapsed
    print(f"Target: {target_rate:7.1f} Hz | Actual: {actual_rate:7.1f} Hz | Sent: {packets:5d} | Errors: {errors}")
    time.sleep(0.5)

dev.close()
print("\nWhich rates produced smooth vibration?")
