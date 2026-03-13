#!/usr/bin/env python3
"""DS5 BT Haptic Audio Demo - Sine wave over Report 0x32 (based on SAxense research)"""

import hid
import math
import time
import struct
import zlib
import sys

VENDOR_ID = 0x054C
PRODUCT_ID = 0x0CE6
REPORT_ID = 0x32
REPORT_SIZE = 141  # total including report_id
SAMPLE_SIZE = 64
SAMPLE_RATE = 3000
INTERVAL = SAMPLE_SIZE / (SAMPLE_RATE * 2)  # ~10.67ms

def crc32_ds5(data):
    """CRC32 with 0xA2 seed (same as regular DS5 BT)"""
    return zlib.crc32(bytes([0xA2]) + data) & 0xFFFFFFFF

def build_haptic_report(sample_data, seq):
    """Build a 0x32 haptic report with audio samples"""
    # Report structure (141 bytes):
    # [0] = report_id (0x32) -- not sent via hidapi on some platforms
    # [1] = tag(4bit) | seq(4bit)
    # [2..N] = packets
    # [N+1..N+4] = CRC32
    
    payload_size = REPORT_SIZE - 1 - 4  # minus report_id and crc = 136 bytes
    
    # Packet 0x11: control (pid=0x11, sized=1, length=7, data=7bytes)
    pkt_0x11 = bytes([
        (0x11 & 0x3F) | (0 << 6) | (1 << 7),  # pid=0x11, unk=0, sized=1
        7,  # length
        0b11111110, 0, 0, 0, 0, seq & 0xFF, 0  # data (7 bytes)
    ])
    
    # Packet 0x12: audio samples (pid=0x12, sized=1, length=64)
    pkt_0x12_header = bytes([
        (0x12 & 0x3F) | (0 << 6) | (1 << 7),  # pid=0x12, unk=0, sized=1
        SAMPLE_SIZE,  # length
    ])
    
    # Build payload
    packets = pkt_0x11 + pkt_0x12_header + sample_data
    
    # Pad to payload_size
    payload = packets.ljust(payload_size, b'\x00')
    
    # Tag=0, seq in upper nibble
    tag_seq = (seq & 0x0F) << 4
    
    # Full report (without report_id for CRC, then prepend it)
    report_body = bytes([tag_seq]) + payload
    
    # CRC32 over report_id + body
    crc_data = bytes([REPORT_ID]) + report_body
    crc = crc32_ds5(crc_data)
    
    # Final: report_id + body + crc
    return bytes([REPORT_ID]) + report_body + struct.pack('<I', crc)

def generate_sine(freq, offset, num_samples):
    """Generate stereo sine wave samples (8-bit unsigned, interleaved L/R)"""
    samples = bytearray(num_samples)
    for i in range(0, num_samples, 2):
        t = (offset + i // 2) / SAMPLE_RATE
        val = int(128 + 80 * math.sin(2 * math.pi * freq * t))
        val = max(0, min(255, val))
        samples[i] = val      # left
        samples[i + 1] = val  # right
    return bytes(samples)

def main():
    freq = float(sys.argv[1]) if len(sys.argv) > 1 else 150.0
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
    
    print(f"DS5 Haptic Demo: {freq}Hz sine wave, {duration}s")
    print(f"Sample rate: {SAMPLE_RATE}Hz, Interval: {INTERVAL*1000:.1f}ms")
    
    # Find DS5 (BT only - usage_page filter)
    devs = hid.enumerate(VENDOR_ID, PRODUCT_ID)
    bt_dev = None
    for d in devs:
        # BT DS5 typically has usage_page 1, usage 5
        if d.get('usage_page') == 1 and d.get('usage') == 5:
            bt_dev = d
            break
    
    if not bt_dev:
        # Fallback: just pick first one
        if devs:
            bt_dev = devs[0]
        else:
            print("No DS5 found!")
            return
    
    print(f"Using: {bt_dev.get('product_string', 'DS5')} [{bt_dev['path'].decode() if isinstance(bt_dev['path'], bytes) else bt_dev['path']}]")
    
    dev = hid.device()
    dev.open_path(bt_dev['path'])
    
    seq = 0
    sample_offset = 0
    packets_sent = 0
    total_packets = int(duration / INTERVAL)
    
    print(f"Sending {total_packets} haptic packets...")
    start = time.perf_counter()
    
    try:
        for _ in range(total_packets):
            samples = generate_sine(freq, sample_offset, SAMPLE_SIZE)
            report = build_haptic_report(samples, seq)
            
            dev.write(report)
            
            packets_sent += 1
            seq = (seq + 1) & 0x0F
            sample_offset += SAMPLE_SIZE // 2
            
            # Precise timing
            next_time = start + packets_sent * INTERVAL
            now = time.perf_counter()
            if next_time > now:
                time.sleep(next_time - now)
        
        print(f"Done! Sent {packets_sent} packets in {time.perf_counter()-start:.2f}s")
        
        # Send silence to stop
        silence = bytes(SAMPLE_SIZE)
        for _ in range(10):
            report = build_haptic_report(silence, seq)
            dev.write(report)
            seq = (seq + 1) & 0x0F
            time.sleep(INTERVAL)
        
    finally:
        dev.close()

if __name__ == '__main__':
    main()
