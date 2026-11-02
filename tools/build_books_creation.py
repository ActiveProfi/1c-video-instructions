"""Accelerate recorded actions while retaining natural narration speed."""
from pathlib import Path
import json
import subprocess
import sys
import wave
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / 'artifacts' / 'books-creation'
sys.path.insert(0, str(ROOT / 'tools' / 'media-runtime'))
import imageio_ffmpeg
FF = imageio_ffmpeg.get_ffmpeg_exe()
SPEC = json.loads((ROOT/'scenarios/books-creation-video.json').read_text(encoding='utf-8-sig'))
EVENTS = {x['label']:x for x in json.loads((MEDIA/'events.json').read_text())}
EPOCH = json.loads((MEDIA/'capture-start.json').read_text())['epoch_ms']


def run(args):
    p = subprocess.run([FF, '-hide_banner', '-loglevel', 'error', '-y', *args], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if p.returncode:
        raise RuntimeError(p.stderr)


def main():
    if SPEC.get('intro_required') and not SPEC['scenes'][0]['speech'].startswith('В этом видео'):
        raise ValueError('The lesson must start with a spoken introduction.')
    kept = 0
    duration_total = 0
    for i, scene in enumerate(SPEC['scenes']):
        clips = []
        for j, label in enumerate(scene['events']):
            event = EVENTS[label]
            start = max(0, (event['start_ms']-EPOCH)/1000-.25)
            duration = (event['end_ms']-event['start_ms'])/1000+.25+1.25
            # UPD details dialog has a brief animation before it becomes stable.
            if label == 'open_upd':
                duration += .5
            kept += duration
            clip = f'cut-{i}-{j}.mp4'
            run(['-ss', f'{start:.3f}', '-t', f'{duration:.3f}', '-i', str(MEDIA/'raw.mp4'), '-an', '-vf', f'setpts=(PTS-STARTPTS)/{SPEC["speed"]},setsar=1', '-c:v', 'libx264', '-preset', 'fast', '-crf', '19', '-pix_fmt', 'yuv420p', str(MEDIA/clip)])
            clips.append(clip)
        playlist = MEDIA/f'clips-{i}.txt'
        playlist.write_text(''.join(f"file '{clip}'\n" for clip in clips), encoding='utf-8')
        sequence = MEDIA/f'sequence-{i}.mp4'
        run(['-f','concat','-safe','0','-i',str(playlist),'-c','copy',str(sequence)])
        voice = MEDIA / SPEC.get('audio_directory', '.') / f'voice-{i}.wav'
        with wave.open(str(voice)) as w:
            voice_duration = w.getnframes()/w.getframerate()
        # Retain readable actions and only hold the last frame for narration.
        estimated = sum((EVENTS[x]['end_ms']-EVENTS[x]['start_ms'])/1000+1.5+(.5 if x=='open_upd' else 0) for x in scene['events'])
        duration = max(estimated/SPEC['speed'], voice_duration+.4)
        duration_total += duration
        overlay = Image.new('RGBA',(1438,850),(0,0,0,0))
        d = ImageDraw.Draw(overlay)
        d.rectangle((0,754,1438,850),fill='#101827')
        font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf',27)
        label = scene['title'] if i == 0 else f'{i}. {scene["title"]}'
        d.text((28,773),label,font=font,fill='white')
        d.text((28,813),'Учебная база · пошаговая запись создания документа',font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',18),fill='#bdc9da')
        overlay_path = MEDIA/f'caption-{i}.png'
        overlay.save(overlay_path)
        run(['-i',str(sequence),'-i',str(overlay_path),'-i',str(voice),'-filter_complex','[0:v]tpad=stop_mode=clone:stop_duration=60,pad=1438:850:0:0:color=0x101827[v];[v][1:v]overlay=0:0[out]','-map','[out]','-map','2:a','-t',f'{duration:.3f}','-af','apad','-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p','-c:a','aac','-b:a','160k',str(MEDIA/f'chapter-{i}.mp4')])
    playlist = MEDIA/'chapters.txt'
    playlist.write_text(''.join(f"file 'chapter-{i}.mp4'\n" for i in range(len(SPEC['scenes']))),encoding='utf-8')
    normal = MEDIA/'edited-normal-speed.mp4'
    run(['-f','concat','-safe','0','-i',str(playlist),'-c','copy',str(normal)])
    output = MEDIA/SPEC.get('output_filename','books-upd-instruction-1.1x.mp4')
    run(['-i',str(normal),'-c','copy','-movflags','+faststart',str(output)])
    run(['-i',str(output),'-map','0:v:0','-map','0:a:0','-f','null','-'])
    run(['-ss','7','-i',str(output),'-frames:v','1',str(MEDIA/'qa-start.png')])
    run(['-ss','40','-i',str(output),'-frames:v','1',str(MEDIA/'qa-middle.png')])
    report = {'status':'decode_verified','source':'actual_screen_recording','voice':SPEC['voice'],'voice_provider':SPEC.get('voice_provider'),'spoken_intro':SPEC['scenes'][0]['speech'],'action_speed':SPEC['speed'],'narration_speed':1.0,'video':str(output),'approx_duration_seconds':round(duration_total,1),'actions_included':sum(len(s['events']) for s in SPEC['scenes']),'retained_action_seconds_before_narration':round(kept,1),'receipt_number':'0000-000019','incoming_upd_number':'УЧ-КНИГИ-002'}
    (MEDIA/'video-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    main()
