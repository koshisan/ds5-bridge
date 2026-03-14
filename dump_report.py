import hid, struct, zlib, math, time

dev = hid.device()
devs = hid.enumerate(0x054C, 0x0CE6)
bt = [d for d in devs if d.get('usage_page')==1 and d.get('usage')==5]
dev.open_path((bt or devs)[0]['path'])

REPORT_ID = 0x32
REPORT_SIZE = 141
SAMPLE_SIZE = 64
SAMPLE_RATE = 3000
INTERVAL = SAMPLE_SIZE / (SAMPLE_RATE * 2)

def crc32_ds5(data):
    return zlib.crc32(bytes([0xA2]) + data) & 0xFFFFFFFF

def build_report_DEMO(sample_data, seq):
    """Exact copy from haptic_demo.py"""
    payload_size = REPORT_SIZE - 1 - 4
    pkt_0x11 = bytes([
        (0x11 & 0x3F) | (0 << 6) | (1 << 7),
        7,
        0b11111110, 0, 0, 0, 0, seq & 0xFF, 0
    ])
    pkt_0x12_header = bytes([
        (0x12 & 0x3F) | (0 << 6) | (1 << 7),
        SAMPLE_SIZE,
    ])
    packets = pkt_0x11 + pkt_0x12_header + sample_data
    payload = packets.ljust(payload_size, b'\x00')
    tag_seq = (seq & 0x0F) << 4
    report_body = bytes([tag_seq]) + payload
    crc_data = bytes([REPORT_ID]) + report_body
    crc = crc32_ds5(crc_data)
    return bytes([REPORT_ID]) + report_body + struct.pack('<I', crc)

samples = bytes([int(128+80*math.sin(2*3.14159*150*i/3000)) for i in range(32) for _ in (0,1)])

report = build_report_DEMO(samples, 0)
print(f'DEMO: len={len(report)}')
print(f'DEMO: {report[:20].hex(" ")}')

# Send 50 packets like haptic_demo does
print("Sending 50 haptic packets...")
for i in range(50):
    samples = bytes([int(128+80*math.sin(2*3.14159*150*(i*32+j)/3000)) for j in range(32) for _ in (0,1)])
    report = build_report_DEMO(samples, i)
    dev.write(report)
    time.sleep(INTERVAL)

print("Done!")
dev.close()
