"""Monitor gyro data through the bridge for jumps."""
import socket
import struct
import sys
import time

host = sys.argv[1] if len(sys.argv) > 1 else '192.168.81.88'
port = 5555

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(('0.0.0.0', 0))
sock.settimeout(0.1)

print(f"Listening for input reports forwarded to {host}:{port}")
print("Also receiving from driver on our port...")
print("Move controller slowly. Will flag jumps > 500 units.")
print()

# We need to tap into the input stream. Let's read from a DS5 directly
# and check for jumps before sending
import hid

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
print(f"DS5: {'BT' if is_bt else 'USB'}")

prev_gx, prev_gy, prev_gz = 0, 0, 0
count = 0
jumps = 0
THRESHOLD = 500

while True:
    try:
        data = dev.read(128, 50)
        if not data:
            continue

        if is_bt:
            payload = data[2:] if data[0] == 0x31 else data[1:]
        else:
            payload = data[1:] if data[0] == 0x01 else data

        if len(payload) < 27:
            continue

        gx = struct.unpack_from('<h', bytes(payload), 15)[0]
        gy = struct.unpack_from('<h', bytes(payload), 17)[0]
        gz = struct.unpack_from('<h', bytes(payload), 19)[0]

        count += 1
        dx = abs(gx - prev_gx)
        dy = abs(gy - prev_gy)
        dz = abs(gz - prev_gz)

        if count > 1 and (dx > THRESHOLD or dy > THRESHOLD or dz > THRESHOLD):
            jumps += 1
            print(f"JUMP #{jumps} at pkt {count}: GX {prev_gx:+6d}->{gx:+6d} (d={dx}) GY {prev_gy:+6d}->{gy:+6d} (d={dy}) GZ {prev_gz:+6d}->{gz:+6d} (d={dz})")
        elif count % 200 == 0:
            print(f"  [{count} pkts, {jumps} jumps] GX={gx:+6d} GY={gy:+6d} GZ={gz:+6d}")

        prev_gx, prev_gy, prev_gz = gx, gy, gz

    except KeyboardInterrupt:
        break

print(f"\n{count} packets, {jumps} jumps detected")
dev.close()
