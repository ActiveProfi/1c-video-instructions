"""Кадры макета 1С, титры и пометки: рамка, стрелка, субтитры."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HEADER = "#F5C400"
LEFT = "#243140"
PAPER = "#F4F6F8"
INK = "#1C2430"
MUTED = "#5C6B7A"
LINE = "#D5DDE6"
BUTTON = "#FFE56A"
LINK = "#1A56B0"
BAR = (20, 32, 51)
ORANGE = "#E08900"
CARD = "#0E2744"


def find_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _canvas_size(ui_map: dict) -> tuple[int, int]:
    canvas = ui_map.get("canvas") or {}
    return int(canvas.get("width", 1280)), int(canvas.get("height", 720))


def _money(value: str) -> str:
    return str(value)


def apply_compute(values: dict, ui_map: dict) -> None:
    for rule in ui_map.get("compute") or []:
        if rule.get("op") != "mul_money":
            continue
        try:
            qty = float(str(values.get(rule["qty"], "")).replace(" ", "").replace(",", "."))
            price = float(str(values.get(rule["price"], "")).replace(" ", "").replace(",", "."))
        except ValueError:
            continue
        amount = qty * price
        values[rule["target"]] = f"{amount:,.2f}".replace(",", " ").replace(".", ",")


def _draw_element(draw: ImageDraw.ImageDraw, element: dict, value: str | None) -> None:
    box = element["bounds"]
    x, y, w, h = int(box["x"]), int(box["y"]), int(box["width"]), int(box["height"])
    kind = element.get("type")
    font = find_font(18)
    small = find_font(15)
    if kind == "nav":
        draw.rounded_rectangle([x, y, x + w, y + h], radius=6, fill="#31465C")
        draw.text((x + 12, y + 8), element.get("name", ""), fill="white", font=font)
        return
    if kind == "button":
        draw.rounded_rectangle([x, y, x + w, y + h], radius=6, fill=BUTTON, outline="#C4A000")
        draw.text((x + 12, y + 10), element.get("name", ""), fill=INK, font=font)
        return
    if kind == "link":
        draw.text((x, y + 6), element.get("name", ""), fill=LINK, font=font)
        return
    if kind == "label":
        draw.text((x, y), element.get("text") or element.get("name", ""), fill=INK, font=font)
        return
    if kind == "check":
        checked = str(value).lower() in {"1", "true", "yes", "да"} or (
            value is None and element.get("checked")
        )
        draw.rounded_rectangle([x, y + 8, x + 22, y + 30], radius=3, outline="#7A8794", width=2, fill="white")
        if checked:
            draw.line([(x + 4, y + 18), (x + 9, y + 24), (x + 18, y + 12)], fill="#1B7F3A", width=3)
        draw.text((x + 32, y + 6), element.get("name", ""), fill=INK, font=font)
        return
    if kind == "table":
        draw.rounded_rectangle([x, y, x + w, y + h], radius=4, fill="white", outline=LINE)
        columns = element.get("columns") or []
        if columns:
            col_w = w / len(columns)
            draw.rectangle([x, y, x + w, y + 28], fill="#E8EEF5")
            for index, name in enumerate(columns):
                draw.text((x + 8 + int(index * col_w), y + 5), name, fill=MUTED, font=small)
        row = element.get("row_from") or []
        if value:
            # value is a list joined by the caller via values dict; table reads fields itself
            pass
        return
    # field
    label = element.get("name", "")
    draw.text((x, y), label, fill=MUTED, font=small)
    draw.rounded_rectangle([x, y + 22, x + w, y + h], radius=4, fill="white", outline=LINE)
    shown = value if value not in (None, "") else element.get("value") or ""
    if shown:
        draw.text((x + 8, y + 26), str(shown), fill=INK, font=font)


def _table_row(values: dict, element: dict) -> list[str]:
    keys = element.get("row_from") or []
    if not keys:
        return []
    if any(not values.get(key) for key in keys[:1]):
        return []
    return [str(values.get(key, "")) for key in keys]


def render_screen(ui_map: dict, screen_id: str, values: dict, path: Path, mock_banner: bool) -> None:
    screen = ui_map["screens"][screen_id]
    width, height = _canvas_size(ui_map)
    image = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, 56], fill=HEADER)
    draw.text((16, 14), ui_map.get("window_title") or "1С", fill=INK, font=find_font(22, bold=True))
    draw.text((420, 16), screen.get("title") or "", fill="#3A3320", font=find_font(18))
    if mock_banner:
        note = "Макет интерфейса, не запись вашей базы"
        font = find_font(14)
        draw.text((width - 16 - int(font.getlength(note)), 18), note, fill="#5A4A10", font=font)
    draw.rectangle([0, 56, 228, height], fill=LEFT)
    menu_font = find_font(18)
    for index, name in enumerate(["Главное", "Покупки", "Продажи", "Склад", "Банк и касса"]):
        draw.text((24, 86 + index * 44), name, fill="#C5D0DC", font=menu_font)
    for element in (screen.get("elements") or {}).values():
        if element.get("type") == "table":
            _draw_element(draw, element, None)
            row = _table_row(values, element)
            if row:
                box = element["bounds"]
                x, y, w = int(box["x"]), int(box["y"]), int(box["width"])
                col_w = w / max(1, len(row))
                font = find_font(16)
                for index, cell in enumerate(row):
                    draw.text((x + 8 + int(index * col_w), y + 40), cell, fill=INK, font=font)
            continue
        value = values.get(_key_of(screen, element))
        _draw_element(draw, element, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _key_of(screen: dict, element: dict) -> str:
    for key, candidate in (screen.get("elements") or {}).items():
        if candidate is element:
            return key
    return ""


def render_title_card(title: str, subtitle: str, footer: str, path: Path, width: int = 1280, height: int = 720) -> None:
    image = Image.new("RGB", (width, height), CARD)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, 8], fill=HEADER)
    draw.text((80, 180), "Урок", fill="#F5C400", font=find_font(28, bold=True))
    wrapped = _wrap(title.removeprefix("Урок:").strip(), find_font(48, bold=True), width - 160)
    y = 240
    for line in wrapped[:3]:
        draw.text((80, y), line, fill="white", font=find_font(48, bold=True))
        y += 62
    draw.text((80, y + 20), subtitle, fill="#D5DDE8", font=find_font(26))
    # Ниже полосы субтитров подпись не ставим: её закрывает тёмная плашка.
    draw.text((80, height - 190), footer, fill="#9FB0C3", font=find_font(20))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _wrap(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    lines = []
    current = words[0]
    for word in words[1:]:
        trial = current + " " + word
        if font.getlength(trial) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _chip_position(x, y, w, h, chip_w, chip_h, width, bar_top):
    """Подпись рядом с рамкой, а не поверх соседнего поля."""
    right = x + w + 10
    if right + chip_w <= width - 8:
        return right, min(max(62, y), bar_top - chip_h - 4)
    above = y - chip_h - 8
    left = min(max(8, x), width - chip_w - 8)
    if above >= 62:
        return left, above
    below = y + h + 8
    if below + chip_h < bar_top:
        return left, below
    return left, max(62, bar_top - chip_h - 4)


def _arrow_between(source, target):
    sx1, sy1, sx2, sy2 = source
    tx1, ty1, tx2, ty2 = target
    scx, scy = (sx1 + sx2) / 2, (sy1 + sy2) / 2
    tcx, tcy = (tx1 + tx2) / 2, (ty1 + ty2) / 2
    if scx >= tx2:
        return (sx1, scy), (tx2, tcy)
    if scx <= tx1:
        return (sx2, scy), (tx1, tcy)
    if scy <= ty1:
        return (scx, sy2), (tcx, ty1)
    return (scx, sy1), (tcx, ty2)


def _arrow_head(start, end, size=18):
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    return [
        end,
        (end[0] - size * math.cos(angle - 0.45), end[1] - size * math.sin(angle - 0.45)),
        (end[0] - size * math.cos(angle + 0.45), end[1] - size * math.sin(angle + 0.45)),
    ]


def annotate_frame(source: Path, dest: Path, bounds: dict | None, caption: str, subtitle: str) -> None:
    image = Image.open(source).convert("RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    bar_top = height - 118
    if bounds and bounds.get("width") and bounds.get("height"):
        x = int(bounds["x"])
        y = int(bounds["y"])
        w = int(bounds["width"])
        h = int(bounds["height"])
        x = max(2, min(x, width - 4))
        y = max(2, min(y, bar_top - 4))
        w = max(8, min(w, width - x - 2))
        h = max(8, min(h, bar_top - y - 2))
        pad = 4
        box = [x - pad, y - pad, x + w + pad, y + h + pad]
        draw.rounded_rectangle(box, radius=6, outline=ORANGE, width=4)
        if caption:
            font = find_font(16, bold=True)
            chip = caption[:80]
            chip_w = int(font.getlength(chip)) + 16
            chip_h = 26
            chip_x, chip_y = _chip_position(x, y, w, h, chip_w, chip_h, width, bar_top)
            draw.rounded_rectangle(
                [chip_x, chip_y, chip_x + chip_w, chip_y + chip_h],
                radius=4,
                fill="#3A2A08",
            )
            draw.text((chip_x + 8, chip_y + 3), chip, fill="#FFE7A3", font=font)
            start, tip = _arrow_between(
                (chip_x, chip_y, chip_x + chip_w, chip_y + chip_h),
                (box[0], box[1], box[2], box[3]),
            )
            draw.line([start, tip], fill=ORANGE, width=5)
            draw.polygon(_arrow_head(start, tip), fill=ORANGE)
    draw.rectangle([0, bar_top, width, height], fill=BAR)
    font = find_font(24)
    lines = _wrap(subtitle, font, width - 64)[:3]
    if len(_wrap(subtitle, font, width - 64)) > 3:
        font = find_font(20)
        lines = _wrap(subtitle, font, width - 64)[:3]
    text_y = bar_top + 14
    for line in lines:
        draw.text((32, text_y), line, fill="white", font=font)
        text_y += int(font.size) + 6
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest)
