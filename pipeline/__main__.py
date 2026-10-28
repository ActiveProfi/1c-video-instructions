"""Команда: python -m pipeline run"""

from __future__ import annotations

import argparse
from pathlib import Path

from pipeline.run import execute, load_request, slugify


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Собрать видеоурок по 1С: сценарий, шаги, запись и монтаж."
    )
    parser.add_argument("command", nargs="?", default="run", choices=["run"])
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
