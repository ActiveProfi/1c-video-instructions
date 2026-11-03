"""Синтетический курсор: показывает элемент, о котором говорит голос, когда клика нет.

Сценарий задаёт point_at: [{"label", "say", "rect"}] — слово из фразы и рамка элемента
в координатах записи (для старых записей — разметка по кадрам, для новых — UI Automation).
Пока звучит фраза, курсор плавно едет к элементу и стоит на нём, элемент обведён рамкой.
С настоящего курсора на стоп-кадре снимаются свечение и стрелка (заплатка из того же кадра),
поэтому двух курсоров в кадре нет.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

TRAVEL = 0.35  # секунды на перелёт курсора
LEAD = 0.3
PATCH = 128  # сторона заплатки поверх настоящего курсора (px записи)


@dataclass
class Point:
    label: str
    say: str
    rect: tuple[int, int, int, int] | None = None
    caption: str = ""
    element: str = ""  # имя элемента для UI Automation (новые записи)


def points_from_json(items) -> list[Point]:
    out = []
    for p in items or []:
        rect = tuple(int(v) for v in p["rect"]) if p.get("rect") else None
        out.append(Point(label=str(p.get("label") or p.get("element") or p.get("say") or ""), say=str(p.get("say") or ""),
                         rect=rect, caption=p.get("caption") or "", element=p.get("element") or ""))
    return out


def phrase_windows(beat) -> list[tuple[float, float]]:
    t = beat.voice_at
    out = []
    for phrase in beat.phrases:
        length = phrase.end - phrase.start
        out.append((t, t + length))
        t += length
    return out


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def assign_points(points: list[Point], beats, action_says: list[str], travel: float = TRAVEL, lead: float = LEAD) -> list[str]:
    """Раскладывает наведения по тактам. Возвращает заметки о ненайденных словах."""
    notes = []
    for beat in beats:
        beat.points = []
    for point in points:
        if not point.rect or not point.say:
            notes.append(f"{point.label}: нет рамки или слова — наведение пропущено")
            continue
        hit = None
        for beat in beats:
            for k, (a, b) in enumerate(phrase_windows(beat)):
                text = _norm(beat.phrases[k].text)
                pos = text.find(_norm(point.say))
                if pos >= 0:
                    hit = (beat, k, a, b, a + (b - a) * pos / max(1, len(text)), text)
                    break
            if hit:
                break
        if not hit:
            notes.append(f"{point.label}: слово «{point.say}» не найдено")
            continue
        beat, k, a, b, word, text = hit
        clicked = any(s and _norm(s) in text for s in action_says) or (beat.action is not None and k == 0)
        beat.points.append({"label": point.label, "say": point.say, "rect": list(point.rect), "phrase": k,
                            "word_at": round(word, 3), "from": a, "to": b, "clicked": clicked})
    for beat in beats:
        beat.points.sort(key=lambda p: p["word_at"])
        for i, p in enumerate(beat.points):
            first_in_phrase = i == 0 or beat.points[i - 1]["phrase"] != p["phrase"]
            if not first_in_phrase or p["clicked"]:
                p["from"] = max(p["from"], p["word_at"] - lead)
        for i, p in enumerate(beat.points[:-1]):
            nxt = beat.points[i + 1]
            if nxt["phrase"] == p["phrase"]:
                p["to"] = min(p["to"], nxt["from"])
        prev_leave = None
        for p in beat.points:
            # курсор приезжает к началу фразы; раньше нельзя: идёт живое видео с настоящим курсором
            earliest = beat.shown + travel if beat.shown > 0.01 else travel * 0.0
            if prev_leave is not None:
                earliest = max(earliest, prev_leave + travel)
            p["arrive"] = round(max(p["from"], earliest), 3)
            p["to"] = round(max(p["to"], p["arrive"] + 0.2), 3)
            p["from"] = round(p["from"], 3)
            span = max(0.01, p["to"] - p["from"])
            p["covered"] = round(max(0.0, min(1.0, (p["to"] - p["arrive"]) / span)), 3)
            prev_leave = p["to"]
    return notes


def center(rect) -> tuple[int, int]:
    l, t, r, b = rect
    return (l + r) // 2, (t + b) // 2


def keyframes(beat, start_pos, travel: float = TRAVEL) -> tuple[float, list[tuple[float, int, int]]]:
    """Ключевые точки пути курсора внутри такта: (время, x, y). Возвращает время появления и путь."""
    pts = beat.points
    first = pts[0]
    appear = max(beat.shown if beat.shown > 0.01 else 0.0, first["arrive"] - travel)
    pos = start_pos or center(first["rect"])
    frames = [(appear, pos[0], pos[1])]
    for p in pts:
        cx, cy = center(p["rect"])
        frames.append((max(frames[-1][0] + 0.04, p["arrive"] - travel), frames[-1][1], frames[-1][2]))
        frames.append((max(frames[-1][0] + 0.04, p["arrive"]), cx, cy))
        frames.append((max(frames[-1][0], p["to"]), cx, cy))
    if start_pos:
        end = beat.duration
        leave = frames[-1][0]
        back = max(leave, end - travel)
        frames.append((back, frames[-1][1], frames[-1][2]))
        frames.append((max(back + 0.04, end - 0.04), start_pos[0], start_pos[1]))
    return appear, frames


def path_expr(frames: list[tuple[float, int, int]], axis: int, offset: int) -> str:
    expr = f"{frames[-1][axis] - offset}"
    for (t0, *p0), (t1, *p1) in reversed(list(zip(frames, frames[1:]))):
        v0, v1 = p0[axis - 1] - offset, p1[axis - 1] - offset
        if v0 == v1:
            seg = f"{v0}"
        else:
            d = max(0.04, t1 - t0)
            s = f"clip((t-{t0:.3f})/{d:.3f},0,1)"
            seg = f"{v0}+({v1 - v0})*{s}*{s}*(3-2*{s})"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return expr


# ---------- картинки ----------

HOT = (40, 40)  # где в PNG кончик стрелки


def cursor_png(dest: Path) -> Path:
    """Стрелка с голубым свечением — как курсор в записи."""
    from PIL import Image, ImageDraw, ImageFilter

    size = 100
    glow = Image.new("L", (size, size), 0)
    ImageDraw.Draw(glow).ellipse((HOT[0] - 26, HOT[1] - 24, HOT[0] + 34, HOT[1] + 36), fill=120)
    glow = glow.filter(ImageFilter.GaussianBlur(11))
    img = Image.new("RGBA", (size, size), (66, 140, 255, 0))
    img.putalpha(glow)
    d = ImageDraw.Draw(img)
    x, y = HOT
    arrow = [(x, y), (x, y + 17), (x + 4, y + 13), (x + 7, y + 20), (x + 9, y + 19), (x + 6, y + 12), (x + 12, y + 12)]
    d.polygon(arrow, fill=(0, 0, 0, 255), outline=(255, 255, 255, 255))
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)
    return dest


def _ffmpeg_bin() -> str:
    from pipeline.continuous import _ffmpeg  # type: ignore

    return _ffmpeg()


def _rgb(raw: Path, moment: float, crop=None, fps: float | None = None, duration: float | None = None):
    vf = []
    if crop:
        l, t, w, h = crop
        vf.append(f"crop={w}:{h}:{l}:{t}")
    if fps:
        vf.append(f"fps={fps}")
    cmd = [_ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, moment):.3f}"]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-i", str(raw)]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    if not fps:
        cmd += ["-frames:v", "1"]
    cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    result = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True)
    return result.stdout if result.returncode == 0 else b""


def _glow(r: int, g: int, b: int) -> bool:
    # свечение курсора: светло-голубое (не тёмное выделение текста и не синие ссылки)
    return b > 200 and r > 90 and b - r > 18 and b - g > 6


def find_glow(data: bytes, w: int, h: int, step: int = 2, win: int = 16) -> tuple[int, int] | None:
    """Центр голубого свечения курсора в кадре rgb24 (или None)."""
    sw, sh = w // step, h // step
    if len(data) < w * h * 3 or sw < win or sh < win:
        return None
    grid = [[0] * (sw + 1) for _ in range(sh + 1)]
    for y in range(sh):
        row = grid[y + 1]
        prev = grid[y]
        acc = 0
        base = (y * step) * w * 3
        for x in range(sw):
            i = base + x * step * 3
            acc += 1 if _glow(data[i], data[i + 1], data[i + 2]) else 0
            row[x + 1] = prev[x + 1] + acc
    best, at = 0, None
    for y in range(0, sh - win):
        for x in range(0, sw - win):
            s = grid[y + win][x + win] - grid[y][x + win] - grid[y + win][x] + grid[y][x]
            if s > best:
                best, at = s, (x, y)
    if not at or best < win * win * 0.25:
        return None
    return (at[0] + win // 2) * step, (at[1] + win // 2) * step


def arrow_tip(data: bytes, w: int, h: int, spot: tuple[int, int], radius: int = 34) -> tuple[int, int]:
    """Кончик чёрной стрелки возле свечения: верхний левый угол самого плотного тёмного пятна."""
    dark = set()
    for y in range(max(0, spot[1] - radius), min(h, spot[1] + radius)):
        for x in range(max(0, spot[0] - radius), min(w, spot[0] + radius)):
            i = (y * w + x) * 3
            if data[i] + data[i + 1] + data[i + 2] < 150:
                dark.add((x, y))
    if not dark:
        return spot
    def density(p):
        x0, y0 = p
        return sum(1 for (x, y) in dark if x0 <= x < x0 + 10 and y0 <= y < y0 + 16)
    scored = [(density(p), p) for p in dark]
    top = max(d for d, _ in scored)
    if top < 25:
        return spot
    tips = [p for d, p in scored if d >= 0.7 * top]
    return min(tips, key=lambda p: p[0] + p[1])


def _region(cx: int, cy: int, w: int, h: int, side: int = PATCH) -> tuple[int, int, int, int]:
    l = min(max(0, cx - side // 2), max(0, w - side))
    t = min(max(0, cy - side // 2), max(0, h - side))
    return l, t, min(side, w), min(side, h)


def hide_real_cursor(raw: Path, moment: float, size: tuple[int, int], total: float, dest: Path) -> dict | None:
    """Находит настоящий курсор на стоп-кадре и делает заплатку без него (снимает свечение и стрелку).

    Возвращает {"cursor": (x, y), "patch": png, "region": (l, t, w, h)} или None, если курсора не видно.
    """
    from PIL import Image

    w, h = size
    frame = _rgb(raw, moment)
    spot = find_glow(frame, w, h)
    if not spot:
        return None
    cursor = arrow_tip(frame, w, h, spot)
    region = _region(cursor[0] + 6, cursor[1] + 12, w, h)
    patch = remove_cursor(frame, w, h, spot, cursor, region)
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.frombytes("RGB", (region[2], region[3]), patch).save(dest)
    return {"cursor": cursor, "patch": dest, "region": region}


GLOW_RGB = (0, 128, 255)  # цвет свечения курсора в записи (подобран по кадрам: Δg/Δr ≈ 0.5)


def remove_cursor(frame: bytes, w: int, h: int, spot: tuple[int, int], tip: tuple[int, int], region) -> bytes:
    """Снимает свечение и стрелку с фрагмента стоп-кадра.

    Свечение — полупрозрачная голубая заливка: hold = bg·(1−a) + C·a. Прозрачность a(d)
    оценивается по расстоянию от центра (медиана (b−r)/255 на кольцах), затем фон восстанавливается.
    Стрелка (непрозрачная) закрашивается интерполяцией соседних пикселей по строке.
    """
    l, t, rw, rh = region
    px = lambda x, y: frame[(y * w + x) * 3:(y * w + x) * 3 + 3]
    # центр свечения: центроид голубизны
    sx = sy = sm = 0
    for y in range(max(0, spot[1] - 50), min(h, spot[1] + 50)):
        for x in range(max(0, spot[0] - 50), min(w, spot[0] + 50)):
            r, g, b = px(x, y)
            v = b - r
            if v > 15 and r > 60:
                sx += x * v; sy += y * v; sm += v
    cx, cy = (sx / sm, sy / sm) if sm else spot
    bins: dict[int, list[float]] = {}
    for y in range(t, t + rh):
        for x in range(l, l + rw):
            r, g, b = px(x, y)
            if r < 60:
                continue
            d = int(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 // 3)
            bins.setdefault(d, []).append(max(0.0, (b - r) / 255.0))
    alpha = {}
    for d, values in bins.items():
        values.sort()
        alpha[d] = values[int(len(values) * 0.75)] * 1.05
    # фон без свечения дальше ~60 px: a там 0
    out = bytearray()
    for y in range(t, t + rh):
        for x in range(l, l + rw):
            r, g, b = px(x, y)
            d = int(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 // 3)
            a = min(0.6, alpha.get(d, 0.0)) if d < 22 else 0.0
            if a > 0.01:
                vals = []
                for c, k in zip((r, g, b), GLOW_RGB):
                    vals.append(int(max(0, min(255, round((c - k * a) / (1 - a))))))
                r, g, b = vals
            out += bytes((r, g, b))
    # стрелка: тёмные пиксели и белая обводка в рамке стрелки
    ax0, ay0 = tip[0] - 2, tip[1] - 2
    ax1, ay1 = tip[0] + 16, tip[1] + 24
    def get(x, y):
        i = ((y - t) * rw + (x - l)) * 3
        return out[i:i + 3]
    for y in range(max(t, ay0), min(t + rh, ay1)):
        xs = [x for x in range(max(l, ax0), min(l + rw, ax1))]
        mask = [sum(get(x, y)) < 300 or (tip[0] - 2 <= x <= tip[0] + (y - tip[1]) * 0.85 + 3) for x in xs]
        # опорные пиксели слева и справа от стрелки
        left = get(max(l, ax0 - 1), y)
        right = get(min(l + rw - 1, ax1), y)
        n = len(xs)
        for j, x in enumerate(xs):
            if not mask[j]:
                continue
            f = (j + 1) / (n + 1)
            i = ((y - t) * rw + (x - l)) * 3
            out[i:i + 3] = bytes(int(left[c] * (1 - f) + right[c] * f) for c in range(3))
    return bytes(out)
