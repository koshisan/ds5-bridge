"""Test DS5 feature report SET 0x80 with BT CRC32."""
import hid
import time
import zlib

def bt_crc32(data, seed=0xA3):
    return zlib.crc32(bytes([seed]) + data) & 0xFFFFFFFF

dev = hid.device()
dev.open(0x054C, 0x0CE6)
print(f"Opened: {dev.get_manufacturer_string()} {dev.get_product_string()}")

before = dev.get_feature_report(0x81, 64)
print(f"Before: {bytes(before[:20]).hex(' ')}")

# Try with CRC32 appended (BT feature report format)
payload = bytearray([0x80] + [0x00] * 63)
crc = bt_crc32(bytes(payload))
payload_crc = payload + bytearray(zlib.crc32(bytes([0xA3]) + bytes(payload)).to_bytes(4, 'little'))
print(f"Sending SET 0x80 with CRC ({len(payload_crc)}B): {bytes(payload_crc[:10]).hex(' ')}...{bytes(payload_crc[-4:]).hex(' ')}")
try:
    dev.send_feature_report(bytes(payload_crc))
    print("Sent OK")
except Exception as e:
    print(f"Send error: {e}")

time.sleep(0.2)

after = dev.get_feature_report(0x81, 64)
print(f"After:  {bytes(after[:20]).hex(' ')}")

if before == after:
    print("\n=> SAME - CRC didn't help either")
else:
    print("\n=> DIFFERENT - Auth works with CRC!")

dev.close()
