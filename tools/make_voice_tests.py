"""Create five standalone voice auditions; never rebuild the lesson video."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools/voice-runtime'))
sys.path.insert(0, str(ROOT/'tools/media-runtime'))
import edge_tts
import imageio_ffmpeg

OUT = ROOT/'artifacts/voice-tests'
TEXT = 'В этом видео разберём, как создать документ поступления товаров в программе один эс: Бухгалтерия предприятия, редакция три ноль. На примере книг оформим УПД и посмотрим проводки. Начнём с раздела Покупки.'
VOICES = [('1-andrew','en-US-AndrewMultilingualNeural'),('2-brian','en-US-BrianMultilingualNeural'),('3-remy','fr-FR-RemyMultilingualNeural'),('4-florian','de-DE-FlorianMultilingualNeural'),('5-giuseppe','it-IT-GiuseppeMultilingualNeural')]
if '--female' in sys.argv:
    OUT = ROOT/'artifacts/voice-tests-female'
    catalog = json.loads((ROOT/'config/voices.json').read_text(encoding='utf-8-sig'))
    female = [v for v in catalog['voices'] if v['gender']=='Female']
    VOICES = [(f'{i+1}-{v["id"]}',v['id']) for i,v in enumerate(female)]
    if len(VOICES)!=5:
        raise ValueError('Exactly five female auditions are required')

async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/'samples.json').write_text(json.dumps({'text':TEXT,'speed':1,'voices':VOICES},ensure_ascii=False,indent=2),encoding='utf-8')
    pending = list(VOICES)
    attempts = 2 if '--female-extra' in sys.argv else 5
    for attempt in range(attempts):
        remaining = []
        for label, voice in pending:
            output = OUT/f'{label}.mp3'
            if output.exists() and output.stat().st_size>1000:
                result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-i',str(output),'-f','null','-'],capture_output=True)
                if result.returncode == 0:
                    print(f'{label}: cached and decoded',flush=True)
                    continue
            try:
                await edge_tts.Communicate(TEXT,voice,rate='+0%',pitch='+0Hz').save(str(output))
                result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-i',str(output),'-f','null','-'],capture_output=True)
                if result.returncode or output.stat().st_size<1000:
                    raise RuntimeError('Audio verification failed')
                print(f'{label}: ready and decoded',flush=True)
            except Exception as error:
                print(f'{label}: {type(error).__name__}',flush=True)
                remaining.append((label,voice))
        pending = remaining
        if not pending:
            print('All five samples ready',flush=True)
            return
        if attempt < attempts-1:
            await asyncio.sleep(10)
    raise RuntimeError('Unavailable samples: '+', '.join(x[0] for x in pending))

if __name__=='__main__':
    if '--female-extra' in sys.argv:
        OUT = ROOT/'artifacts/voice-tests-female-extra'
        VOICES = [('1-dalia','es-MX-DaliaMultilingualNeural'),('2-arabella','es-ES-ArabellaMultilingualNeural'),('3-isabella','it-IT-IsabellaMultilingualNeural'),('4-ximena','es-ES-XimenaMultilingualNeural')]
    asyncio.run(main())
