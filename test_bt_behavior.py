"""Test DS5 haptic motor behavior - how does it interpret the samples?"""
import sys
import time
import struct
import zlib
import hid

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
if len(test) <= 64:
    print("Not BT!"); sys.exit(1)

def ds5_bt_crc32(data):
    return zlib.crc32(bytes([0xA2]) + data) & 0xFFFFFFFF

seq = 0
def send_haptic(audio_64bytes):
    global seq
    REPORT_ID = 0x32
    pkt_0x11 = bytes([(0x11 & 0x3F)|(1<<7), 7, 0b11111110, 0, 0, 0, 0, seq & 0xFF, 0])
    pkt_0x12 = bytes([(0x12 & 0x3F)|(1<<7), 64])
    payload = (pkt_0x11 + pkt_0x12 + audio_64bytes).ljust(136, b'\x00')
    tag_seq = (seq & 0x0F) << 4
    body = bytes([tag_seq]) + payload
    crc = ds5_bt_crc32(bytes([REPORT_ID]) + body)
    report = bytes([REPORT_ID]) + body + struct.pack('<I', crc)
    dev.write(report)
    seq = (seq + 1) & 0x0F

def silence():
    return bytes(64)  # all 128? no, all 0 = center... wait
    # u8 center = 128
    return bytes([128] * 64)

def constant(val):
    """All 32 stereo samples = same value."""
    return bytes([val] * 64)

def ramp_up():
    """Ramp from 128 to 255 over 32 samples."""
    d = bytearray(64)
    for i in range(32):
        v = 128 + int(127 * i / 31)
        d[i*2] = v
        d[i*2+1] = v
    return bytes(d)

def ramp_down():
    """Ramp from 255 to 128 over 32 samples."""
    d = bytearray(64)
    for i in range(32):
        v = 255 - int(127 * i / 31)
        d[i*2] = v
        d[i*2+1] = v
    return bytes(d)

input("Press Enter to start tests (hold controller in hand)...")

# Test 1: Single pulse - one packet at max, then silence
print("\n=== TEST 1: Single packet at 255, then 2s silence ===")
print("  Does it: (a) pulse once, (b) vibrate until next packet?")
send_haptic(constant(255))
time.sleep(2)
send_haptic(silence())
time.sleep(0.5)

input("What happened? Press Enter for next test...")

# Test 2: Constant value - send 255 repeatedly for 2s
print("\n=== TEST 2: Constant 255, 93.75 Hz for 2s ===")
print("  Continuous vibration?")
start = time.perf_counter()
while time.perf_counter() - start < 2.0:
    send_haptic(constant(255))
    time.sleep(0.01067)
send_haptic(silence())
time.sleep(0.5)

input("What happened? Press Enter for next test...")

# Test 3: Constant value - send 255 repeatedly but SLOW (2 Hz)
print("\n=== TEST 3: Constant 255, only 2 packets/sec for 3s ===")
print("  Continuous or pulsing?")
start = time.perf_counter()
while time.perf_counter() - start < 3.0:
    send_haptic(constant(255))
    time.sleep(0.5)
send_haptic(silence())
time.sleep(0.5)

input("What happened? Press Enter for next test...")

# Test 4: Half intensity
print("\n=== TEST 4: Constant 192 (half amplitude) for 2s ===")
print("  Weaker vibration?")
start = time.perf_counter()
while time.perf_counter() - start < 2.0:
    send_haptic(constant(192))
    time.sleep(0.01067)
send_haptic(silence())
time.sleep(0.5)

input("What happened? Press Enter for next test...")

# Test 5: Alternating 0 and 255 within one packet
print("\n=== TEST 5: Alternating 0/255 within each packet for 2s ===")
print("  Maximum frequency signal within one packet")
alt = bytearray(64)
for i in range(32):
    v = 255 if i % 2 == 0 else 0
    alt[i*2] = v
    alt[i*2+1] = v
start = time.perf_counter()
while time.perf_counter() - start < 2.0:
    send_haptic(bytes(alt))
    time.sleep(0.01067)
send_haptic(silence())
time.sleep(0.5)

input("What happened? Press Enter for next test...")

# Test 6: Ramp up then constant
print("\n=== TEST 6: One ramp-up packet, then constant 255 for 2s ===")
send_haptic(ramp_up())
start = time.perf_counter()
while time.perf_counter() - start < 2.0:
    send_haptic(constant(255))
    time.sleep(0.01067)
send_haptic(silence())
time.sleep(0.5)

input("What happened? Press Enter for next test...")

# Test 7: Send 1 packet then NOTHING for 5 seconds
print("\n=== TEST 7: One packet at 255, then NO MORE PACKETS for 5s ===")
print("  When does vibration stop?")
send_haptic(constant(255))
print("  Packet sent. Waiting 5 seconds...")
time.sleep(5)
print("  Done. Did it stop immediately or continue?")

dev.close()
print("\nAll tests done.")
