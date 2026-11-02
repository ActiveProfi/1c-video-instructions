"""Журнал для монтажа из непрерывной записи урока про книги (artifacts/books-creation).

Берёт raw.mp4, events.json, capture-start.json, сценарий и уже готовую озвучку
(кэш wav из папки голоса). Новых запросов к сервисам озвучки не делает.

    py -3 tools/books_edit_journal.py --out artifacts/books-continuous-2026-11-02
    py -3 -m pipeline edit --journal artifacts/books-continuous-2026-11-02/edit-journal.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(media: Path, spec_path: Path, out: Path, before: float = 0.25, after: float = 1.5) -> Path:
    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    events = {item["label"]: item for item in json.loads((media / "events.json").read_text(encoding="utf-8"))}
    epoch = json.loads((media / "capture-start.json").read_text(encoding="utf-8"))["epoch_ms"]
    voice_dir = media / spec.get("audio_directory", ".")
    steps = []
    for index, scene in enumerate(spec["scenes"]):
        wav = voice_dir / f"voice-{index}.wav"
        if not wav.exists():
            raise FileNotFoundError(f"Нет озвучки сцены {index}: {wav}")
        windows = []
        for label in scene["events"]:
            event = events[label]
            start = (event["start_ms"] - epoch) / 1000 - before
            end = (event["end_ms"] - epoch) / 1000 + after
            windows.append([round(max(0.0, start), 3), round(end, 3)])
        steps.append({"id": f"{index}-{scene['events'][0]}", "narration": scene["speech"], "wav": str(wav), "windows": windows})
    out.mkdir(parents=True, exist_ok=True)
    journal = out / "edit-journal.json"
    payload = {"raw": str(media / "raw.mp4"), "voice": spec.get("voice"), "voice_provider": spec.get("voice_provider"), "steps": steps}
    journal.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return journal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media", default=str(ROOT / "artifacts" / "books-creation"))
    parser.add_argument("--spec", default=str(ROOT / "scenarios" / "books-creation-video.json"))
    parser.add_argument("--out", default=str(ROOT / "artifacts" / "books-continuous"))
    args = parser.parse_args()
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    print(build(Path(args.media), Path(args.spec), out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
