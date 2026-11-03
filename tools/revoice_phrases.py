"""Переозвучить только предложения, где словарь произношения меняет критичные слова.

Остальная озвучка берётся из готовых wav сцен. Результат: wav для каждого такого
предложения в <папка голоса>/phrases/ и phrases.json (какое предложение чем заменено).
Ключ ElevenLabs берётся из .env и не печатается.

    py -3 tools/revoice_phrases.py --dry-run
    py -3 tools/revoice_phrases.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.postprod import sentences  # noqa: E402
from pipeline.pronunciation import pronounce, revoice_needed  # noqa: E402


def plan(spec: dict, voice_dir: Path, voice: str, model: str) -> list[dict]:
    items = []
    for index, scene in enumerate(spec["scenes"]):
        parts = sentences(scene["speech"])
        for number, text in enumerate(parts):
            hits = revoice_needed(text)
            if not hits:
                continue
            speech = pronounce(text)
            key = hashlib.sha256(f"{voice}\n{model}\n{speech}".encode("utf-8")).hexdigest()[:16]
            items.append(
                {
                    "scene": index,
                    "sentence": number,
                    "text": text,
                    "speech": speech,
                    "terms": hits,
                    "previous_text": pronounce(parts[number - 1]) if number else "",
                    "next_text": pronounce(parts[number + 1]) if number + 1 < len(parts) else "",
                    "wav": str(voice_dir / "phrases" / f"scene-{index}-{number}-{key}.wav"),
                }
            )
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", default=str(ROOT / "scenarios" / "books-creation-video.json"))
    parser.add_argument("--media", default=str(ROOT / "artifacts" / "books-creation"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    from pipeline.tts import read_dotenv, synthesize_elevenlabs

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
    voice = spec.get("voice") or "pFZP5JQG7iQjIQuC4Bku"
    model = read_dotenv(ROOT).get("ELEVENLABS_MODEL_ID") or "eleven_multilingual_v2"
    voice_dir = Path(args.media) / spec.get("audio_directory", ".")
    items = plan(spec, voice_dir, voice, model)
    spent = 0
    for item in items:
        wav = Path(item["wav"])
        cached = wav.exists() and wav.stat().st_size > 1000
        item["cached"] = cached
        print(f"сцена {item['scene']} предложение {item['sentence']} ({', '.join(item['terms'])}): {'кэш' if cached else 'озвучить'} — {item['speech']}", flush=True)
        if cached or args.dry_run:
            continue
        synthesize_elevenlabs(item["speech"], voice, wav, ROOT, previous_text=item["previous_text"], next_text=item["next_text"])
        spent += len(item["speech"])
    (voice_dir / "phrases").mkdir(parents=True, exist_ok=True)
    (voice_dir / "phrases" / "phrases.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Символов отправлено в ElevenLabs: {spent}" + (" (пробный прогон)" if args.dry_run else ""), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
