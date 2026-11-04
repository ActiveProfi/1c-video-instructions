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
    [None, 0],  # номер и дата вводятся во второй фразе
    [0, 1, None, None],  # «выбираем его из списка» — во второй фразе
    [None, 0, None, 3, 4, None],
    [None, 0, 1, None, None, 2],
    [0, None, None, None],  # «Все реквизиты» звучит в первой фразе
    [None, 0, None, None],  # кнопка «Дебет Кредит» — во второй фразе, не в «Первая строка»
    [0, None],
]

# Слово из фразы, на котором происходит действие. Действия внутри такта ставятся на него.
# Для старой записи (без UIA-координат) детекция по кадрам здесь ошибается:
# всплывающие списки закрываются раньше, чем заканчивается фраза, или рамка уходит не туда.
# Лучше без рамки, чем кривая рамка.
BOOKS_NO_BOX = {"focus_price", "search_books", "select_books", "close_upd_dialog", "open_receipt_entries"}

# Наведения курсора без клика (сцена -> список): слово из фразы и рамка элемента в координатах записи.
# Старая запись без UI Automation, поэтому рамки размечены по кадрам вручную (1438x754).
BOOKS_POINTS = {
    1: [{"label": "Новый документ", "say": "откроется новый документ", "rect": [312, 70, 762, 98]}],
    2: [{"label": "Номер и дата УПД", "say": "сначала укажем номер", "rect": [316, 136, 586, 170]}],
    3: [{"label": "Договор", "say": "договор подставился", "rect": [323, 252, 734, 279]},
        {"label": "Признак УПД", "say": "признак упд", "rect": [198, 654, 266, 678]}],
    4: [{"label": "Кнопка «Добавить»", "say": "добавим товар", "rect": [200, 347, 303, 373]},
        {"label": "Единица: упаковка", "say": "единица", "rect": [733, 414, 871, 445]},
        {"label": "Ставка НДС", "say": "ставку ндс", "rect": [1180, 414, 1271, 445]}],
    5: [{"label": "Цена с НДС", "say": "цена у нас", "rect": [873, 414, 1060, 445]},
        {"label": "Всего и НДС", "say": "всего получилось", "rect": [1004, 656, 1411, 689]},
        {"label": "Признак УПД", "say": "признак упд включ", "rect": [198, 654, 266, 678]}],
    6: [{"label": "Счёт-фактура", "say": "полученный счёт-фактура", "rect": [344, 140, 496, 170]},
        {"label": "Номер и дата", "say": "проверим номер", "rect": [344, 140, 640, 170]},
        {"label": "Суммы", "say": "и суммы", "rect": [200, 349, 1177, 378]},
        {"label": "Флажок вычета НДС", "say": "флажок вычета", "rect": [526, 174, 893, 196]}],
    7: [{"label": "Первая проводка", "say": "первая строка", "rect": [208, 272, 1397, 383]},
        {"label": "Вторая проводка", "say": "вторая —", "rect": [208, 390, 1397, 501]}],
    8: [{"label": "Проводка вычета НДС", "say": "видим вычет", "rect": [208, 272, 1397, 383]}],
}

BOOKS_SAY = {
    "navigate_purchases": "начнём с раздела",
    "navigate_receipts_link": "здесь открываем",
    "open_create_menu": "нажимаем поступление",
    "create_goods": "выбираем товары",
    "enter_upd_number": "вводим учебный номер",
    "focus_upd_date": "и дату",
    "enter_upd_date": "одиннадцатое",
    "focus_supplier": "теперь выберем",
    "search_supplier": "начинаем вводить",
    "select_supplier": "выбираем его",
    "add_goods_row": "нажимаем добавить",
    "search_books": "вводим книги",
    "select_books": "выбираем нужную",
    "enter_quantity": "указываем количество",
    "focus_price": "цена упаковки",
    "select_price_text": "тринадцать",
    "enter_price": "пятьсот рублей",
    "open_price_mode": "открываем настройку",
    "confirm_price_mode": "нажимаем ок",
    "post_receipt": "можно проводить",
    "open_upd": "нажмём на ссылку",
    "open_invoice_details": "все реквизиты",
    "return_receipt": "возвращаемся",
    "close_upd_dialog": "в поступление",
    "open_receipt_entries": "нажимаем кнопку",
    "open_invoice_entries": "а в движениях",
}


def build(media: Path, spec_path: Path, out: Path, before: float = 0.25, after: float = 1.5) -> Path:
    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    events = {item["label"]: item for item in json.loads((media / "events.json").read_text(encoding="utf-8"))}
    epoch = json.loads((media / "capture-start.json").read_text(encoding="utf-8"))["epoch_ms"]
    voice_dir = media / spec.get("audio_directory", ".")
    phrases_file = voice_dir / "phrases" / "phrases.json"
    revoiced = json.loads(phrases_file.read_text(encoding="utf-8")) if phrases_file.exists() else []
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
            say = event.get("say") or BOOKS_SAY.get(label)
            if say:
                action["say"] = say
            caption = event.get("caption") or BOOKS_CAPTIONS.get(label)
            if caption:
                action["caption"] = caption
            if event.get("rect"):
                action["rect"] = event["rect"]
            elif label in BOOKS_NO_BOX:
                action["box"] = False
            actions.append(action)
        anchors = scene.get("anchors")
        if anchors is None and index < len(BOOKS_ANCHORS) and spec_path.name == "books-creation-video.json":
            anchors = BOOKS_ANCHORS[index]
        point_at = scene.get("point_at")
        if point_at is None and spec_path.name == "books-creation-video.json":
            point_at = BOOKS_POINTS.get(index, [])
        phrase_wavs = {str(p["sentence"]): p["wav"] for p in revoiced if p["scene"] == index and Path(p["wav"]).exists()}
        steps.append(
            {
                "id": f"{index}-{scene['events'][0]}",
                "title": scene.get("title") or "",
                "narration": scene["speech"],
                "wav": str(wav),
                "windows": windows,
                "actions": actions,
                "anchors": anchors,
                "phrase_wavs": phrase_wavs,
                "point_at": point_at or [],
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
