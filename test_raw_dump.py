"""Dump 3 raw reports to find timestamp position."""
import hid, struct, time

DS5_VID = 0x054C
DS5_PIDS = {0x0CE6, 0x0DF2}
dev_info = None
for info in hid.enumerate(DS5_VID):
    if info['product_id'] in DS5_PIDS:
        dev_info = info
        break

dev = hid.device()
dev.open_path(dev_info['path'])

reports = []
for i in range(5):
    data = dev.read(128, 100)
    if data:
        reports.append(bytes(data))
    time.sleep(0.05)

for i, p in enumerate(reports):
    is_bt = len(p) > 64
    payload = p[2:] if (is_bt and p[0] == 0x31) else p[1:]
    print(f"Report {i}: {len(p)}B {'BT' if is_bt else 'USB'}")
    print(f"  Full: {p[:50].hex(' ')}")
    print(f"  Payload[0:30]: {bytes(payload[:30]).hex(' ')}")
    
    # Find changing values between consecutive reports
    if i > 0:
        prev_payload = reports[i-1][2:] if (is_bt and reports[i-1][0] == 0x31) else reports[i-1][1:]
        print(f"  Changed bytes vs prev:")
        for off in range(min(30, len(payload), len(prev_payload))):
            if payload[off] != prev_payload[off]:
                print(f"    offset {off}: {prev_payload[off]:3d} -> {payload[off]:3d}")
    print()

dev.close()
