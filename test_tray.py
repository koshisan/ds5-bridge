import pystray
from PIL import Image, ImageDraw

img = Image.new('RGBA', (64, 64), (0, 200, 0, 255))
icon = pystray.Icon('test', img, 'Test')
print("Starting icon.run()...")
icon.run()
print("icon.run() returned!")
