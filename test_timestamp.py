"""Check DS5 timestamps - look for jumps or inconsistencies."""
import hid, struct, time, sys

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
is_bt = len(test) > 64
print(f"{'BT' if is_bt else 'USB'}, report={len(test)}B")

prev_ts = 0
prev_seq = 0
count = 0
ts_jumps = 0

for _ in range(500):
    data = dev.read(128, 100)
    if not data:
        continue
    
    if is_bt:
        p = data[2:] if data[0] == 0x31 else data[1:]
    else:
        p = data[1:] if data[0] == 0x01 else data
    
    if len(p) < 15:
        continue

    seq = p[6]  # sequence counter at offset 6 in payload
    ts = struct.unpack_from('<I', bytes(p), 7)[0]  # timestamp at offset 7-10
    
    count += 1
    if count > 1:
        dt = (ts - prev_ts) & 0xFFFFFFFF
        if dt > 100000 or dt == 0:  # >100ms or zero = suspicious
            ts_jumps += 1
            print(f"TS JUMP #{ts_jumps} at {count}: seq {prev_seq}->{seq} ts {prev_ts}->{ts} dt={dt}")
        elif count <= 10 or count % 100 == 0:
            print(f"  [{count}] seq={seq} ts={ts} dt={dt}")
    else:
        print(f"  [{count}] seq={seq} ts={ts} (first)")
    
    prev_ts = ts
    prev_seq = seq

print(f"\n{count} packets, {ts_jumps} timestamp jumps")
dev.close()
