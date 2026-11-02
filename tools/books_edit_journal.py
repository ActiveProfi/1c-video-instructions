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

# К какому действию сцены относится каждое предложение озвучки (null — фраза без своего клика).
# Используется, если в сценарии у сцены нет поля "anchors".
# Короткие подписи действий для аннотаций.
BOOKS_CAPTIONS = {
    "navigate_purchases": "Раздел «Покупки»",
    "navigate_receipts_link": "Поступление (акты, накладные, УПД)",
    "open_create_menu": "Кнопка «Поступление»",
    "create_goods": "Товары (накладная, УПД)",
    "enter_upd_number": "Номер УПД",
    "focus_upd_date": "Дата УПД",
    "enter_upd_date": "Дата: 11.10.2026",
    "focus_supplier": "Поле «Контрагент»",
    "search_supplier": "Ищем «Альфа»",
    "select_supplier": "Выбираем ООО «Альфа»",
    "add_goods_row": "Кнопка «Добавить»",
    "search_books": "Ищем «Книги»",
    "select_books": "Выбираем «Книги»",
    "enter_quantity": "Количество: 4",
    "focus_price": "Поле «Цена»",
    "select_price_text": "Выделяем цену",
    "enter_price": "Цена: 13 500 ₽",
    "open_price_mode": "Настройка «НДС в сумме»",
    "confirm_price_mode": "Кнопка «ОК»",
    "post_receipt": "Провести",
    "open_upd": "Ссылка на УПД",
    "open_invoice_details": "Все реквизиты",
    "return_receipt": "Назад к поступлению",
    "close_upd_dialog": "Закрываем окно",
    "open_receipt_entries": "Кнопка «Дт/Кт» — проводки",
    "open_invoice_entries": "Движения счёта-фактуры",
}

BOOKS_ANCHORS = [
    [None, None, 0],
    [0, 1, None],
    [0, 1],
    [0, 1, 2, None],
    [None, 0, None, 3, 4, None],
    [None, 0, 1, None, None, 2],
    [0, 1, None, None],
    [None, 0, 2, None],
    [0, None],
]


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
        actions = []
        for label in scene["events"]:
            event = events[label]
            start = (event["start_ms"] - epoch) / 1000 - before
            end = (event["end_ms"] - epoch) / 1000 + after
            windows.append([round(max(0.0, start), 3), round(end, 3)])
            action = {
                "label": label,
                "start": round((event["start_ms"] - epoch) / 1000, 3),
                "end": round((event["end_ms"] - epoch) / 1000, 3),
            }
            caption = event.get("caption") or BOOKS_CAPTIONS.get(label)
            if caption:
                action["caption"] = caption
            if event.get("rect"):
                action["rect"] = event["rect"]
            actions.append(action)
        anchors = scene.get("anchors")
        if anchors is None and index < len(BOOKS_ANCHORS) and spec_path.name == "books-creation-video.json":
            anchors = BOOKS_ANCHORS[index]
        steps.append(
            {
                "id": f"{index}-{scene['events'][0]}",
                "title": scene.get("title") or "",
                "narration": scene["speech"],
                "wav": str(wav),
                "windows": windows,
                "actions": actions,
                "anchors": anchors,
            }
        )
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
