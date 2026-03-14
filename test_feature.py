"""Test DS5 feature report SET 0x80 / GET 0x81 via hidapi."""
import hid
import time

dev = hid.device()
dev.open(0x054C, 0x0CE6)
print(f"Opened: {dev.get_manufacturer_string()} {dev.get_product_string()}")

# Read 0x81 before SET
before = dev.get_feature_report(0x81, 64)
print(f"Before SET: {bytes(before[:20]).hex(' ')}")

# Send SET 0x80 with dummy challenge
dev.send_feature_report([0x80] + [0x00] * 63)
print("Sent SET 0x80")

# Wait for crypto chip
time.sleep(0.2)

# Read 0x81 after SET
after = dev.get_feature_report(0x81, 64)
print(f"After SET:  {bytes(after[:20]).hex(' ')}")

# Check if different
if before == after:
    print("\n=> SAME - SET had no effect (CRC issue? hidapi limitation?)")
else:
    print("\n=> DIFFERENT - Auth works over hidapi!")

dev.close()
