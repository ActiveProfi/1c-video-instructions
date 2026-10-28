"""Монтаж: озвучка, субтитры, рамки, стрелки, заставка и концовка."""

from __future__ import annotations

import re
import shutil
import wave
from pathlib import Path

from pipeline.frames import annotate_frame, render_title_card
from pipeline.proc import loads_json, run_captured, text_of
from pipeline.tts import speak_clip


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe() -> str:
    return shutil.which("ffprobe") or "ffprobe"


def run_ffmpeg(args: list[str]) -> None:
    result = run_captured([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args])
    if result.returncode:
        detail = text_of(result.stderr).strip() or text_of(result.stdout).strip()
        raise RuntimeError("ffmpeg не собрал фрагмент: " + detail[-1500:])


def wav_duration(path: Path) -> float:
    with wave.open(str(path)) as handle:
        return handle.getnframes() / float(handle.getframerate())


def pad_wav(path: Path, minimum: float) -> float:
    current = wav_duration(path)
    if current >= minimum:
        return current
    with wave.open(str(path), "rb") as handle:
        params = handle.getparams()
        frames = handle.readframes(handle.getnframes())
    extra = int((minimum - current) * params.framerate)
    silence = b"\x00" * extra * params.sampwidth * params.nchannels
    with wave.open(str(path), "wb") as handle:
        handle.setparams(params)
        handle.writeframes(frames + silence)
    return minimum


def sentences(text: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if part.strip()]
    return parts or [text.strip()]


def cue_spans(parts: list[str], start: float, duration: float) -> list[tuple[float, float, str]]:
    weights = [max(1, len(part)) for part in parts]
    total = float(sum(weights))
    cursor = start
    cues = []
    for part, weight in zip(parts, weights):
        length = duration * weight / total
        cues.append((cursor, cursor + length, part))
        cursor += length
    return cues


def active_sentence(cues: list[tuple[float, float, str]], moment: float) -> str:
    for begin, end, text in cues:
        if begin <= moment < end:
            return text
    return cues[-1][2] if cues else ""


def format_timestamp(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(cues: list[tuple[float, float, str]], path: Path) -> None:
    blocks = []
    for index, (begin, end, text) in enumerate(cues, start=1):
        blocks.append(f"{index}\n{format_timestamp(begin)} --> {format_timestamp(end)}\n{text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")


def _encode_still(png: Path, duration: float, dest: Path) -> None:
    run_ffmpeg(
        [
            "-loop",
            "1",
            "-framerate",
            "10",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(png),
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-r",
            "10",
            "-an",
            str(dest),
        ]
    )


def _concat(paths: list[Path], dest: Path, with_audio: bool) -> None:
    """Склеивает ролики аргументами ffmpeg, без текстового списка.

    Список файлов на русском Windows читался бы в кодировке cp1251 и ломал
    путь с русскими буквами. Аргументы командной строки такой проблемы не имеют.
    """
    if not paths:
        raise RuntimeError("Нет фрагментов для склейки.")
    if len(paths) == 1:
        if with_audio:
            run_ffmpeg(
                [
                    "-i",
                    str(paths[0]),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "23",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-movflags",
                    "+faststart",
                    str(dest),
                ]
            )
        else:
            run_ffmpeg(["-i", str(paths[0]), "-c", "copy", str(dest)])
        return
    args: list[str] = []
    for path in paths:
        args.extend(["-i", str(path)])
    count = len(paths)
    if with_audio:
        chain = "".join(f"[{index}:v][{index}:a]" for index in range(count))
        args.extend(
            [
                "-filter_complex",
                f"{chain}concat=n={count}:v=1:a=1[v][a]",
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(dest),
            ]
        )
    else:
        chain = "".join(f"[{index}:v]" for index in range(count))
        args.extend(
            [
                "-filter_complex",
                f"{chain}concat=n={count}:v=1:a=0[v]",
                "-map",
                "[v]",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                str(dest),
            ]
        )
    run_ffmpeg(args)


def _mux(video: Path, audio: Path, dest: Path) -> None:
    run_ffmpeg(
        [
            "-i",
            str(video),
            "-i",
            str(audio),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(dest),
        ]
    )


def _resolve_frame(out_dir: Path, frame: str) -> Path:
    path = Path(frame)
    if not path.is_absolute():
        path = out_dir / path
    if not path.exists():
        raise FileNotFoundError(f"Нет кадра шага: {path}")
    return path


def verify_media(path: Path) -> dict:
    probe = run_captured(
        [
            _ffprobe(),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ]
    )
    if probe.returncode:
        detail = text_of(probe.stderr).strip() or text_of(probe.stdout).strip()
        raise RuntimeError("ffprobe не прочитал готовый файл: " + detail[-800:])
    data = loads_json(probe.stdout, "ffprobe")
    kinds = {stream.get("codec_type"): stream.get("codec_name") for stream in data.get("streams") or []}
    if "video" not in kinds or "audio" not in kinds:
        raise RuntimeError("В готовом файле нет и картинки, и звука.")
    decode = run_captured([_ffmpeg(), "-v", "error", "-i", str(path), "-f", "null", "-"])
    if decode.returncode:
        detail = text_of(decode.stderr).strip() or text_of(decode.stdout).strip()
        raise RuntimeError("Готовый файл не декодируется: " + detail[-800:])
    return {
        "video": kinds["video"],
        "audio": kinds["audio"],
        "duration_seconds": round(float(data["format"]["duration"]), 2),
    }


def assemble(plan: dict, journal: dict, out_dir: Path, provider: str, voice: str, project_root: Path) -> dict:
    build = out_dir / "build"
    build.mkdir(parents=True, exist_ok=True)
    voice_dir = out_dir / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    events = {item["step_id"]: item for item in journal["events"]}
    pieces = []
    cues = []
    clock = 0.0
    warnings = []
    used_provider = provider

    def speak(name: str, text: str, minimum: float) -> tuple[Path, float]:
        nonlocal used_provider
        dest = voice_dir / f"{name}.wav"
        chosen, warning = speak_clip(text, dest, provider, voice, project_root)
        used_provider = chosen
        if warning:
            warnings.append(warning)
        duration = pad_wav(dest, minimum)
        return dest, duration

    def add_card(name: str, big_title: str, footer: str, narration: str) -> None:
        nonlocal clock
        card = build / f"{name}.png"
        render_title_card(big_title, plan["configuration"], footer, card)
        parts = sentences(narration)
        audio, duration = speak(name, narration, max(2.4, 1.2 * len(parts)))
        local = cue_spans(parts, 0, duration)
        subtitled = build / f"{name}-sub.png"
        annotate_frame(card, subtitled, None, "", local[0][2])
        silent = build / f"{name}-silent.mp4"
        _encode_still(subtitled, duration, silent)
        mixed = build / f"{name}.mp4"
        _mux(silent, audio, mixed)
        pieces.append(mixed)
        cues.extend(cue_spans(parts, clock, duration))
        clock += duration

    add_card(
        "intro",
        plan["card_title"],
        "Учебный ролик. Сверьте шаги со своей базой.",
        plan["intro_narration"],
    )

    for scene in plan["scenes"]:
        scene_events = [events[step_id] for step_id in scene["step_ids"]]
        parts = sentences(scene["narration"])
        audio, duration = speak(scene["id"], scene["narration"], max(len(scene_events) * 1.05, 1.2 * len(parts)))
        local = cue_spans(parts, 0, duration)
        step_duration = duration / len(scene_events)
        silent_parts = []
        for index, event in enumerate(scene_events):
            moment = (index + 0.5) * step_duration
            frame = build / f"{event['step_id']}.png"
            annotate_frame(
                _resolve_frame(out_dir, event["frame"]),
                frame,
                event.get("bounds"),
                event.get("caption") or "",
                active_sentence(local, moment),
            )
            silent = build / f"{event['step_id']}.mp4"
            _encode_still(frame, step_duration, silent)
            silent_parts.append(silent)
        silent_scene = build / f"{scene['id']}-silent.mp4"
        _concat(silent_parts, silent_scene, with_audio=False)
        mixed = build / f"{scene['id']}.mp4"
        _mux(silent_scene, audio, mixed)
        pieces.append(mixed)
        cues.extend(cue_spans(parts, clock, duration))
        clock += duration

    add_card(
        "outro",
        plan["card_title"],
        "Концовка урока",
        plan["outro_narration"],
    )

    video = out_dir / "lesson.mp4"
    _concat(pieces, video, with_audio=True)
    srt_path = out_dir / "subtitles.srt"
    write_srt(cues, srt_path)
    checked = verify_media(video)
    raw = journal.get("raw_screen")
    return {
        "status": "verified",
        "video": str(video),
        "subtitles": str(srt_path),
        "duration_seconds": checked["duration_seconds"],
        "codecs": {"video": checked["video"], "audio": checked["audio"]},
        "driver": journal.get("driver"),
        "mocked": bool(journal.get("mocked")),
        "tts": used_provider,
        "voice": voice,
        "tts_warning": warnings[0] if warnings else None,
        "card_title": plan["card_title"],
        "configuration": plan["configuration"],
        "needs_review": plan["needs_review"],
        "disclaimer": plan["disclaimer"],
        "sources": plan["sources"],
        "annotations": ["highlight", "arrow", "subtitles", "intro", "outro"],
        "raw_screen": raw,
        "scene_count": len(plan["scenes"]),
        "step_count": len(plan["steps"]),
    }
