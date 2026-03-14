import hid, struct, zlib, math

dev = hid.device()
devs = hid.enumerate(0x054C, 0x0CE6)
bt = [d for d in devs if d.get('usage_page')==1 and d.get('usage')==5]
dev.open_path((bt or devs)[0]['path'])

samples = bytes([int(128+80*math.sin(2*3.14159*150*i/3000)) for i in range(32) for _ in (0,1)])

pkt11 = bytes([(0x11&0x3F)|(1<<7), 7, 0b11111110,0,0,0,0,0,0])
pkt12 = bytes([(0x12&0x3F)|(1<<7), 64]) + samples
payload = (pkt11+pkt12).ljust(136, b'\x00')
body = bytes([0]) + payload
crc = zlib.crc32(bytes([0xA2,0x32])+body) & 0xFFFFFFFF
report = bytes([0x32]) + body + struct.pack('<I', crc)

print('DEMO:', report[:20].hex(' '))
print('FULL:', report.hex(' '))
print(f'LEN: {len(report)}')
dev.write(report)
dev.close()
print('Sent!')
