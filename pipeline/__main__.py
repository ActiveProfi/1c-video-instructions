"""Команда: python -m pipeline run"""

from __future__ import annotations

import argparse
from pathlib import Path

from pipeline.live import run_dry, run_record
from pipeline.run import execute, load_request, slugify
from pipeline.tnved import assess_tnved, read_smartphone_tnved, write_report
from pipeline.ui_session import PywinautoSession, EscWatcher


def _require_training(args) -> bool:
    if args.confirm_training_copy:
        return True
    print(
        "Добавьте --confirm-training-copy только если открыта учебная копия «Альфа ООО», не рабочая база.",
        flush=True,
    )
    return False


def _live(root: Path, args) -> int:
    if not _require_training(args):
        return 1
    if not args.lesson:
        print("Укажите урок, например: --lesson lessons/lesson1.yaml", flush=True)
        return 1
    lesson = Path(args.lesson)
    if not lesson.is_absolute():
        lesson = root / lesson
    out = Path(args.out) if args.out else root / "artifacts" / "lessons" / lesson.stem
    if not out.is_absolute():
        out = root / out
    if args.dry_run:
        report = run_dry(lesson, out)
        print(
            f"Пробный прогон. Найдено: {len(report.get('found') or [])}. "
            f"Не найдено: {len(report.get('missing') or [])}. Запись не велась, документы не проводились.",
            flush=True,
        )
        if not report.get("organization_seen", True):
            print("В окне не видно организацию «Альфа ООО».", flush=True)
        for name in report.get("missing") or []:
            print(f"Нет на экране: {name}", flush=True)
        print(f"Отчёт: {out / 'dry-run.json'}", flush=True)
        return 1 if report.get("status") == "stopped" else 0
    result = run_record(
        lesson,
        out,
        root,
        args.tts or "auto",
        args.voice or "ru-RU-DmitryNeural",
    )
    if result.get("status") != "recorded":
        print(result.get("message") or "Запись не завершена.", flush=True)
        return 1
    print(f"Готово: {result['video']}", flush=True)
    return 0


def _tnved(root: Path, args) -> int:
    if not _require_training(args):
        return 1
    from pipeline.tnved import NOT_FOUND_MESSAGE

    out = Path(args.out) if args.out else root / "artifacts" / "tnved-report.json"
    if not out.is_absolute():
        out = root / out
    report = {
        "found": False,
        "code": None,
        "expected": "8517 13 000 0",
        "ok": False,
        "warning": None,
        "message": "",
    }
    try:
        session = PywinautoSession(EscWatcher())
        session.connect()
        code = read_smartphone_tnved(session)
        if not code:
            report["message"] = NOT_FOUND_MESSAGE
            print(NOT_FOUND_MESSAGE, flush=True)
            print(f"Отчёт: {out}", flush=True)
            return 0
        checked = assess_tnved(code)
        report.update(checked)
        report["found"] = True
        report["message"] = checked["warning"] or f"Код совпадает с {checked['expected']}."
        print(f"Код ТН ВЭД в карточке: {code}", flush=True)
        if checked["warning"]:
            print(checked["warning"], flush=True)
            return 2
        print(report["message"], flush=True)
        return 0
    except Exception as error:
        report["message"] = str(error)
        print(f"Остановка: {error}", flush=True)
        return 1
    finally:
        write_report(out, report)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Собрать видеоурок по 1С: сценарий, шаги, запись и монтаж."
    )
    parser.add_argument("command", nargs="?", default="run", choices=["run", "live", "tnved"])
    parser.add_argument("--lesson", default=None, help="Файл урока YAML, например lessons/lesson1.yaml")
    parser.add_argument("--request", default=None, help="JSON с темой и конфигурацией")
    parser.add_argument("--topic", default=None, help="Тема урока, если её нет в запросе")
    parser.add_argument("--configuration", default=None, help="Название конфигурации 1С")
    parser.add_argument("--script", default=None, help="Готовый сценарий JSON. Если пусто — сценарий пишется сам")
    parser.add_argument("--ui-map", default=None, help="Карта подписей и координат интерфейса")
    parser.add_argument("--materials", action="append", default=None, help="Файл с вашими материалами. Можно указать несколько раз")
    parser.add_argument("--driver", choices=["mock", "windows"], default=None)
    parser.add_argument("--tts", default=None, help="offline, edge, espeak, elevenlabs или auto")
    parser.add_argument("--voice", default=None, help="Имя голоса для edge или ElevenLabs")
    parser.add_argument("--out", default=None, help="Папка результата")
    parser.add_argument(
        "--confirm-training-copy",
        action="store_true",
        help="Подтверждение, что открыта учебная копия базы, не рабочая",
    )
    parser.add_argument("--dry-run", action="store_true", help="Найти элементы 1С и снять кадры, ничего не нажимая")
    parser.add_argument("--process-id", type=int, default=0, help="PID окна 1С, если их несколько")
    args = parser.parse_args(argv)

    root = project_root()
    try:
        if args.command == "live":
            return _live(root, args)
        if args.command == "tnved":
            return _tnved(root, args)
    except Exception as error:
        print(f"Остановка: {error}", flush=True)
        return 1
    request = load_request(root, args.request)
    if args.topic:
        request["topic"] = args.topic
    if args.configuration:
        request["configuration"] = args.configuration
    if args.script is not None:
        request["script"] = args.script or None
    if args.ui_map is not None:
        request["ui_map"] = args.ui_map or None
    if args.materials:
        request["materials"] = list(args.materials)
    driver = args.driver or request.get("driver") or "mock"
    tts_name = args.tts or request.get("tts") or "offline"
    voice = args.voice or request.get("voice") or "ru-RU-DmitryNeural"
    if args.out:
        out_dir = Path(args.out)
        if not out_dir.is_absolute():
            out_dir = root / out_dir
    else:
        out_dir = root / "artifacts" / "lessons" / slugify(request.get("topic") or "urok")
    try:
        execute(
            root,
            request,
            out_dir,
            driver,
            tts_name,
            voice,
            args.confirm_training_copy,
            args.dry_run,
            args.process_id,
        )
    except Exception as error:
        print(f"Остановка: {error}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
