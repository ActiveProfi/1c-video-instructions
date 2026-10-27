"""Record a user-authorized screen region. Does not control any application."""
from pathlib import Path
import json
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'media-runtime'))
import imageio_ffmpeg

folder = ROOT / 'artifacts' / 'books-creation'
folder.mkdir(parents=True, exist_ok=True)
stop = folder / 'stop-recording'
if stop.exists():
    raise RuntimeError('Use a new recording directory: stop file already exists')
with (folder / 'capture.log').open('w', encoding='utf-8') as log:
    proc = subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-y', '-f', 'gdigrab', '-framerate', '20', '-offset_x', '323', '-offset_y', '19', '-video_size', '1438x754', '-draw_mouse', '1', '-i', 'desktop', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', '-pix_fmt', 'yuv420p', str(folder / 'raw.mp4')], stdin=subprocess.PIPE, stdout=log, stderr=log)
    (folder / 'capture-start.json').write_text(json.dumps({'epoch_ms': int(time.time()*1000), 'region': [323, 19, 1438, 754]}), encoding='utf-8')
    print('Recording started', flush=True)
    deadline = time.time() + 900
    while proc.poll() is None and not stop.exists() and time.time() < deadline:
        time.sleep(.2)
    if proc.poll() is None:
        proc.communicate(b'q\n', timeout=30)
    if proc.returncode:
        raise RuntimeError('Capture failed; see capture.log')
print('Recording saved', flush=True)
