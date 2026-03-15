"""Read from the VIRTUAL DS5 (driver) and count report rate."""
import hid
import struct
import time
import sys

DS5_VID = 0x054C
DS5_PID = 0x0CE6

# Find all DS5 devices - we want the virtual one (ROOT enumerated)
print("All DS5 HID devices:")
for info in hid.enumerate(DS5_VID, DS5_PID):
    path = info['path'].decode('utf-8', errors='replace') if isinstance(info['path'], bytes) else info['path']
    print(f"  {path[:80]}...")
    print(f"    usage_page=0x{info['usage_page']:04X} usage=0x{info['usage']:04X}")
    print(f"    interface={info['interface_number']}")
print()

# Try to find virtual device (ROOT in path)
virtual = None
for info in hid.enumerate(DS5_VID, DS5_PID):
    path = info['path'].decode('utf-8', errors='replace') if isinstance(info['path'], bytes) else str(info['path'])
    if 'ROOT' in path.upper() or 'root' in path:
        virtual = info
        print(f"Found virtual: {path[:80]}")
        break

if not virtual:
    print("No virtual DS5 found. Using first available.")
    for info in hid.enumerate(DS5_VID, DS5_PID):
        if info['usage_page'] == 0x0001 and info['usage'] == 0x0005:
            virtual = info
            break

if not virtual:
    print("No DS5 found")
    sys.exit(1)

dev = hid.device()
dev.open_path(virtual['path'])

count = 0
start = time.monotonic()
prev_gx, prev_gy, prev_gz = 0, 0, 0
jumps = 0
THRESHOLD = 500

print("Reading from virtual DS5. Hold controller still...")
while time.monotonic() - start < 5.0:
    data = dev.read(64, 50)
    if not data:
        continue
    count += 1
    
    payload = data[1:] if data[0] == 0x01 else data
    if len(payload) >= 27:
        gx = struct.unpack_from('<h', bytes(payload), 15)[0]
        gy = struct.unpack_from('<h', bytes(payload), 17)[0]
        gz = struct.unpack_from('<h', bytes(payload), 19)[0]
        
        dx = abs(gx - prev_gx)
        dy = abs(gy - prev_gy)
        dz = abs(gz - prev_gz)
        
        if count > 1 and (dx > THRESHOLD or dy > THRESHOLD or dz > THRESHOLD):
            jumps += 1
            print(f"JUMP #{jumps} at {count}: GX {prev_gx}->{gx} GY {prev_gy}->{gy} GZ {prev_gz}->{gz}")
        
        prev_gx, prev_gy, prev_gz = gx, gy, gz

elapsed = time.monotonic() - start
print(f"\n{count} packets in {elapsed:.1f}s = {count/elapsed:.0f} Hz, {jumps} jumps")
dev.close()
