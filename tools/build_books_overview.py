"""Render a narrated overview from captured lesson screens; no UI automation."""
import json
from pathlib import Path
import subprocess
import sys
import wave

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'media-runtime'))
import imageio_ffmpeg

MEDIA = ROOT / 'artifacts' / 'books-lesson'
SPEC = json.loads((ROOT / 'scenarios' / 'books-overview.json').read_text(encoding='utf-8-sig'))
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FONT_PATH = 'C:/Windows/Fonts/arial.ttf'


def run(args):
    result = subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', *args], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(result.stderr)


def fit_font(text, size, max_width):
    while size > 16:
        font = ImageFont.truetype(FONT_PATH, size)
        if font.getlength(text) <= max_width:
            return font
        size -= 1
    return font


def main():
    durations = []
    for i, scene in enumerate(SPEC['scenes']):
        source = Image.open(MEDIA / scene['image']).convert('RGB')
        canvas = Image.new('RGB', (1920, 1080), '#101827')
        draw = ImageDraw.Draw(canvas)
        draw.text((55, 30), scene['title'], fill='white', font=ImageFont.truetype(FONT_PATH, 38))
        draw.text((55, 84), 'Обзор готового учебного примера · 1С:Бухгалтерия 3.0', fill='#b5c3d8', font=ImageFont.truetype(FONT_PATH, 22))
        scale = min(1810 / source.width, 825 / source.height)
        resized = source.resize((round(source.width * scale), round(source.height * scale)), Image.Resampling.LANCZOS)
        x, y = (1920 - resized.width) // 2, 132
        canvas.paste(resized, (x, y))
        box = [x + round(scene['box'][j] * scale) if j % 2 == 0 else y + round(scene['box'][j] * scale) for j in range(4)]
        draw.rounded_rectangle(box, radius=6, outline='#f6a700', width=5)
        draw.text((55, 992), scene['caption'], fill='#ffdf80', font=fit_font(scene['caption'], 29, 1810))
        frame = MEDIA / f'frame-{i}.png'
        canvas.save(frame)
        voice = MEDIA / f'voice-{i}.wav'
        with wave.open(str(voice)) as wav:
            duration = wav.getnframes() / wav.getframerate() + 1.2
        durations.append(duration)
        run(['-loop', '1', '-framerate', '15', '-i', str(frame), '-i', str(voice), '-t', f'{duration:.3f}', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-af', 'apad', str(MEDIA / f'part-{i}.mp4')])
    playlist = MEDIA / 'parts.txt'
    playlist.write_text(''.join(f"file 'part-{i}.mp4'\n" for i in range(len(durations))), encoding='utf-8')
    output = MEDIA / 'books-upd-overview.mp4'
    run(['-f', 'concat', '-safe', '0', '-i', str(playlist), '-c', 'copy', '-movflags', '+faststart', str(output)])
    # Decode both streams end to end; a file merely existing is insufficient.
    run(['-i', str(output), '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'])
    report = {'status': 'verified', 'format': 'narrated_screenshot_overview', 'video': str(output), 'duration_seconds': round(sum(durations), 2), 'continuous_input_recording': False, 'codec': 'H264/AAC', 'screens': len(durations)}
    (MEDIA / 'video-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
