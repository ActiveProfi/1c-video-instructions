"""Generate a male neural narration, without using desktop SAPI voices."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'/'voice-runtime'))
sys.path.insert(0, str(ROOT/'tools'/'media-runtime'))
import edge_tts
import imageio_ffmpeg
from pronunciation import pronounce

SPEC = json.loads((ROOT/'scenarios/books-creation-video.json').read_text(encoding='utf-8-sig'))
OUTPUT = ROOT/'artifacts'/'books-creation'/SPEC['audio_directory']


async def main():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    voices = await edge_tts.list_voices()
    matching = [voice for voice in voices if voice['ShortName']==SPEC['voice'] and voice['Gender']==SPEC.get('voice_gender','Male')]
    if len(matching)!=1:
        raise RuntimeError('Requested voice is unavailable; no fallback to SAPI.')
    for i, scene in enumerate(SPEC['scenes']):
        speech = pronounce(scene['speech'])
        signature = hashlib.sha256((SPEC['voice']+'\n'+speech).encode()).hexdigest()
        cache = OUTPUT/f'voice-{i}.json'
        wav = OUTPUT/f'voice-{i}.wav'
        if cache.exists() and wav.exists() and json.loads(cache.read_text())['signature']==signature:
            print(f'Voice {i}: cached',flush=True)
            continue
        mp3 = OUTPUT/f'voice-{i}.mp3'
        for attempt in range(4):
            try:
                await edge_tts.Communicate(speech,SPEC['voice'],rate='+0%',pitch='+0Hz').save(str(mp3))
                break
            except Exception as error:
                if attempt == 3:
                    raise RuntimeError(f'Voice {i}: service unavailable ({type(error).__name__})') from None
                print(f'Voice {i}: retry {attempt+1} ({type(error).__name__})',flush=True)
                await asyncio.sleep([5,15,30][attempt])
        result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-y','-i',str(mp3),'-ar','24000','-ac','1',str(wav)],capture_output=True,text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        cache.write_text(json.dumps({'signature':signature,'voice':SPEC['voice'],'provider':'edge-tts'},indent=2),encoding='utf-8')
        print(f'Voice {i}: generated',flush=True)
    print('Neural narration ready',flush=True)


if __name__=='__main__':
    asyncio.run(main())
