"""Dump raw reports from VIRTUAL DS5 to check timestamps."""
import hid, struct, time

DS5_VID = 0x054C
DS5_PID = 0x0CE6

for info in hid.enumerate(DS5_VID, DS5_PID):
    path = info['path'].decode('utf-8', errors='replace') if isinstance(info['path'], bytes) else str(info['path'])
    print(f"  {path[:80]}")

# Open first match
dev = hid.device()
dev.open(DS5_VID, DS5_PID)

for i in range(5):
    data = dev.read(64, 200)
    if data:
        p = bytes(data)
        print(f"Report {i}: {len(p)}B")
        print(f"  Hex: {p[:40].hex(' ')}")
        # Check timestamp at Extended[0:4] = byte 12-15 in report
        if len(p) >= 16:
            ts = struct.unpack_from('<I', p, 12)[0]
            print(f"  Timestamp (offset 12): {ts}")
    time.sleep(0.05)

dev.close()
