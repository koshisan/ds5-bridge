import pyaudiowpatch as pyaudio
p = pyaudio.PyAudio()
for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)
    if '2-' in info['name'] or 'DualSense' in info['name']:
        print(f"[{i}] {info['name']}  loop={info.get('isLoopbackDevice')}  ch_in={info['maxInputChannels']}  ch_out={info['maxOutputChannels']}")
p.terminate()
