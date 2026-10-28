"""Запись урока в открытой 1С и пробный прогон без проведения."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from pipeline.frames import render_title_card
from pipeline.lesson_yaml import action_plan, load_lesson
from pipeline.postprod import run_ffmpeg, wav_duration
from pipeline.proc import text_of
from pipeline.tts import speak_clip
from pipeline.ui_session import EscWatcher, PywinautoSession, StopRequested


class Overlay:
    def show(self, element, caption: str, subtitle: str) -> None:
        del element, caption, subtitle

    def close(self) -> None:
        return None


class WinOverlay(Overlay):
    """Рамка поверх экрана. В запись gdigrab она попадает вместе с окном 1С."""

    def __init__(self) -> None:
        self._root = None
        self._window = None

    def show(self, element, caption: str, subtitle: str) -> None:
        import tkinter as tk

        if self._root is None:
            self._root = tk.Tk()
            self._root.withdraw()
            self._window = tk.Toplevel(self._root)
            self._window.overrideredirect(True)
            self._window.attributes("-topmost", True)
            try:
                self._window.attributes("-alpha", 0.86)
            except Exception:
                pass
            self._window.configure(bg="#E08900")
            self._label = tk.Label(
                self._window,
                text="",
                bg="#142033",
                fg="white",
                justify="left",
                font=("Segoe UI", 12),
                wraplength=520,
            )
            self._label.pack(fill="both", expand=True, padx=3, pady=3)
        text = caption
        if subtitle:
            text = f"{caption}\n{subtitle[:280]}"
        self._label.configure(text=text)
        width = max(220, min(560, element.right - element.left + 80))
        height = 90
        x = max(0, element.left)
        y = max(0, element.top - height - 8)
        self._window.geometry(f"{width}x{height}+{x}+{y}")
        self._window.update()

    def close(self) -> None:
        if self._root is not None:
            try:
                self._root.destroy()
            except Exception:
                pass
            self._root = None


class MemoryOverlay(Overlay):
    def __init__(self) -> None:
        self.frames: list[str] = []

    def show(self, element, caption: str, subtitle: str) -> None:
        self.frames.append(caption)


def gdigrab_command(ffmpeg: str, rect: tuple[int, int, int, int], path: Path) -> list[str]:
    left, top, right, bottom = rect
    width = max(2, (right - left) // 2 * 2)
    height = max(2, (bottom - top) // 2 * 2)
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "gdigrab",
        "-framerate",
        "8",
        "-offset_x",
        str(int(left)),
        "-offset_y",
        str(int(top)),
        "-video_size",
        f"{width}x{height}",
        "-i",
        "desktop",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]


class ScreenGrab:
    def __init__(self) -> None:
        self.proc = None
        self.path: Path | None = None

    def start(self, rect: tuple[int, int, int, int], path: Path) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("Не найден ffmpeg. Без него запись экрана не начнётся.")
        path.parent.mkdir(parents=True, exist_ok=True)
        command = gdigrab_command(ffmpeg, rect, path)
        self.path = path
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def stop(self) -> str:
        if self.proc is None:
            return ""
        try:
            if self.proc.stdin:
                self.proc.stdin.write(b"q\n")
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            error = self.proc.stderr.read() if self.proc.stderr else b""
        except Exception:
            error = b""
        try:
            self.proc.wait(timeout=15)
        except Exception:
            self.proc.kill()
        self.proc = None
        return error.decode("utf-8", errors="replace")


def _kind_label(kind: str) -> str:
    return {
        "navigate": "открыть",
        "mutate": "нажать при записи",
        "show": "показать",
        "fill": "заполнить при записи",
    }.get(kind, kind)


def dry_run(session, lesson: dict, timeout: float = 0.8) -> dict:
    """Ищет подписи. Не проводит, не заполняет и не пишет видео."""
    session.connect()
    rows = []
    for scene in lesson["scenes"]:
        for action in action_plan(scene):
            row = {
                "step": scene["id"],
                "element": action["name"],
                "kind": action["kind"],
                "role": action.get("role") or "",
                "within": action.get("within") or "",
                "index": action.get("index"),
                "found": False,
                "ambiguous": False,
                "note": "",
                "performed": "только поиск",
                "would": _kind_label(action["kind"]),
                "narration": scene["narration"],
                "legal_note": scene["legal_note"],
            }
            try:
                element = session.find(
                    action["name"],
                    timeout=timeout,
                    role=action.get("role") or "",
                    within=action.get("within") or "",
                    index=action.get("index"),
                )
                row["found"] = True
                row["ambiguous"] = bool(getattr(element, "ambiguous", False))
                row["note"] = getattr(element, "note", "") or ""
                if row["ambiguous"]:
                    row["note"] = (row["note"] + "; выбран один видимый элемент").strip("; ")
            except StopRequested:
                raise
            except Exception as error:
                row["found"] = False
                row["note"] = str(error) or "не найден"
            rows.append(row)
    found = [row["element"] for row in rows if row["found"]]
    missing = [row["element"] for row in rows if not row["found"]]
    seen = False
    try:
        seen = bool(session.organization_visible(lesson["organization"]))
    except Exception as error:
        seen = False
        missing.append(f"организация: {error}")
    return {
        "status": "checked",
        "mode": "dry-run",
        "recorded": False,
        "posted": False,
        "filled": False,
        "organization": lesson["organization"],
        "organization_seen": seen,
        "found": found,
        "missing": missing,
        "steps": rows,
    }


def _needs_smartphone(lesson: dict) -> bool:
    import json as _json

    return "смартфон" in _json.dumps(lesson, ensure_ascii=False).lower()


def _perform(session, overlay: Overlay, action: dict, scene: dict) -> None:
    element = session.find(
        action["name"],
        timeout=8,
        role=action.get("role") or "",
        within=action.get("within") or "",
        index=action.get("index"),
    )
    caption = action["name"]
    subtitle = scene["narration"]
    if scene.get("legal_note"):
        subtitle = scene["narration"] + " Почему так: " + scene["legal_note"]
    overlay.show(element, caption, subtitle)
    session.move_cursor((element.left + element.right) // 2, (element.top + element.bottom) // 2)
    if action["kind"] == "show":
        return
    if action["kind"] == "fill":
        session.set_text(element, action.get("value") or "")
        return
    session.click(element)


def record_lesson(session, lesson: dict, out_dir: Path, project_root: Path, tts_name: str, voice: str, overlay: Overlay | None = None, grab: ScreenGrab | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    overlay = overlay or WinOverlay()
    grab = grab if grab is not None else ScreenGrab()
    watcher = getattr(session, "watcher", None)
    started = time.time()
    cues = []
    raw_error = ""
    try:
        session.connect()
        if not session.organization_visible(lesson["organization"]):
            raise RuntimeError(
                f"В открытом окне не видно организацию «{lesson['organization']}». "
                "Откройте учебную базу и не переключайтесь на рабочую."
            )
        grab.start(session.window_rect(), out_dir / "raw-screen.mp4")
        if _needs_smartphone(lesson):
            from pipeline.tnved import ensure_smartphone

            created = ensure_smartphone(session)
            speech = out_dir / "voice" / "smartphone.wav"
            speak_clip(created["message"], speech, tts_name, voice, project_root)
            cues.append({
                "step": "smartphone",
                "start": round(time.time() - started, 3),
                "narration": created["message"],
                "legal_note": "Код ТН ВЭД 8517 13 000 0 нужен для ставки сбора 73 860 ₽ по ПП РФ № 1637.",
                "wav": str(speech),
            })
        for scene in lesson["scenes"]:
            if watcher:
                watcher.poll()
            speech = out_dir / "voice" / f"step-{scene['id']}.wav"
            speak_clip(scene["narration"], speech, tts_name, voice, project_root)
            cues.append(
                {
                    "step": scene["id"],
                    "start": round(time.time() - started, 3),
                    "narration": scene["narration"],
                    "legal_note": scene["legal_note"],
                    "wav": str(speech),
                }
            )
            for action in action_plan(scene):
                if watcher:
                    watcher.poll()
                try:
                    _perform(session, overlay, action, scene)
                except LookupError as error:
                    raise RuntimeError(
                        f"Шаг {scene['id']}: не найден элемент «{error}». "
                        "Откройте нужный раздел. Точную подпись можно взять из пробного прогона."
                    ) from None
                time.sleep(0.15)
    finally:
        raw_error = grab.stop()
        overlay.close()
    raw = out_dir / "raw-screen.mp4"
    if not raw.exists() or raw.stat().st_size < 1000:
        raise RuntimeError("Запись экрана не сохранилась. " + text_of(raw_error)[-500:])
    video = _finish_movie(lesson, cues, raw, out_dir, project_root, tts_name, voice)
    return {
        "status": "recorded",
        "mode": "record",
        "recorded": True,
        "video": str(video),
        "raw_screen": str(raw),
        "organization": lesson["organization"],
        "step_count": len(lesson["scenes"]),
    }


def _finish_movie(lesson, cues, raw: Path, out_dir: Path, project_root: Path, tts_name: str, voice: str) -> Path:
    intro_wav = out_dir / "voice" / "intro.wav"
    outro_wav = out_dir / "voice" / "outro.wav"
    speak_clip(lesson["intro_narration"], intro_wav, tts_name, voice, project_root)
    speak_clip(lesson["outro_narration"], outro_wav, tts_name, voice, project_root)
    intro = out_dir / "intro.mp4"
    outro = out_dir / "outro.mp4"
    _card_clip(lesson["card_title"], lesson["configuration"], "Учебная база. Отправки в ФНС нет.", intro_wav, intro)
    _card_clip(lesson["card_title"], lesson["configuration"], "Конец урока", outro_wav, outro)
    narrated = out_dir / "screen-narrated.mp4"
    _mux_timeline(raw, cues, narrated)
    final = out_dir / "lesson.mp4"
    from pipeline.postprod import _concat

    _concat([intro, narrated, outro], final, with_audio=True)
    _write_srt(cues, out_dir / "subtitles.srt", intro_duration=wav_duration(intro_wav))
    return final


def _card_clip(title: str, configuration: str, footer: str, wav: Path, dest: Path) -> None:
    png = dest.with_suffix(".png")
    render_title_card(title, configuration, footer, png)
    duration = max(2.0, wav_duration(wav))
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
            "-i",
            str(wav),
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(dest),
        ]
    )


def _mux_timeline(video: Path, cues: list[dict], dest: Path) -> None:
    if not cues:
        raise RuntimeError("Нет озвучки шагов.")
    args: list[str] = ["-i", str(video)]
    filters = []
    for index, cue in enumerate(cues):
        args.extend(["-i", cue["wav"]])
        delay = max(0, int(float(cue["start"]) * 1000))
        filters.append(f"[{index + 1}:a]adelay={delay}|{delay},apad[a{index}]")
    mix = "".join(f"[a{index}]" for index in range(len(cues)))
    filters.append(f"{mix}amix=inputs={len(cues)}:normalize=0:duration=longest[a]")
    args.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "0:v:0",
            "-map",
            "[a]",
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
    run_ffmpeg(args)


def _write_srt(cues: list[dict], path: Path, intro_duration: float) -> None:
    blocks = []
    for index, cue in enumerate(cues, start=1):
        start = intro_duration + float(cue["start"])
        end = start + 4
        if index < len(cues):
            end = intro_duration + float(cues[index]["start"])
        text = cue["narration"].strip()
        if cue.get("legal_note"):
            text += "\nПочему так: " + cue["legal_note"].strip()
        blocks.append(f"{index}\n{_ts(start)} --> {_ts(end)}\n{text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")


def _ts(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def dump_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def open_session(backend: str, watcher: EscWatcher):
    if backend == "fake":
        raise RuntimeError("Учебный имитатор вызывается только из проверки, не из командного файла.")
    if backend != "uia":
        raise RuntimeError("Для живой 1С нужен режим uia.")
    return PywinautoSession(watcher)


def run_dry(lesson_path: Path, out_dir: Path, session=None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "failed",
        "mode": "dry-run",
        "recorded": False,
        "posted": False,
        "filled": False,
        "found": [],
        "missing": [],
        "steps": [],
        "error": None,
    }
    try:
        lesson = load_lesson(lesson_path)
        session = session or PywinautoSession(EscWatcher())
        report = dry_run(session, lesson)
    except StopRequested as error:
        report["status"] = "stopped"
        report["error"] = str(error)
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
    finally:
        dump_json(out_dir / "dry-run.json", report)
    return report


def run_record(lesson_path: Path, out_dir: Path, project_root: Path, tts_name: str, voice: str, session=None, overlay=None, grab=None) -> dict:
    lesson = load_lesson(lesson_path)
    session = session or PywinautoSession(EscWatcher())
    try:
        result = record_lesson(session, lesson, out_dir, project_root, tts_name, voice, overlay, grab)
    except StopRequested as error:
        result = {"status": "stopped", "recorded": False, "message": str(error)}
    dump_json(out_dir / "live-result.json", result)
    return result
