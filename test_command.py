"""Test DS5 command/response protocol (SET 0x80 / GET 0x81)."""
import hid
import time

dev = hid.device()
dev.open(0x054C, 0x0CE6)
print(f"Opened: {dev.get_manufacturer_string()} {dev.get_product_string()}")

# Try reading PCBA ID (subcommand from daidr tester)
# sendFeatureReport(0x80, [subcmd, subsub])
# The tester sends: [0x80, subcmd, subsub, 0, 0, ...]

# First, try without CRC (raw hidapi)
subcmd = 0x09  # SYSTEM
subsub = 0x02  # READ_PCBAID (guessed from source)

payload = [0x80] + [subcmd, subsub] + [0] * 61  # 64 bytes total
print(f"\nSending SET 0x80 [{subcmd:#04x}, {subsub:#04x}] (no CRC, {len(payload)}B)")
try:
    dev.send_feature_report(payload)
    print("Sent OK")
except Exception as e:
    print(f"Send error: {e}")

# Poll 0x81
for i in range(20):
    time.sleep(0.01)  # 10ms like the web tester
    resp = dev.get_feature_report(0x81, 64)
    resp_bytes = bytes(resp)
    status = resp_bytes[3] if len(resp_bytes) > 3 else 0
    if resp_bytes[1] == subcmd and resp_bytes[2] == subsub:
        print(f"  Poll {i}: MATCH! status={status:#04x} data={resp_bytes[:20].hex(' ')}")
        if status in (0x00, 0x01, 0x02):  # COMPLETE statuses
            print("  -> COMPLETE!")
            break
    elif any(b != 0 for b in resp_bytes[1:]):
        print(f"  Poll {i}: non-zero but wrong subcmd: {resp_bytes[:10].hex(' ')}")
    else:
        if i == 0:
            print(f"  Poll {i}: all zeros")

print("\n--- Now trying with explicit 64-byte report ---")
payload2 = bytearray(64)
payload2[0] = 0x80
payload2[1] = 0x01  # Different subcmd
payload2[2] = 0x11
print(f"Sending SET 0x80 [0x01, 0x11]")
try:
    dev.send_feature_report(bytes(payload2))
    print("Sent OK")
except Exception as e:
    print(f"Send error: {e}")

for i in range(20):
    time.sleep(0.01)
    resp = dev.get_feature_report(0x81, 64)
    resp_bytes = bytes(resp)
    if resp_bytes[1] == 0x01 and resp_bytes[2] == 0x11:
        print(f"  Poll {i}: MATCH! status={resp_bytes[3]:#04x} data={resp_bytes[:20].hex(' ')}")
        break
    elif any(b != 0 for b in resp_bytes[1:]):
        print(f"  Poll {i}: {resp_bytes[:10].hex(' ')}")

dev.close()
