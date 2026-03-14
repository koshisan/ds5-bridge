import sounddevice as sd
for i, d in enumerate(sd.query_devices()):
    if 'DualSense' in d['name']:
        api = sd.query_hostapis(d['hostapi'])['name']
        print(f"[{i}] {d['name']} in={d['max_input_channels']} out={d['max_output_channels']} ({api})")
