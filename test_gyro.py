"""Compare BT vs USB gyro data layout."""
import hid
import struct
import sys
import time

DS5_VID = 0x054C
DS5_PIDS = {0x0CE6, 0x0DF2}

dev_info = None
for info in hid.enumerate(DS5_VID):
    if info['product_id'] in DS5_PIDS:
        dev_info = info
        break

if not dev_info:
    print("No DS5 found")
    sys.exit(1)

dev = hid.device()
dev.open_path(dev_info['path'])
test = dev.read(128, 500)
is_bt = len(test) > 64 if test else False
print(f"Connection: {'BT' if is_bt else 'USB'}, raw report size: {len(test)}")
print(f"First bytes: {bytes(test[:10]).hex(' ')}")
print()

# DS5 USB input report layout (after report ID 0x01):
# Offset 0: LX, 1: LY, 2: RX, 3: RY
# Offset 7: Buttons
# Offset 15-16: Gyro X (s16 LE)
# Offset 17-18: Gyro Y (s16 LE)
# Offset 19-20: Gyro Z (s16 LE)
# Offset 21-22: Accel X (s16 LE)
# Offset 23-24: Accel Y (s16 LE)
# Offset 25-26: Accel Z (s16 LE)

print("Hold controller still. Reading 20 samples...")
print(f"{'#':>3} {'Raw[0:4]':>12} {'GX':>7} {'GY':>7} {'GZ':>7} {'AX':>7} {'AY':>7} {'AZ':>7}")

for i in range(20):
    data = dev.read(128, 100)
    if not data:
        continue
    
    if is_bt:
        # BT: data[0]=0x31, data[1]=??, data[2:] = USB-like payload
        raw = bytes(data)
        payload = data[2:] if data[0] == 0x31 else data[1:]
    else:
        # USB: data[0]=0x01, data[1:] = payload
        raw = bytes(data)
        payload = data[1:] if data[0] == 0x01 else data
    
    if len(payload) >= 27:
        gx = struct.unpack_from('<h', bytes(payload), 15)[0]
        gy = struct.unpack_from('<h', bytes(payload), 17)[0]
        gz = struct.unpack_from('<h', bytes(payload), 19)[0]
        ax = struct.unpack_from('<h', bytes(payload), 21)[0]
        ay = struct.unpack_from('<h', bytes(payload), 23)[0]
        az = struct.unpack_from('<h', bytes(payload), 25)[0]
        first4 = bytes(payload[:4]).hex(' ')
        print(f"{i:3d} {first4:>12} {gx:7d} {gy:7d} {gz:7d} {ax:7d} {ay:7d} {az:7d}")
    
    time.sleep(0.05)

print()
print("Now try different offsets to find correct gyro position...")
print()

data = dev.read(128, 100)
if data:
    if is_bt:
        payload = data[2:] if data[0] == 0x31 else data[1:]
    else:
        payload = data[1:] if data[0] == 0x01 else data
    
    p = bytes(payload)
    print(f"Payload length: {len(p)}")
    print(f"Payload hex: {p[:40].hex(' ')}")
    print()
    
    # Try different offsets for gyro - look for reasonable values (near 0 when still, ~±16000 for gravity on accel)
    print("Scanning for gyro/accel at different offsets:")
    for off in range(10, min(35, len(p)-5)):
        vals = [struct.unpack_from('<h', p, off + i*2)[0] for i in range(6)]
        # Accel should show ~±8000-16000 on one axis (gravity)
        has_gravity = any(abs(v) > 5000 for v in vals[3:6])
        marker = " <-- GRAVITY?" if has_gravity else ""
        print(f"  offset {off:2d}: {vals[0]:7d} {vals[1]:7d} {vals[2]:7d} | {vals[3]:7d} {vals[4]:7d} {vals[5]:7d}{marker}")

dev.close()
