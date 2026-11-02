"""Монтаж урока из непрерывной записи экрана.

По отметкам времени шагов из журнала режем raw-screen.mp4, выбрасываем
паузы без действий (ffmpeg freezedetect), подгоняем длительность каждого
куска под озвучку шага: длинный кусок ускоряем (не больше max_speed),
короткий продлеваем последним кадром. Затем заставка, шаги, концовка и
субтитры. Если что-то пошло не так, вызывающий код возвращается к старому
режиму (наложение голоса на запись или ролик из кадров).
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.postprod import cue_spans, format_timestamp, run_ffmpeg, sentences, wav_duration
from pipeline.proc import loads_json, run_captured, text_of


@dataclass
class Step:
    id: str
    narration: str
    wav: Path
    windows: list[tuple[float, float]]
    legal_note: str = ""


@dataclass
class SegmentPlan:
    id: str
    keep: list[tuple[float, float]]
    active: float
    speed: float
    hold: float
    voice: float
    duration: float
    removed: float
    notes: list[str] = field(default_factory=list)


def _ffprobe() -> str:
    return shutil.which("ffprobe") or "ffprobe"


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def probe_video(path: Path) -> dict:
    result = run_captured([_ffprobe(), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    if result.returncode:
        raise RuntimeError("ffprobe не прочитал запись: " + (text_of(result.stderr) or "")[-500:])
    data = loads_json(result.stdout, "ffprobe")
    video = next((s for s in data.get("streams") or [] if s.get("codec_type") == "video"), None)
    if not video:
        raise RuntimeError(f"В записи нет видео: {path}")
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    return {"width": int(video["width"]), "height": int(video["height"]), "duration": duration}


FREEZE_START = re.compile(r"freeze_start:\s*([0-9.]+)")
FREEZE_END = re.compile(r"freeze_end:\s*([0-9.]+)")


def parse_freezes(log: str, total: float) -> list[tuple[float, float]]:
    freezes: list[tuple[float, float]] = []
    start = None
    for line in log.splitlines():
        found = FREEZE_START.search(line)
        if found:
            start = float(found.group(1))
            continue
        found = FREEZE_END.search(line)
        if found and start is not None:
            freezes.append((start, float(found.group(1))))
            start = None
    if start is not None:
        freezes.append((start, total))
    return freezes


def detect_freezes(path: Path, total: float, noise: float = 0.0005, min_duration: float = 0.8) -> list[tuple[float, float]]:
    result = run_captured(
        [_ffmpeg(), "-hide_banner", "-nostats", "-i", str(path), "-vf", f"freezedetect=n={noise}:d={min_duration}", "-an", "-f", "null", "-"]
    )
    if result.returncode:
        raise RuntimeError("freezedetect не сработал: " + (text_of(result.stderr) or "")[-500:])
    return parse_freezes(text_of(result.stderr) + "\n" + text_of(result.stdout), total)


def _subtract(window: tuple[float, float], freezes: list[tuple[float, float]], margin: float) -> list[tuple[float, float]]:
    """Окно минус паузы. От каждой паузы оставляем margin секунд с краёв, чтобы результат действия был виден."""
    pieces = [window]
    for f_start, f_end in freezes:
        cut_start, cut_end = f_start + margin, f_end - margin
        if cut_end - cut_start <= 0.05:
            continue
        nxt = []
        for a, b in pieces:
            if cut_end <= a or cut_start >= b:
                nxt.append((a, b))
                continue
            if cut_start > a:
                nxt.append((a, cut_start))
            if cut_end < b:
                nxt.append((cut_end, b))
        pieces = nxt
    return [(round(a, 3), round(b, 3)) for a, b in pieces if b - a > 0.05]


def plan_segment(
    step_id: str,
    windows: list[tuple[float, float]],
    freezes: list[tuple[float, float]],
    voice: float,
    total: float,
    pad: float = 0.35,
    margin: float = 0.4,
    max_speed: float = 2.0,
    min_keep: float = 1.2,
) -> SegmentPlan:
    keep: list[tuple[float, float]] = []
    notes = []
    raw_len = 0.0
    for start, end in windows:
        start, end = max(0.0, start), min(total, end) if total else end
        if end - start <= 0.05:
            continue
        raw_len += end - start
        part = _subtract((start, end), freezes, margin)
        if not part:
            part = [(start, min(end, start + min_keep))]
            notes.append("окно целиком без движения, оставлен короткий кадр")
        keep.extend(part)
    if not keep:
        raise RuntimeError(f"Шаг {step_id}: нет отрезка записи.")
    active = sum(b - a for a, b in keep)
    target = voice + pad
    speed = 1.0
    if active > target:
        speed = min(max_speed, active / target)
        if active / speed > target + 0.05:
            notes.append(f"действия длиннее голоса даже при ускорении x{max_speed:g}")
    shown = active / speed
    hold = max(0.0, target - shown)
    return SegmentPlan(
        id=step_id,
        keep=keep,
        active=round(active, 3),
        speed=round(speed, 4),
        hold=round(hold, 3),
        voice=round(voice, 3),
        duration=round(shown + hold, 3),
        removed=round(max(0.0, raw_len - active), 3),
        notes=notes,
    )


def _even(value: int) -> int:
    return max(2, value // 2 * 2)


def render_segment(raw: Path, plan: SegmentPlan, wav: Path, dest: Path, size: tuple[int, int], fps: int = 25) -> None:
    width, height = size
    parts = []
    labels = []
    for index, (a, b) in enumerate(plan.keep):
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[p{index}]")
        labels.append(f"[p{index}]")
    chain = "".join(labels)
    video = (
        f"{chain}concat=n={len(labels)}:v=1:a=0[c];"
        f"[c]setpts=PTS/{plan.speed:.4f},fps={fps},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x101827,setsar=1,"
        f"tpad=stop_mode=clone:stop_duration={plan.hold + 0.5:.3f},format=yuv420p[v]"
    )
    audio = "[1:a]aresample=48000,aformat=channel_layouts=stereo,apad[a]"
    run_ffmpeg(
        [
            "-i", str(raw), "-i", str(wav),
            "-filter_complex", ";".join(parts + [video, audio]),
            "-map", "[v]", "-map", "[a]",
            "-t", f"{plan.duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-c:a", "aac", "-b:a", "160k",
            str(dest),
        ]
    )


def normalize_clip(src: Path, dest: Path, size: tuple[int, int], fps: int = 25) -> None:
    """Заставка и концовка в размер записи, чтобы склейка не ломалась."""
    width, height = size
    run_ffmpeg(
        [
            "-i", str(src),
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x101827,setsar=1,fps={fps},format=yuv420p",
            "-af", "aresample=48000,aformat=channel_layouts=stereo",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-c:a", "aac", "-b:a", "160k",
            str(dest),
        ]
    )


def concat_same(paths: list[Path], dest: Path) -> None:
    args: list[str] = []
    for path in paths:
        args.extend(["-i", str(path)])
    chain = "".join(f"[{i}:v][{i}:a]" for i in range(len(paths)))
    args.extend(
        [
            "-filter_complex", f"{chain}concat=n={len(paths)}:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            str(dest),
        ]
    )
    run_ffmpeg(args)


def subtitle_cues(steps: list[Step], plans: list[SegmentPlan], offset: float) -> list[tuple[float, float, str]]:
    cues = []
    clock = offset
    for step, plan in zip(steps, plans):
        parts = sentences(step.narration)
        spans = cue_spans(parts, clock, max(0.5, plan.voice))
        if step.legal_note and spans:
            begin, end, text = spans[-1]
            spans[-1] = (begin, end, text + "\nПочему так: " + step.legal_note.strip())
        cues.extend(spans)
        clock += plan.duration
    return cues


def write_srt(cues: list[tuple[float, float, str]], path: Path) -> None:
    blocks = [f"{i}\n{format_timestamp(a)} --> {format_timestamp(b)}\n{t}\n" for i, (a, b, t) in enumerate(cues, start=1)]
    path.write_text("\n".join(blocks), encoding="utf-8")


def steps_from_cues(cues: list[dict], total: float) -> list[Step]:
    """Журнал live-режима: у шага есть только начало; конец — начало следующего."""
    ordered = sorted(cues, key=lambda c: float(c["start"]))
    steps = []
    for index, cue in enumerate(ordered):
        start = float(cue["start"])
        end = float(ordered[index + 1]["start"]) if index + 1 < len(ordered) else total
        if cue.get("end") is not None:
            end = float(cue["end"])
        steps.append(Step(id=str(cue["step"]), narration=cue["narration"], wav=Path(cue["wav"]), windows=[(start, end)], legal_note=cue.get("legal_note") or ""))
    return steps


def load_journal(path: Path) -> tuple[Path, list[Step], dict]:
    """Формат edit-journal.json: raw, steps[{id, narration, wav, windows|start/end, legal_note}], intro/outro."""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    base = path.parent
    raw = Path(data["raw"])
    raw = raw if raw.is_absolute() else base / raw
    steps = []
    for item in data["steps"]:
        wav = Path(item["wav"])
        wav = wav if wav.is_absolute() else base / wav
        windows = item.get("windows") or [[item["start"], item["end"]]]
        steps.append(Step(id=str(item["id"]), narration=item["narration"], wav=wav, windows=[(float(a), float(b)) for a, b in windows], legal_note=item.get("legal_note") or ""))
    return raw, steps, data


def edit_continuous(
    raw: Path,
    steps: list[Step],
    out_dir: Path,
    intro: Path | None = None,
    outro: Path | None = None,
    max_speed: float = 2.0,
    pad: float = 0.35,
    freeze_noise: float = 0.0005,
    freeze_min: float = 0.8,
    use_freezedetect: bool = True,
    margin: float = 0.4,
) -> dict:
    """Собирает lesson.mp4 из непрерывной записи. Возвращает отчёт edit-report.json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    build = out_dir / "continuous"
    build.mkdir(parents=True, exist_ok=True)
    info = probe_video(raw)
    size = (_even(info["width"]), _even(info["height"]))
    freezes = detect_freezes(raw, info["duration"], freeze_noise, freeze_min) if use_freezedetect else []
    plans = []
    pieces: list[Path] = []
    intro_duration = 0.0
    if intro is not None:
        clip = build / "intro.mp4"
        normalize_clip(intro, clip, size)
        intro_duration = probe_video(clip)["duration"]
        pieces.append(clip)
    for index, step in enumerate(steps):
        plan = plan_segment(step.id, step.windows, freezes, wav_duration(step.wav), info["duration"], pad=pad, max_speed=max_speed, margin=margin)
        dest = build / f"step-{index:02d}-{_safe(step.id)}.mp4"
        render_segment(raw, plan, step.wav, dest, size)
        plans.append(plan)
        pieces.append(dest)
    if outro is not None:
        clip = build / "outro.mp4"
        normalize_clip(outro, clip, size)
        pieces.append(clip)
    final = out_dir / "lesson.mp4"
    concat_same(pieces, final)
    srt = out_dir / "subtitles.srt"
    write_srt(subtitle_cues(steps, plans, intro_duration), srt)
    report = {
        "mode": "continuous",
        "raw_screen": str(raw),
        "raw_duration": round(info["duration"], 2),
        "video": str(final),
        "subtitles": str(srt),
        "duration_seconds": round(probe_video(final)["duration"], 2),
        "freezes": [[round(a, 2), round(b, 2)] for a, b in freezes],
        "removed_seconds": round(sum(p.removed for p in plans), 2),
        "settings": {"max_speed": max_speed, "pad": pad, "freeze_noise": freeze_noise, "freeze_min": freeze_min, "margin": margin},
        "segments": [plan.__dict__ for plan in plans],
    }
    (out_dir / "edit-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _safe(name: str) -> str:
    return re.sub(r"[^0-9A-Za-zА-Яа-яЁё_-]+", "_", name)[:40] or "step"
