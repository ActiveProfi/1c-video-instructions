"""Свежая запись урока «книги по УПД»: одна короткая фраза — один показ.

Речь начинается в тот момент, когда курсор уже на элементе и рамка снята
с окна. Старые документы не открываются, не меняются и не удаляются.
Если подпись не найдена или их несколько, проход останавливается.
Запасной монтаж без привязки ко времени здесь не вызывается.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

from pipeline.postprod import write_srt
from pipeline.pronunciation import strip_stress
from pipeline.tts import read_dotenv, speak_clip, speech_cache_file
from pipeline.ui_session import AmbiguousTarget, guard_name

LILY_VOICE = "pFZP5JQG7iQjIQuC4Bku"
LILY_MODEL = "eleven_multilingual_v2"
RESERVED_NUMBERS = ("УЧ-КНИГИ-001", "УЧ-КНИГИ-002")
REQUIRED_COVERS = ("contract", "vat_rate", "vat_amount", "total", "invoice", "entries")
NUMBER_RE = re.compile(r"УЧ-КНИГИ-\d+(?:-\d+)?")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
UNSAFE_WORDS = ("удалить", "пометить на удаление", "восстанов", "загрузить базу", ".dt", "выгрузить", "отправить")
PLACEHOLDER = "{{incoming_number}}"


class TargetError(RuntimeError):
    def __init__(self, kind: str, target: str, message: str, beat: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.target = target
        self.beat = beat


class SyncAssemblyError(RuntimeError):
    """Сборка не стала бы роликом, где речь и картинка расходятся. Запасного монтажа нет."""


class Clock:
    def __init__(self, sleep=None, time_fn=None) -> None:
        self._t = 0.0
        self._sleep = sleep
        self._time = time_fn

    def now(self) -> float:
        if self._time is not None:
            return float(self._time())
        return self._t

    def sleep(self, seconds: float) -> None:
        pause = max(0.0, float(seconds))
        if self._sleep is not None:
            self._sleep(pause)
        if self._time is None:
            self._t += pause


def glide_path(x0: int, y0: int, x1: int, y1: int, steps: int = 10) -> list[tuple[int, int]]:
    """Плавный путь. Середина не прыгает сразу в конец."""
    steps = max(2, int(steps))
    points = []
    for index in range(steps + 1):
        t = index / steps
        eased = t * t * (3 - 2 * t)
        points.append((round(x0 + (x1 - x0) * eased), round(y0 + (y1 - y0) * eased)))
    return points


def _cursor_now(session) -> tuple[int, int]:
    current = getattr(session, "cursor", None)
    if current:
        return int(current[0]), int(current[1])
    try:
        import ctypes

        class Point(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        point = Point()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return int(point.x), int(point.y)
    except Exception:
        pass
    left, top, _right, _bottom = session.window_rect()
    return int(left) + 40, int(top) + 40


def glide_cursor(session, x: int, y: int, duration: float = 0.4, steps: int = 10, sleep=None) -> list[tuple[int, int]]:
    start = _cursor_now(session)
    path = glide_path(start[0], start[1], int(x), int(y), steps)
    pause = duration / max(1, len(path) - 1)
    sleeper = sleep or time.sleep
    for px, py in path:
        session.move_cursor(px, py)
        if pause:
            sleeper(pause)
    session.cursor_path = list(getattr(session, "cursor_path", [])) + path
    return path


def visible_incoming_numbers(session) -> list[str]:
    """Только чтение уже видимого текста. Документы не открываются."""
    chunks: list[str] = []
    for attr in ("texts", "names"):
        chunks.extend(str(item) for item in (getattr(session, attr, []) or []))
    values = getattr(session, "values", {}) or {}
    chunks.extend(str(item) for item in values.values())
    try:
        rows = session.dump_ui()
    except Exception as error:
        raise RuntimeError(
            "Не удалось прочитать номера с экрана. Старые документы не открывались и не менялись."
        ) from error
    for row in rows:
        texts = row.get("texts") or {}
        if isinstance(texts, dict):
            chunks.extend(str(item) for item in texts.values())
        elif texts:
            chunks.append(str(texts))
    found: list[str] = []
    for chunk in chunks:
        found.extend(NUMBER_RE.findall(chunk))
    return list(dict.fromkeys(found))


def allocate_incoming_number(seen, now: datetime) -> str:
    """Новый номер. УЧ-КНИГИ-001 и УЧ-КНИГИ-002 и всё, что видно на экране, заняты."""
    occupied = {str(item) for item in seen} | set(RESERVED_NUMBERS)
    stamp = now.strftime("%y%m%d%H%M%S")
    candidate = f"УЧ-КНИГИ-{stamp}"
    if candidate not in occupied:
        return candidate
    for suffix in range(2, 51):
        nxt = f"УЧ-КНИГИ-{stamp}-{suffix}"
        if nxt not in occupied:
            return nxt
    raise RuntimeError("Не удалось подобрать свободный номер. Старые документы не изменялись.")


def apply_number(text: str, number: str) -> str:
    return (text or "").replace(PLACEHOLDER, number)


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in SENTENCE_RE.split((text or "").strip()) if part.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def beats_from_lesson(lesson: dict) -> list[dict]:
    if lesson.get("recording") != "synced":
        raise ValueError("У урока нет признака recording: synced.")
    beats = []
    for scene in lesson.get("scenes") or []:
        sync = scene.get("sync") or {}
        narration = str(scene.get("narration") or "").strip()
        element = str(sync.get("element") or "").strip()
        kind = str(sync.get("kind") or "").strip()
        beat_id = str(scene.get("id") or "")
        if not beat_id or not narration or not element:
            raise ValueError(f"У шага «{beat_id or '?'}» нет короткой фразы или элемента.")
        if kind not in ("click", "fill", "point"):
            raise ValueError(f"Шаг {beat_id}: действие «{kind}» не поддерживается.")
        if len(narration) > 220 or len(_sentences(narration)) > 2:
            raise ValueError(f"Шаг {beat_id}: фраза слишком длинная. Нужно одно-два коротких предложения.")
        blob = " ".join(
            [
                narration,
                element,
                str(sync.get("value") or ""),
                " ".join(str(item) for item in (sync.get("aliases") or [])),
            ]
        ).lower()
        if any(word in blob for word in UNSAFE_WORDS):
            raise ValueError(f"Шаг {beat_id}: в шаге есть удаление, восстановление базы или отправка.")
        if str(sync.get("value") or "") in RESERVED_NUMBERS:
            raise ValueError(f"Шаг {beat_id}: нельзя снова записывать номер {sync.get('value')}.")
        guard_name(element)
        beats.append(
            {
                "id": beat_id,
                "narration": narration,
                "legal_note": str(scene.get("legal_note") or ""),
                "kind": kind,
                "element": element,
                "role": str(sync.get("role") or ""),
                "within": str(sync.get("within") or ""),
                "aliases": [str(item) for item in (sync.get("aliases") or []) if str(item) != element],
                "value": str(sync.get("value") or ""),
                "covers": str(sync.get("covers") or ""),
                "expect_contains": str(sync.get("expect_contains") or ""),
                "expect_digits": str(sync.get("expect_digits") or ""),
                "expect_name": str(sync.get("expect_name") or ""),
                "require_filled": bool(sync.get("require_filled")),
                "rescan_after": bool(sync.get("rescan_after")),
                "mutates": bool(sync.get("mutates")),
                "uses_number": PLACEHOLDER in narration or PLACEHOLDER in str(sync.get("value") or ""),
            }
        )
    covers = {beat["covers"] for beat in beats}
    missing = [name for name in REQUIRED_COVERS if name not in covers]
    if missing:
        raise ValueError("В уроке нет показа: " + ", ".join(missing))
    entries = [beat for beat in beats if beat["covers"] == "entries"]
    if not entries or "дебет" not in entries[0]["narration"].lower() or "кредит" not in entries[0]["narration"].lower():
        raise ValueError("Фраза про проводки должна вслух называть дебет и кредит.")
    return beats


def output_directory(root: Path, requested: str | None, stamp: str) -> Path:
    if requested:
        path = Path(requested)
        return path if path.is_absolute() else root / path
    folder = root / "artifacts" / "books-upd" / stamp
    if folder.exists():
        folder = root / "artifacts" / "books-upd" / f"{stamp}-2"
    return folder


def resolve_voice(lesson: dict, tts_arg: str | None, voice_arg: str | None) -> tuple[str, str]:
    tts_name = tts_arg or lesson.get("tts") or "elevenlabs"
    voice = voice_arg or lesson.get("voice") or LILY_VOICE
    if tts_name in ("auto", "chain"):
        raise RuntimeError(
            "Для свежей записи книг запасной голос отключён: вместо Лили мог бы заговорить другой голос. "
            "Укажите --tts elevenlabs --voice pFZP5JQG7iQjIQuC4Bku."
        )
    return tts_name, voice


def assert_approved_voice(project_root: Path, tts_name: str, voice: str, lesson: dict | None = None) -> None:
    if tts_name != "elevenlabs":
        return
    if voice != LILY_VOICE:
        raise RuntimeError(
            f"Для этого урока нужен голос Лили ({LILY_VOICE}). Сейчас выбран «{voice}»."
        )
    wanted = (lesson or {}).get("tts_model") or LILY_MODEL
    settings = read_dotenv(project_root)
    import os

    model = os.environ.get("ELEVENLABS_MODEL_ID") or settings.get("ELEVENLABS_MODEL_ID") or LILY_MODEL
    if model != wanted:
        raise RuntimeError(
            f"Для Лили нужна модель {wanted}. В настройках указана «{model}». Запись не начата, запрос в сервис не отправлялся."
        )


def preflight_exit_code(report: dict) -> int:
    status = report.get("status")
    if status == "blocked":
        return 2
    if status != "preflight":
        return 1
    return 0


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _read(session, element) -> str:
    if hasattr(session, "read"):
        return str(session.read(element) or "")
    return str(getattr(element, "value", "") or "")


def _check_shown(session, element, beat: dict) -> None:
    target = beat["element"]
    if beat.get("expect_name") and beat["expect_name"].lower().replace("ё", "е") not in element.name.lower().replace("ё", "е"):
        raise TargetError("mismatch", target, f"На экране «{element.name}», а фраза ждёт «{beat['expect_name']}».", beat["id"])
    blob = f"{element.name} {_read(session, element)}"
    if beat.get("expect_contains") and beat["expect_contains"].lower().replace("ё", "е") not in blob.lower().replace("ё", "е"):
        raise TargetError(
            "mismatch",
            target,
            f"В «{target}» не видно «{beat['expect_contains']}». Значение: «{_read(session, element)}». Запись остановлена.",
            beat["id"],
        )
    if beat.get("expect_digits"):
        got = _digits(_read(session, element))
        expected = _digits(beat["expect_digits"])
        if not got.startswith(expected):
            raise TargetError(
                "mismatch",
                target,
                f"Сумма в «{target}» не начинается с {expected}. На экране: «{_read(session, element)}».",
                beat["id"],
            )
    if beat.get("require_filled") and not _read(session, element).strip():
        raise TargetError(
            "empty",
            target,
            f"Поле «{target}» пустое. Значение само не подставляется, чтобы не выбрать неверную запись.",
            beat["id"],
        )


def _find_strict(session, beat: dict, timeout: float):
    names = [beat["element"], *beat.get("aliases", [])]
    last = None
    for index, name in enumerate(names):
        try:
            return session.find_unique(
                name,
                timeout=timeout if index == 0 else min(float(timeout), 1.2),
                role=beat.get("role") or "",
                within=beat.get("within") or "",
            )
        except AmbiguousTarget as error:
            raise TargetError(
                "ambiguous",
                name,
                f"Подпись «{name}» встречается {error.count} раз. Рамка не ставится наугад.",
                beat["id"],
            ) from error
        except LookupError as error:
            last = error
    raise TargetError("missing", beat["element"], f"На экране нет «{beat['element']}».", beat["id"]) from last


def _recording_rect(session, element) -> list[int]:
    left, top, _right, _bottom = session.window_rect()
    rect = [
        int(element.left) - int(left),
        int(element.top) - int(top),
        int(element.right) - int(left),
        int(element.bottom) - int(top),
    ]
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        raise TargetError("missing", element.name, f"У «{element.name}» пустая рамка. Координаты не подставлены.")
    return rect


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _cache_dir(project_root: Path, tts_name: str, voice: str) -> Path:
    safe_voice = re.sub(r"[^0-9A-Za-z._-]+", "_", voice or "voice")
    return project_root / "artifacts" / "tts-cache" / (tts_name or "tts") / safe_voice


def _default_speak(project_root: Path, tts_name: str, voice: str, cache_dir: Path, log):
    def speak(text: str, dest: Path) -> float:
        from pipeline.postprod import wav_duration

        speak_clip(text, dest, tts_name, voice, project_root, log=log, cache_dir=cache_dir)
        return wav_duration(dest)

    return speak


def _remember_speech(speak, cache_dir: Path, tts_name: str, voice: str, text: str, dest: Path) -> tuple[float, bool]:
    cached = speech_cache_file(dest, tts_name, voice, text, cache_dir)
    hit = cached.exists() and cached.stat().st_size > 400
    duration = float(speak(text, dest))
    return duration, hit


def preflight_synced(session, lesson: dict, now: datetime | None = None) -> dict:
    """Ищет подписи. Не нажимает, не вводит текст, не закрывает окна клавишей Esc."""
    beats = beats_from_lesson(lesson)
    session.connect()
    seen = visible_incoming_numbers(session)
    moment = now or datetime.now()
    number = allocate_incoming_number(seen, moment)
    rows = []
    ambiguous = []
    missing = []
    mismatches = []
    for beat in beats:
        row = {
            "step": beat["id"],
            "narration": apply_number(beat["narration"], number),
            "element": beat["element"],
            "kind": beat["kind"],
            "covers": beat["covers"],
            "found": False,
            "ambiguous": False,
            "note": "",
            "performed": "только поиск",
        }
        try:
            element = _find_strict(session, beat, 0.05)
            row["found"] = True
            row["note"] = getattr(element, "note", "") or ""
            if beat["kind"] != "fill":
                try:
                    _check_shown(session, element, beat)
                except TargetError as error:
                    row["found"] = False
                    row["note"] = str(error)
                    mismatches.append(beat["element"])
        except TargetError as error:
            row["found"] = False
            row["ambiguous"] = error.kind == "ambiguous"
            row["note"] = str(error)
            if error.kind == "ambiguous":
                ambiguous.append(beat["element"])
            elif error.kind in ("mismatch", "empty"):
                mismatches.append(beat["element"])
            else:
                missing.append(beat["element"])
        rows.append(row)
    status = "blocked" if ambiguous or mismatches else "preflight"
    return {
        "status": status,
        "mode": "synced-preflight",
        "recorded": False,
        "posted": False,
        "filled": False,
        "document_modified": False,
        "legacy_fallback": False,
        "windows_1c_verified": False,
        "organization": lesson.get("organization"),
        "incoming_number_preview": number,
        "reserved_skipped": list(RESERVED_NUMBERS),
        "seen_on_screen": seen,
        "scan": "visible_window_only",
        "ambiguous": ambiguous,
        "mismatches": mismatches,
        "missing_on_this_screen": missing,
        "steps": rows,
        "note": (
            "Пробный прогон только читает экран. Поля документа появятся после открытия формы, "
            "поэтому их отсутствие на начальной странице ожидаемо. Повторяющаяся подпись останавливает прогон. "
            "База не восстанавливается, старые документы не открываются и не удаляются."
        ),
    }


def _failure(beat, error: TargetError, number: str, seen, changed: bool, posted: bool, rows: list[dict]) -> dict:
    return {
        "status": "blocked" if error.kind == "ambiguous" else "failed",
        "mode": "synced-record",
        "recorded": False,
        "posted": posted,
        "filled": changed,
        "document_modified": changed,
        "legacy_fallback": False,
        "windows_1c_verified": False,
        "incoming_number": number,
        "seen_on_screen": seen,
        "message": str(error) + " Частично заполненный документ не удалялся и не исправлялся.",
        "error": {"kind": error.kind, "target": error.target, "beat": error.beat or (beat or {}).get("id", ""), "message": str(error)},
        "beats": rows,
    }


def perform_synced(
    session,
    lesson: dict,
    out_dir: Path,
    project_root: Path,
    tts_name: str,
    voice: str,
    *,
    clock: Clock | None = None,
    speak=None,
    grab=None,
    now: datetime | None = None,
    timeout: float = 8,
) -> dict:
    """Пишет экран и держит элемент всю фразу. Возвращает отчёт, даже если показ не найден."""
    beats = beats_from_lesson(lesson)
    out_dir.mkdir(parents=True, exist_ok=True)
    clock = clock or Clock(sleep=time.sleep, time_fn=time.time)
    cache = _cache_dir(project_root, tts_name, voice)
    log_holder = {}
    if speak is None:
        from pipeline.tts import TtsLog

        log_holder["log"] = TtsLog()
        speak = _default_speak(project_root, tts_name, voice, cache, log_holder["log"])
    moment = now or datetime.now()
    session.connect()
    if not session.organization_visible(lesson.get("organization") or ""):
        report = {
            "status": "failed",
            "mode": "synced-record",
            "recorded": False,
            "posted": False,
            "filled": False,
            "document_modified": False,
            "legacy_fallback": False,
            "windows_1c_verified": False,
            "message": f"В окне не видно организацию «{lesson.get('organization')}». Запись не начата.",
            "beats": [],
        }
        _write(out_dir / "errors.json", [report])
        return report
    seen = visible_incoming_numbers(session)
    number = allocate_incoming_number(seen, moment)
    voice_dir = out_dir / "voice"
    ready: dict[str, tuple[float, bool, str]] = {}
    for beat in beats:
        if beat["uses_number"]:
            continue
        dest = voice_dir / f"{beat['id']}.wav"
        text = beat["narration"]
        duration, hit = _remember_speech(speak, cache, tts_name, voice, text, dest)
        ready[beat["id"]] = (duration, hit, text)
    grab_error = ""
    if grab is not None:
        grab.start(session.window_rect(), out_dir / "raw-screen.mp4")
    origin = clock.now()
    rows = []
    changed = False
    posted = False
    stopped = None
    try:
        if getattr(session, "cursor", None) is None:
            session.cursor = _cursor_now(session)
        for beat in beats:
            watcher = getattr(session, "watcher", None)
            if watcher is not None:
                watcher.poll()
            if beat["uses_number"] and beat["id"] not in ready:
                dest = voice_dir / f"{beat['id']}.wav"
                text = apply_number(beat["narration"], number)
                if "{{" in text:
                    raise TargetError("missing", beat["element"], "Номер документа не подставлен в фразу.", beat["id"])
                duration, hit = _remember_speech(speak, cache, tts_name, voice, text, dest)
                ready[beat["id"]] = (duration, hit, text)
            duration, hit, text = ready[beat["id"]]
            element = _find_strict(session, beat, timeout)
            if beat["kind"] != "fill":
                _check_shown(session, element, beat)
            rect = _recording_rect(session, element)
            center_x = (element.left + element.right) // 2
            center_y = (element.top + element.bottom) // 2
            glide_cursor(session, center_x, center_y, duration=0.4, steps=10, sleep=clock.sleep)
            if beat["kind"] == "click":
                session.click(element)
                changed = changed or beat["mutates"]
                if beat["mutates"] and "провест" in beat["element"].lower():
                    posted = True
            elif beat["kind"] == "fill":
                value = apply_number(beat["value"], number)
                if value in RESERVED_NUMBERS or "{{" in value:
                    raise TargetError("mismatch", beat["element"], f"В поле нельзя писать «{value}».", beat["id"])
                session.set_text(element, value)
                changed = True
            shown_at = round(clock.now() - origin, 3)
            clock.sleep(duration)
            speech_end = round(clock.now() - origin, 3)
            row = {
                "id": beat["id"],
                "narration": text,
                "subtitle": strip_stress(text),
                "kind": beat["kind"],
                "element": element.name,
                "covers": beat["covers"],
                "rect": rect,
                "rect_source": "uia",
                "shown_at": shown_at,
                "speech_start": shown_at,
                "speech_end": speech_end,
                "drift_seconds": 0.0,
                "wav": str(voice_dir / f"{beat['id']}.wav"),
                "from_cache": hit,
                "mutates": beat["mutates"],
            }
            rows.append(row)
            if beat["rescan_after"]:
                if hasattr(session, "refresh"):
                    session.refresh(0)
                seen = list(dict.fromkeys([*seen, *visible_incoming_numbers(session)]))
                if number in seen:
                    number = allocate_incoming_number(seen, moment)
                    for item in beats:
                        if item["uses_number"]:
                            ready.pop(item["id"], None)
        status = "recorded"
        message = "Фразы показаны в момент речи. Ролик ещё нужно собрать из этой записи."
    except TargetError as error:
        stopped = _failure(None, error, number, seen, changed, posted, rows)
        status = stopped["status"]
        message = stopped["message"]
    except Exception as error:
        from pipeline.ui_session import StopRequested

        if isinstance(error, StopRequested):
            status = "stopped"
            message = str(error)
            stopped = {
                "status": status,
                "mode": "synced-record",
                "recorded": False,
                "posted": posted,
                "filled": changed,
                "document_modified": changed,
                "legacy_fallback": False,
                "windows_1c_verified": False,
                "incoming_number": number,
                "message": message,
                "beats": rows,
            }
        else:
            raise
    finally:
        if grab is not None:
            try:
                grab_error = grab.stop() or ""
            except Exception as error:
                grab_error = str(error)
    report = stopped or {
        "status": status,
        "mode": "synced-record",
        "recorded": False,
        "posted": posted,
        "filled": changed,
        "document_modified": changed,
        "legacy_fallback": False,
        "windows_1c_verified": False,
        "incoming_number": number,
        "reserved_skipped": list(RESERVED_NUMBERS),
        "seen_on_screen": seen,
        "scan": "visible_window_only",
        "message": message,
        "beats": rows,
        "grab_error": grab_error,
        "cache_dir": str(cache),
    }
    _write(out_dir / "audit.json", report)
    _write(out_dir / "errors.json", [] if report["status"] == "recorded" else [report.get("error") or {"message": report.get("message")}])
    _write(
        out_dir / "coverage.json",
        [
            {
                "phrase": row["narration"],
                "shown": row["element"],
                "covers": row["covers"],
                "rect": row["rect"],
                "rect_source": row["rect_source"],
                "speech": [row["speech_start"], row["speech_end"]],
            }
            for row in rows
        ],
    )
    drifts = [abs(float(row["speech_start"]) - float(row["shown_at"])) for row in rows]
    _write(
        out_dir / "timing-report.json",
        {
            "synced": report["status"] == "recorded" and all(item <= 0.05 for item in drifts),
            "max_drift_seconds": round(max(drifts) if drifts else 0.0, 3),
            "beats": [
                {
                    "id": row["id"],
                    "shown_at": row["shown_at"],
                    "speech_start": row["speech_start"],
                    "speech_end": row["speech_end"],
                    "drift_seconds": row["drift_seconds"],
                    "from_cache": row["from_cache"],
                }
                for row in rows
            ],
        },
    )
    if log_holder.get("log") is not None:
        log_holder["log"].write(out_dir / "tts-log.json")
    return report


def _draw_filters(beats: list[dict]) -> tuple[str, str]:
    src = "[0:v]"
    parts = []
    for index, beat in enumerate(beats):
        left, top, right, bottom = beat["rect"]
        width = right - left
        height = bottom - top
        dst = f"[v{index}]"
        parts.append(
            f"{src}drawbox=x={left}:y={top}:w={width}:h={height}:color=0xE08900:t=3:"
            f"enable='between(t,{float(beat['speech_start']):.3f},{float(beat['speech_end']):.3f})'{dst}"
        )
        src = dst
    return ",".join(parts), src


def assemble_synced(raw: Path, beats: list[dict], out_dir: Path, run_ffmpeg) -> dict:
    """Накладывает голос на ту же секунду, где элемент уже был на экране. Кадры не переставляет."""
    problems = []
    for beat in beats:
        drift = abs(float(beat["speech_start"]) - float(beat["shown_at"]))
        rect = beat.get("rect") or []
        if drift > 0.05:
            problems.append(f"{beat['id']}: речь смещена на {drift:.3f} с")
        if len(rect) != 4 or rect[2] <= rect[0] or rect[3] <= rect[1] or beat.get("rect_source") != "uia":
            problems.append(f"{beat['id']}: нет точной рамки элемента «{beat.get('element')}»")
        if not beat.get("wav"):
            problems.append(f"{beat['id']}: нет звука фразы")
    covered = {beat.get("covers") for beat in beats}
    for name in REQUIRED_COVERS:
        if name not in covered:
            problems.append(f"нет показа «{name}»")
    if problems:
        raise SyncAssemblyError("Сборка остановлена. " + "; ".join(problems))
    video_filters, last_video = _draw_filters(beats)
    args = ["-i", str(raw)]
    audio = []
    for index, beat in enumerate(beats):
        args.extend(["-i", str(beat["wav"])])
        delay = max(0, int(round(float(beat["speech_start"]) * 1000)))
        audio.append(f"[{index + 1}:a]adelay={delay}|{delay},apad[a{index}]")
    mix = "".join(f"[a{index}]" for index in range(len(beats)))
    audio.append(f"{mix}amix=inputs={len(beats)}:normalize=0:duration=longest[a]")
    dest = out_dir / "synced-body.mp4"
    args.extend(
        [
            "-filter_complex",
            video_filters + ";" + ";".join(audio),
            "-map",
            last_video,
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-shortest",
            str(dest),
        ]
    )
    try:
        run_ffmpeg(args)
    except Exception as error:
        raise SyncAssemblyError(
            "Видео не собрано. Запасной монтаж без синхронных рамок не запускался. " + str(error)[:300]
        ) from error
    cues = []
    for beat in beats:
        cues.append((float(beat["speech_start"]), float(beat["speech_end"]), beat.get("subtitle") or strip_stress(beat["narration"])))
    write_srt(cues, out_dir / "subtitles.srt")
    report = {
        "mode": "synced",
        "video": str(dest),
        "subtitles": str(out_dir / "subtitles.srt"),
        "legacy_fallback": False,
        "windows_1c_verified": False,
        "speech_tempo": 1.0,
        "beats": len(beats),
    }
    _write(out_dir / "video-result.json", report)
    return report


def attach_cards(lesson: dict, body: Path, out_dir: Path, speak, card_clip, concat) -> float:
    """Заставка и концовка до и после уже синхронной записи. Внутри записи кадры не двигаются."""
    from pipeline.postprod import wav_duration

    intro_wav = out_dir / "voice" / "intro.wav"
    outro_wav = out_dir / "voice" / "outro.wav"
    speak(lesson.get("intro_narration") or "", intro_wav)
    speak(lesson.get("outro_narration") or "", outro_wav)
    intro = out_dir / "intro.mp4"
    outro = out_dir / "outro.mp4"
    card_clip(lesson.get("card_title") or lesson.get("title") or "", lesson.get("configuration") or "", "Учебная база. Отправки в ФНС нет.", intro_wav, intro)
    card_clip(lesson.get("card_title") or lesson.get("title") or "", lesson.get("configuration") or "", "Конец урока", outro_wav, outro)
    final = out_dir / "lesson.mp4"
    concat([intro, body, outro], final, with_audio=True)
    intro_length = wav_duration(intro_wav)
    raw_srt = (out_dir / "subtitles.srt").read_text(encoding="utf-8")
    # Субтитры тела уже без ударений. Сдвигаем их на длину заставки и добавляем вступление.
    body_cues = []
    blocks = [block for block in raw_srt.split("\n\n") if block.strip()]
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start_text, end_text = lines[1].split("-->")
        body_cues.append((start_text.strip(), end_text.strip(), "\n".join(lines[2:])))
    def _seconds(stamp: str) -> float:
        clock, millis = stamp.split(",")
        hours, minutes, seconds = [int(part) for part in clock.split(":")]
        return hours * 3600 + minutes * 60 + seconds + int(millis) / 1000

    cues = [(0.0, intro_length, strip_stress(lesson.get("intro_narration") or ""))]
    for start_text, end_text, text in body_cues:
        cues.append((_seconds(start_text) + intro_length, _seconds(end_text) + intro_length, text))
    outro_at = cues[-1][1] if cues else intro_length
    cues.append((outro_at, outro_at + wav_duration(outro_wav), strip_stress(lesson.get("outro_narration") or "")))
    write_srt(cues, out_dir / "subtitles.srt")
    if not final.exists():
        raise SyncAssemblyError("Итоговый файл не собран.")
    return intro_length


def run_preflight(lesson_path: Path, out_dir: Path, session=None, now: datetime | None = None) -> dict:
    from pipeline.lesson_yaml import load_lesson
    from pipeline.ui_session import EscWatcher, PywinautoSession

    out_dir.mkdir(parents=True, exist_ok=True)
    lesson = load_lesson(lesson_path)
    active = session
    try:
        active = session or PywinautoSession(EscWatcher())
        report = preflight_synced(active, lesson, now=now)
    except Exception as error:
        report = {
            "status": "failed",
            "mode": "synced-preflight",
            "recorded": False,
            "posted": False,
            "filled": False,
            "document_modified": False,
            "legacy_fallback": False,
            "windows_1c_verified": False,
            "message": str(error),
            "steps": [],
        }
    finally:
        _write(out_dir / "dry-run.json", report)
        _write(out_dir / "preflight.json", report)
    return report


def run_synced(
    lesson: dict,
    out_dir: Path,
    project_root: Path,
    tts_name: str,
    voice: str,
    session,
    grab=None,
    speak=None,
    clock: Clock | None = None,
    now: datetime | None = None,
    assemble=True,
) -> dict:
    assert_approved_voice(project_root, tts_name, voice, lesson)
    if grab is None:
        from pipeline.live import ScreenGrab

        grab = ScreenGrab()
    report = perform_synced(
        session,
        lesson,
        out_dir,
        project_root,
        tts_name,
        voice,
        clock=clock,
        speak=speak,
        grab=grab,
        now=now,
    )
    if report.get("status") != "recorded":
        report["recorded"] = False
        report["legacy_fallback"] = False
        _write(out_dir / "audit.json", report)
        return report
    raw = out_dir / "raw-screen.mp4"
    if not raw.exists() or raw.stat().st_size < 1000:
        report["status"] = "failed"
        report["recorded"] = False
        report["message"] = "Запись экрана не сохранилась. Ролик из старых кадров не подставлялся."
        _write(out_dir / "errors.json", [{"message": report["message"]}])
        _write(out_dir / "audit.json", report)
        return report

    def _ffmpeg(args: list[str]) -> None:
        from pipeline.postprod import run_ffmpeg

        run_ffmpeg(args)

    if assemble:
        try:
            video = assemble_synced(raw, report["beats"], out_dir, _ffmpeg)
            from pipeline.live import _card_clip
            from pipeline.postprod import _concat

            cache = Path(report["cache_dir"])
            card_speak = speak or _default_speak(project_root, tts_name, voice, cache, None)
            attach_cards(lesson, Path(video["video"]), out_dir, card_speak, _card_clip, _concat)
        except SyncAssemblyError as error:
            report["status"] = "failed"
            report["recorded"] = False
            report["legacy_fallback"] = False
            report["message"] = str(error)
            _write(out_dir / "errors.json", [{"message": str(error)}])
            _write(out_dir / "audit.json", report)
            return report
        except Exception as error:
            report["status"] = "failed"
            report["recorded"] = False
            report["legacy_fallback"] = False
            report["message"] = (
                "Заставка или склейка не собралась. Запись экрана не заменялась старым монтажом. " + str(error)[:300]
            )
            _write(out_dir / "errors.json", [{"message": report["message"]}])
            _write(out_dir / "audit.json", report)
            return report
        report["recorded"] = True
        report["video"] = str(out_dir / "lesson.mp4")
        report["message"] = "Готово: каждая фраза лежит на том кадре, где виден её элемент."
    else:
        report["recorded"] = True
        report["video"] = ""
        report["message"] = "Проход записан. Сборка ролика в этой проверке не запускалась."
    report["legacy_fallback"] = False
    report["windows_1c_verified"] = False
    _write(out_dir / "audit.json", report)
    return report
