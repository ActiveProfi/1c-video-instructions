"""Монтаж с якорями: каждая фраза озвучки начинается вместе со своим действием.

Как это устроено:
1. Озвучка шага (один wav на шаг) делится на фразы по паузам между предложениями.
   Голос не ускоряется и не замедляется: atempo не используется.
2. Каждая фраза привязана к действию (клик, ввод). Привязку можно задать в журнале
   полем ``anchors`` (номер действия или null для каждой фразы). Если её нет,
   фразы распределяются по действиям по порядку.
3. Запись режется на «такты»: такт начинается чуть раньше своего действия и идёт
   до начала следующего якорного действия. Паузы без движения вырезаются
   (freezedetect). Видео идёт со скоростью 1x. Если фраза длиннее такта,
   последний кадр держится (стоп-кадр). Если действие длиннее фразы, видео
   доигрывает без голоса.
4. Голос в такте начинается в момент якорного действия (минус lead) уже на
   смонтированном таймлайне. Поэтому сдвиги из-за вырезанных пауз не копятся.
5. Аннотации: рамка вокруг элемента, по которому кликают, и подпись шага внизу
   слева. Рамка берётся из координат UI Automation, если они есть в журнале.
   Если их нет, используется область, которая изменилась на экране во время действия.
"""

from __future__ import annotations

import array
import json
import math
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.continuous import (
    _even,
    _ffmpeg,
    _safe,
    _subtract,
    concat_same,
    detect_freezes,
    normalize_clip,
    probe_video,
    write_srt,
)
from pipeline.postprod import run_ffmpeg, sentences

FPS = 25


BOX_MAX = 3.0  # рамка держится не дольше, чтобы не пережить всплывающий список


@dataclass
class Action:
    label: str
    start: float
    end: float
    caption: str = ""
    rect: tuple[int, int, int, int] | None = None  # left, top, right, bottom в пикселях записи
    say: str = ""  # слово из фразы, на котором должно случиться это действие (для действий внутри такта)
    box: bool = True  # False — рамку не рисуем (всплывающие списки, неуверенная детекция)


@dataclass
class AnchoredStep:
    id: str
    narration: str
    wav: Path
    actions: list[Action]
    title: str = ""
    anchors: list | None = None
    legal_note: str = ""
    phrase_wavs: dict = field(default_factory=dict)  # номер предложения -> wav переозвученной фразы


@dataclass
class Phrase:
    text: str
    start: float  # секунда внутри wav шага
    end: float
    samples: list | None = None  # своя озвучка фразы (переозвучено по словарю)


@dataclass
class Beat:
    step: str
    index: int
    action: int | None
    raw_start: float
    raw_end: float
    keep: list[tuple[float, float]]
    shown: float
    voice_at: float
    phrases: list[Phrase]
    voice: float
    hold: float
    duration: float
    boxes: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    pauses: list[float] = field(default_factory=list)  # стоп-кадр перед каждым куском keep
    sync: list[dict] = field(default_factory=list)


# ---------- фразы из wav ----------


def read_wav(path: Path) -> tuple[list[int], int]:
    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise RuntimeError(f"Нужен 16-битный wav: {path}")
        rate, channels = handle.getframerate(), handle.getnchannels()
        data = array.array("h", handle.readframes(handle.getnframes()))
    if channels > 1:
        data = array.array("h", data[::channels])
    return list(data), rate


def silences(samples: list[int], rate: int, min_len: float = 0.16, window: float = 0.01, threshold_db: float = -32.0) -> list[tuple[float, float]]:
    """Тихие участки (относительно пика), кроме краёв записи."""
    step = max(1, int(rate * window))
    levels = []
    for i in range(0, len(samples), step):
        chunk = samples[i : i + step]
        levels.append(math.sqrt(sum(v * v for v in chunk) / len(chunk)) if chunk else 0.0)
    peak = max(levels) if levels else 0.0
    if peak <= 0:
        return []
    limit = peak * (10 ** (threshold_db / 20))
    loud = [i for i, v in enumerate(levels) if v > limit]
    if not loud:
        return []
    first, last = loud[0], loud[-1]
    runs, begin = [], None
    for i in range(first, last + 1):
        if levels[i] <= limit:
            begin = i if begin is None else begin
        elif begin is not None:
            if (i - begin) * window >= min_len:
                runs.append((begin * window, i * window))
            begin = None
    return runs


def split_phrases(text: str, samples: list[int], rate: int) -> tuple[list[Phrase], str]:
    """Делит озвучку шага на фразы-предложения по паузам. Возвращает фразы и заметку."""
    parts = sentences(text)
    total = len(samples) / float(rate)
    if len(parts) == 1:
        return [Phrase(parts[0], 0.0, total)], ""
    runs = silences(samples, rate)
    lengths = [max(1, len(p)) for p in parts]
    speech_start = 0.0
    speech_end = total
    cuts: list[float] = []
    used: set[int] = set()
    acc = 0
    for i in range(len(parts) - 1):
        acc += lengths[i]
        target = speech_start + (speech_end - speech_start) * acc / sum(lengths)
        best, best_score = None, None
        for j, (a, b) in enumerate(runs):
            if j in used or (cuts and (a + b) / 2 <= cuts[-1]):
                continue
            # ближе к ожидаемому месту и длиннее — лучше
            score = abs((a + b) / 2 - target) - 0.8 * min(b - a, 0.6)
            if best_score is None or score < best_score:
                best, best_score = j, score
        if best is None:
            return [Phrase(text.strip(), 0.0, total)], "паузы между предложениями не найдены, фраза целиком"
        used.add(best)
        a, b = runs[best]
        cuts.append(round((a + b) / 2, 3))
    bounds = [0.0] + cuts + [total]
    return [Phrase(parts[i], bounds[i], bounds[i + 1]) for i in range(len(parts))], ""


def default_anchors(phrases: int, actions: int) -> list[int | None]:
    if actions <= 0:
        return [None] * phrases
    return [min(actions - 1, i * actions // max(1, phrases)) for i in range(phrases)]


def trim_silence(samples: list[int], rate: int, head: float = 0.06, tail: float = 0.3) -> list[int]:
    """Обрезает тишину по краям отдельной фразы и оставляет короткие поля, как между предложениями."""
    if not samples:
        return samples
    peak = max(abs(v) for v in samples) or 1
    limit = peak * 0.03
    first = next((i for i, v in enumerate(samples) if abs(v) > limit), 0)
    last = len(samples) - next((i for i, v in enumerate(reversed(samples)) if abs(v) > limit), 0)
    a = max(0, first - int(head * rate))
    b = min(len(samples), last + int(0.05 * rate))
    return list(samples[a:b]) + [0] * int(max(0.0, tail - 0.05) * rate)


def apply_phrase_wavs(phrases: list[Phrase], phrase_wavs: dict, rate: int, note: str = "") -> list[int]:
    """Подменяет звук отдельных предложений переозвученными wav. Речь не растягивается."""
    replaced = []
    if not phrase_wavs or note:
        return replaced
    for key, path in phrase_wavs.items():
        index = int(key)
        if not 0 <= index < len(phrases) or not Path(path).exists():
            continue
        own, own_rate = read_wav(Path(path))
        if own_rate != rate:
            raise RuntimeError(f"Частота {path} ({own_rate}) не совпадает с озвучкой шага ({rate})")
        own = trim_silence(own, rate)
        phrases[index].samples = own
        phrases[index].start = 0.0
        phrases[index].end = len(own) / float(rate)
        replaced.append(index)
    return sorted(replaced)


# ---------- план тактов ----------


def _compressed(point: float, keep: list[tuple[float, float]]) -> float:
    """Где окажется момент записи point после склейки кусков keep."""
    clock = 0.0
    for a, b in keep:
        if point <= a:
            return clock
        if point < b:
            return clock + (point - a)
        clock += b - a
    return clock


def _frames(seconds: float) -> float:
    return math.ceil(round(seconds * FPS, 6)) / FPS


def plan_step(
    step: AnchoredStep,
    phrases: list[Phrase],
    freezes: list[tuple[float, float]],
    cursor: float,
    next_start: float | None,
    total: float,
    lead: float = 0.3,
    pre: float = 0.8,
    tail: float = 1.5,
    gap: float = 0.35,
    margin: float = 0.4,
) -> tuple[list[Beat], float]:
    """Такты одного шага. cursor — где в записи остановился предыдущий шаг."""
    anchors = list(step.anchors) if step.anchors is not None else default_anchors(len(phrases), len(step.actions))
    if len(anchors) != len(phrases):
        anchors = default_anchors(len(phrases), len(step.actions))
    # Группы: [(номер действия или None, [фразы])]. Фразы без действия в начале — отдельный такт на стоп-кадре,
    # после — дописываются к предыдущему такту.
    groups: list[list] = []
    seen: set[int] = set()
    for phrase, anchor in zip(phrases, anchors):
        if anchor is not None and (anchor in seen or not 0 <= int(anchor) < len(step.actions)):
            anchor = None
        if anchor is None:
            if groups and groups[-1][0] is not None:
                groups[-1][1].append(phrase)
            elif groups:
                groups[-1][1].append(phrase)
            else:
                groups.append([None, [phrase]])
            continue
        anchor = int(anchor)
        seen.add(anchor)
        groups.append([anchor, [phrase]])
    beats: list[Beat] = []
    anchored = [g[0] for g in groups if g[0] is not None]
    owner: dict[int, int] = {}
    for ai in range(len(step.actions)):
        before = [x for x in anchored if x <= ai]
        owner[ai] = before[-1] if before else (anchored[0] if anchored else -1)
    first = step.actions[0] if step.actions else None
    for gi, (anchor, items) in enumerate(groups):
        notes: list[str] = []
        members = [ai for ai in range(len(step.actions)) if anchor is not None and owner[ai] == anchor]
        # окна действий такта в порядке номеров; до якоря — то, что идёт раньше него
        members.sort(key=lambda ai: (ai != anchor and ai > (anchor or 0), ai))
        windows = [(max(0.0, step.actions[ai].start - pre), min(total, step.actions[ai].end + tail)) for ai in members]
        keep: list[tuple[float, float]] = []
        offsets: dict[int, float] = {}
        first_piece: dict[int, int] = {}
        for ai, window in zip(members, windows):
            part = _subtract(window, freezes, margin)
            if not part:
                part = [(window[0], min(window[1], window[0] + 1.2))]
                notes.append(f"{step.actions[ai].label}: окно без движения, оставлен короткий кадр")
            first_piece[ai] = len(keep)
            offsets[ai] = sum(b - a for a, b in keep) + _compressed(step.actions[ai].start, part)
            keep.extend(part)
        pauses = [0.0] * len(keep)
        voice_at = max(0.0, offsets[anchor] - lead) if anchor is not None else 0.0
        voice = sum(p.end - p.start for p in items)
        # Действия внутри такта: ставим их на слово из фразы (поле say). Видео можно только
        # задержать стоп-кадром перед действием, речь не трогаем.
        starts, clock = [], voice_at
        for phrase in items:
            starts.append(clock)
            clock += phrase.end - phrase.start
        sync = []
        order = [ai for ai in members if anchor is not None and ai != anchor and offsets[ai] >= offsets[anchor]]
        for ai in order:
            hint = (step.actions[ai].say or "").strip().lower()
            if not hint:
                continue
            word_at = None
            for k, phrase in enumerate(items):
                where = phrase.text.lower().find(hint)
                if where >= 0:
                    word_at = starts[k] + (phrase.end - phrase.start) * where / max(1, len(phrase.text))
                    break
            if word_at is None:
                notes.append(f"{step.actions[ai].label}: слово «{hint}» не найдено во фразе")
                continue
            target = word_at - lead
            if offsets[ai] < target:
                extra = target - offsets[ai]
                pauses[first_piece[ai]] += extra
                for other in members:
                    if offsets[other] >= offsets[ai] and other != anchor:
                        offsets[other] += extra
            sync.append({"action": step.actions[ai].label, "word": hint, "word_at": round(word_at, 3), "action_at": round(offsets[ai], 3),
                         "delta": round(offsets[ai] - word_at, 3)})
        shown = sum(b - a for a, b in keep) + sum(pauses)
        need = voice_at + voice + gap
        duration = _frames(max(shown, need))
        hold = max(0.0, duration - shown)
        boxes = []
        timeline = sorted(members, key=lambda ai: offsets[ai])
        for n, ai in enumerate(timeline):
            action = step.actions[ai]
            begin = max(0.0, offsets[ai] - lead)
            if n + 1 < len(timeline):
                end = max(begin + 0.6, offsets[timeline[n + 1]] - lead - 0.04)
            else:
                end = max(begin + 2.5, voice_at + voice)
            boxes.append({"action": ai, "label": action.label, "at": round(offsets[ai], 3), "from": round(begin, 3), "to": round(min(duration - 0.04, end, begin + BOX_MAX), 3),
                          "rect": action.rect, "caption": action.caption})
        raw_start = windows[0][0] if windows else max(0.0, (first.start - pre) if first else cursor)
        raw_end = windows[-1][1] if windows else raw_start
        beats.append(
            Beat(
                step=step.id,
                index=gi,
                action=anchor,
                raw_start=round(raw_start, 3),
                raw_end=round(raw_end, 3),
                keep=keep,
                shown=round(shown, 3),
                voice_at=round(voice_at, 3),
                phrases=items,
                voice=round(voice, 3),
                hold=round(hold, 3),
                duration=round(duration, 3),
                boxes=boxes,
                notes=notes,
                pauses=[round(x, 3) for x in pauses],
                sync=sync,
            )
        )
        cursor = max(cursor, raw_end)
    return beats, cursor


# ---------- звук такта ----------


def beat_audio(beat: Beat, samples: list[int], rate: int, dest: Path, gap: float = 0.0) -> None:
    """wav такта: тишина до якоря, затем фразы подряд с исходной скоростью, тишина до конца."""
    out = array.array("h", [0] * int(round(beat.voice_at * rate)))
    for phrase in beat.phrases:
        if phrase.samples is not None:
            out.extend(phrase.samples)
            continue
        a, b = int(phrase.start * rate), int(phrase.end * rate)
        out.extend(samples[a:b])
        if gap:
            out.extend([0] * int(gap * rate))
    total = int(round(beat.duration * rate))
    if len(out) < total:
        out.extend([0] * (total - len(out)))
    del out[total:]
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(out.tobytes())


# ---------- аннотации ----------


LOW_W = 240


def _gray_frame(raw: Path, moment: float, sw: int, sh: int) -> bytes | None:
    try:
        result = subprocess.run(
            [_ffmpeg(), "-v", "error", "-ss", f"{max(0.0, moment):.3f}", "-i", str(raw), "-frames:v", "1",
             "-vf", f"scale={sw}:{sh},format=gray", "-f", "rawvideo", "-"],
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    data = result.stdout or b""
    if result.returncode or len(data) < sw * sh:
        return None
    return data[: sw * sh]


def mask_box(before: bytes, after: bytes, sw: int, sh: int, threshold: int = 24) -> tuple[int, int, int, int] | None:
    """Самая крупная связная область изменений (в пикселях уменьшенного кадра).

    Соседние точки склеиваются (буквы одного поля дают одну область). None, если
    изменений нет, область слишком большая (открылась новая форма), слишком тонкая
    или слишком разреженная — тогда рамку лучше не рисовать.
    """
    changed = bytearray(sw * sh)
    for i in range(sw * sh):
        if abs(before[i] - after[i]) > threshold:
            changed[i] = 1
    # склейка: точки ближе 2 пикселей считаем соседями
    seen = bytearray(sw * sh)
    best: list[int] = []
    for start in range(sw * sh):
        if not changed[start] or seen[start]:
            continue
        stack, comp = [start], []
        seen[start] = 1
        while stack:
            i = stack.pop()
            comp.append(i)
            x, y = i % sw, i // sw
            for dy in (-2, -1, 0, 1, 2):
                ny = y + dy
                if ny < 0 or ny >= sh:
                    continue
                for dx in (-2, -1, 0, 1, 2):
                    nx = x + dx
                    if 0 <= nx < sw:
                        j = ny * sw + nx
                        if changed[j] and not seen[j]:
                            seen[j] = 1
                            stack.append(j)
        if len(comp) > len(best):
            best = comp
    if len(best) < 6:
        return None
    xs = [i % sw for i in best]
    ys = [i // sw for i in best]
    left, right, top, bottom = min(xs), max(xs) + 1, min(ys), max(ys) + 1
    w, h = right - left, bottom - top
    if w * h > 0.08 * sw * sh or h > 0.25 * sh or w > 0.6 * sw:
        return None  # изменилась большая часть экрана: это не один элемент
    if w < 5 or h < 2:
        return None
    if len(best) / float(w * h) < 0.08:
        return None
    return left, top, right, bottom


def _iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def stable_box(boxes: list, min_iou: float = 0.5):
    """Рамка, если замеры согласны между собой; иначе None (лучше без рамки, чем кривая)."""
    boxes = [b for b in boxes if b]
    if len(boxes) < 2:
        return None
    for a in boxes:
        for b in boxes:
            if _iou(a, b) < min_iou:
                return None
    # сглаживание: медиана сторон
    def median(values):
        values = sorted(values)
        return values[len(values) // 2]

    return tuple(median([b[k] for b in boxes]) for k in range(4))


def to_video_rect(box, size: tuple[int, int], sw: int, sh: int, pad: int = 5) -> tuple[int, int, int, int]:
    kx, ky = size[0] / sw, size[1] / sh
    left = max(0, int(box[0] * kx) - pad)
    top = max(0, int(box[1] * ky) - pad)
    right = min(size[0] - 2, int(box[2] * kx) + pad)
    bottom = min(size[1] - 2, int(box[3] * ky) + pad)
    return left // 2 * 2, top // 2 * 2, right // 2 * 2, bottom // 2 * 2


def change_box(raw: Path, action_start: float, action_end: float, size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """Рамка элемента для записи без координат UI Automation.

    Сравниваем кадр до действия с тремя кадрами после клика. Рамка ставится, только
    если все замеры дают одну и ту же область (IoU >= 0,5).
    """
    sw = LOW_W
    sh = max(2, int(sw * size[1] / size[0]))
    before = _gray_frame(raw, action_start - 0.3, sw, sh)
    if before is None:
        return None
    boxes = []
    for moment in (action_start + 0.35, action_start + 0.6, max(action_start + 0.85, action_end + 0.3)):
        after = _gray_frame(raw, moment, sw, sh)
        boxes.append(mask_box(before, after, sw, sh) if after is not None else None)
    box = stable_box(boxes)
    return to_video_rect(box, size, sw, sh) if box else None


def caption_png(text: str, dest: Path, width: int, accent=(255, 153, 0, 255), size_pt: int = 22) -> Path | None:
    """Плашка с подписью шага. Нужен Pillow; без него подпись пропускается."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    font = None
    for name in ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(name, size_pt)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    box = probe.textbbox((0, 0), text, font=font)
    w = min(width - 40, box[2] - box[0] + 36)
    h = box[3] - box[1] + 22
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=10, fill=(16, 24, 39, 215))
    draw.rectangle([0, 0, 6, h - 1], fill=accent)
    draw.text((20, 11 - box[1]), text, font=font, fill=(255, 255, 255, 255))
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest)
    return dest


def render_beat(raw: Path, beat: Beat, wav: Path, dest: Path, size: tuple[int, int], caption: Path | None, annotate: bool) -> None:
    width, height = size
    # Каждый кусок — отдельный вход с -ss/-t: несколько trim одного потока в ffmpeg 8 (Windows) зависали.
    parts, labels = [], []
    inputs = ["-i", str(wav)]
    if beat.keep:
        for i, (a, b) in enumerate(beat.keep):
            wait = beat.pauses[i] if i < len(beat.pauses) else 0.0
            pad = f",tpad=start_mode=clone:start_duration={wait:.3f}" if wait > 0.01 else ""
            inputs += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(raw)]
            parts.append(f"[{i + 1}:v]setpts=PTS-STARTPTS,fps={FPS}{pad}[p{i}]")
            labels.append(f"[p{i}]")
        chain = "".join(labels) + f"concat=n={len(labels)}:v=1:a=0[c];[c]"
    else:
        # такт на стоп-кадре: один кадр из места, где остановилась запись
        at = max(0.0, beat.raw_start)
        inputs += ["-ss", f"{at:.3f}", "-t", "0.08", "-i", str(raw)]
        parts.append("[1:v]setpts=PTS-STARTPTS[p0]")
        chain = "[p0]"
    video = (
        f"{chain}fps={FPS},scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x101827,setsar=1,"
        f"tpad=stop_mode=clone:stop_duration={beat.hold + 1:.3f}"
    )
    if annotate:
        for box in beat.boxes:
            if not box.get("rect"):
                continue
            l, t, r, b = box["rect"]
            video += (
                f",drawbox=x={l}:y={t}:w={max(4, r - l)}:h={max(4, b - t)}:color=0xFF9900@0.95:t=4"
                f":enable='between(t,{box['from']:.3f},{box['to']:.3f})'"
            )
    video += ",format=yuv420p"
    extra: list[str] = []
    overlays: list[str] = []
    label = "[base]"
    next_input = 1 + max(1, len(beat.keep))
    if annotate and caption is not None:
        extra += ["-loop", "1", "-i", str(caption)]
        overlays.append(f"{label}[{next_input}:v]overlay=x=16:y=H-h-16:shortest=0[o{next_input}]")
        label = f"[o{next_input}]"
        next_input += 1
    if annotate:
        for box in beat.boxes:
            png = box.get("caption_png")
            if not png:
                continue
            extra += ["-loop", "1", "-i", str(png)]
            overlays.append(
                f"{label}[{next_input}:v]overlay=x=16:y=H-h-74:shortest=0"
                f":enable='between(t,{box['from']:.3f},{box['to'] + 0.6:.3f})'[o{next_input}]"
            )
            label = f"[o{next_input}]"
            next_input += 1
    if overlays:
        graph = parts + [video + "[base]"] + overlays + [f"{label}format=yuv420p[v]"]
    else:
        graph = parts + [video + "[v]"]
    graph.append("[0:a]aresample=48000,aformat=channel_layouts=stereo[a]")
    run_ffmpeg(
        inputs + extra + [
            "-filter_complex", ";".join(graph),
            "-map", "[v]", "-map", "[a]",
            "-t", f"{beat.duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-c:a", "aac", "-b:a", "160k",
            str(dest),
        ]
    )


# ---------- сборка ----------


def edit_anchored(
    raw: Path,
    steps: list[AnchoredStep],
    out_dir: Path,
    intro: Path | None = None,
    outro: Path | None = None,
    annotate: bool = True,
    freeze_noise: float = 0.0005,
    freeze_min: float = 0.8,
    use_freezedetect: bool = True,
    lead: float = 0.3,
    margin: float = 0.4,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    build = out_dir / "anchored"
    build.mkdir(parents=True, exist_ok=True)
    info = probe_video(raw)
    size = (_even(info["width"]), _even(info["height"]))
    total = info["duration"]
    freezes = detect_freezes(raw, total, freeze_noise, freeze_min) if use_freezedetect else []
    pieces: list[Path] = []
    clock = 0.0
    if intro is not None:
        clip = build / "intro.mp4"
        normalize_clip(intro, clip, size)
        clock = probe_video(clip)["duration"]
        pieces.append(clip)
    cues: list[tuple[float, float, str]] = []
    report_steps = []
    cursor = max(0.0, (steps[0].actions[0].start - 1.0) if steps and steps[0].actions else 0.0)
    for si, step in enumerate(steps):
        samples, rate = read_wav(step.wav)
        phrases, note = split_phrases(step.narration, samples, rate)
        replaced = apply_phrase_wavs(phrases, step.phrase_wavs, rate, note)
        next_start = steps[si + 1].actions[0].start if si + 1 < len(steps) and steps[si + 1].actions else None
        beats, cursor = plan_step(step, phrases, freezes, cursor, next_start, total, lead=lead, margin=margin)
        caption = caption_png(step.title, build / f"caption-{si:02d}.png", size[0]) if annotate and step.title else None
        for beat in beats:
            if annotate:
                for box in beat.boxes:
                    action = step.actions[box["action"]]
                    if not action.box:
                        box["rect"], box["source"] = None, "off"
                    elif box["rect"] is None:
                        box["rect"] = change_box(raw, action.start, action.end, size)
                        box["source"] = "change" if box["rect"] else "none"
                    else:
                        box.setdefault("source", "uia")
                for bi, box in enumerate(beat.boxes):
                    if box.get("caption"):
                        png = caption_png(box["caption"], build / f"action-{si:02d}-{beat.index:02d}-{bi}.png", size[0], accent=(255, 204, 0, 255), size_pt=20)
                        box["caption_png"] = str(png) if png else None
            wav = build / f"beat-{si:02d}-{beat.index:02d}.wav"
            beat_audio(beat, samples, rate, wav)
            dest = build / f"beat-{si:02d}-{beat.index:02d}-{_safe(step.id)}.mp4"
            render_beat(raw, beat, wav, dest, size, caption, annotate)
            pieces.append(dest)
            moment = clock + beat.voice_at
            for pi, phrase in enumerate(beat.phrases):
                length = phrase.end - phrase.start
                text = phrase.text
                if step.legal_note and beat is beats[-1] and pi == len(beat.phrases) - 1:
                    text += "\nПочему так: " + step.legal_note.strip()
                cues.append((round(moment, 3), round(moment + length, 3), text))
                moment += length
            clock += beat.duration
        report_steps.append(
            {
                "id": step.id,
                "phrase_note": note,
                "revoiced": replaced,
                "beats": [
                    {
                        "action": None if b.action is None else step.actions[b.action].label,
                        "raw": [b.raw_start, b.raw_end],
                        "shown": b.shown,
                        "voice_at": b.voice_at,
                        "voice": b.voice,
                        "hold": b.hold,
                        "duration": b.duration,
                        "phrases": [p.text for p in b.phrases],
                        "boxes": [{k: v for k, v in x.items()} for x in b.boxes],
                        "notes": b.notes,
                    }
                    for b in beats
                ],
            }
        )
    if outro is not None:
        clip = build / "outro.mp4"
        normalize_clip(outro, clip, size)
        pieces.append(clip)
    final = out_dir / "lesson.mp4"
    concat_same(pieces, final)
    srt = out_dir / "subtitles.srt"
    write_srt(cues, srt)
    report = {
        "mode": "anchored",
        "raw_screen": str(raw),
        "raw_duration": round(total, 2),
        "video": str(final),
        "subtitles": str(srt),
        "duration_seconds": round(probe_video(final)["duration"], 2),
        "annotations": annotate,
        "speech_tempo": 1.0,
        "freezes": [[round(a, 2), round(b, 2)] for a, b in freezes],
        "settings": {"lead": lead, "margin": margin, "freeze_noise": freeze_noise, "freeze_min": freeze_min},
        "steps": report_steps,
    }
    (out_dir / "edit-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def load_anchored_journal(path: Path) -> tuple[Path, list[AnchoredStep], dict] | None:
    """Журнал с действиями (steps[].actions). Если действий нет — None, нужен старый монтаж."""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not all(item.get("actions") for item in data.get("steps") or []):
        return None
    base = path.parent
    raw = Path(data["raw"])
    raw = raw if raw.is_absolute() else base / raw
    steps = []
    for item in data["steps"]:
        wav = Path(item["wav"])
        wav = wav if wav.is_absolute() else base / wav
        actions = [
            Action(
                label=str(a.get("label") or a.get("name") or ""),
                start=float(a["start"]),
                end=float(a.get("end", float(a["start"]) + 0.5)),
                caption=a.get("caption") or "",
                rect=tuple(int(v) for v in a["rect"]) if a.get("rect") else None,
                say=a.get("say") or "",
                box=bool(a.get("box", True)),
            )
            for a in item["actions"]
        ]
        phrase_wavs = {}
        for key, value in (item.get("phrase_wavs") or {}).items():
            path = Path(value)
            phrase_wavs[int(key)] = path if path.is_absolute() else base / path
        steps.append(
            AnchoredStep(
                id=str(item["id"]),
                narration=item["narration"],
                wav=wav,
                actions=actions,
                title=item.get("title") or "",
                anchors=item.get("anchors"),
                legal_note=item.get("legal_note") or "",
                phrase_wavs=phrase_wavs,
            )
        )
    return raw, steps, data


def steps_from_live_cues(cues: list[dict]) -> list[AnchoredStep]:
    """Журнал pipeline live: у каждого шага есть actions с временем и рамкой UI Automation."""
    steps = []
    for cue in cues:
        actions = [
            Action(label=str(a.get("label") or ""), start=float(a["start"]), end=float(a.get("end", a["start"])),
                   caption=a.get("caption") or "", rect=tuple(int(v) for v in a["rect"]) if a.get("rect") else None)
            for a in cue.get("actions") or []
        ]
        steps.append(AnchoredStep(id=str(cue["step"]), narration=cue["narration"], wav=Path(cue["wav"]), actions=actions,
                                  title=cue.get("title") or "", anchors=cue.get("anchors"), legal_note=cue.get("legal_note") or ""))
    return steps
