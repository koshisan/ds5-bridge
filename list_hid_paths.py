import hid
for d in hid.enumerate():
    if d['vendor_id'] == 0x054C and d['product_id'] == 0x0CE6:
        print(f"VID/PID: {d['vendor_id']:04x}:{d['product_id']:04x}")
        print(f"  Path: {d['path']}")
        print(f"  Usage: page={d['usage_page']:#06x} usage={d['usage']:#06x}")
        print(f"  Interface: {d['interface_number']}")
        print()
