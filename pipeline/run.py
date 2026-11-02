"""Сборка урока от темы до файла видео."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pipeline.drivers import MockDriver, WindowsUiDriver
from pipeline.planner import build_plan, complete_ui_map
from pipeline.postprod import assemble
from pipeline.research import load_json, load_research
from pipeline.scriptgen import build_script


def slugify(text: str) -> str:
    lowered = text.lower().replace("ё", "е")
    slug = re.sub(r"[^a-zа-я0-9]+", "-", lowered).strip("-")
    return (slug or "urok")[:60]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_request(project_root: Path, path: str | None) -> dict:
    if not path:
        path = "fixtures/vat-usn/request.json"
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = project_root / file_path
    if not file_path.exists():
        raise FileNotFoundError(f"Не найден файл запроса: {file_path}")
    return load_json(file_path)


def load_ui_map(project_root: Path, request: dict) -> dict | None:
    raw = request.get("ui_map")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = project_root / path
    if not path.exists():
        raise FileNotFoundError(
            f"Не найдена карта интерфейса: {path}. "
            "Для макета оставьте fixtures/vat-usn/ui-map.json."
        )
    return load_json(path)


def execute(
    project_root: Path,
    request: dict,
    out_dir: Path,
    driver_name: str,
    tts_name: str,
    voice: str,
    confirm: bool,
    dry_run: bool,
    process_id: int = 0,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    print("Шаг 1 из 4. Собираю сценарий…", flush=True)
    research = load_research(project_root, request)
    script = build_script(project_root, request, research)
    write_json(out_dir / "script.json", script)
    print("Шаг 2 из 4. Раскладываю сценарий на действия в интерфейсе…", flush=True)
    ui_map = load_ui_map(project_root, request) or {}
    ui_map, generated = complete_ui_map(ui_map, script)
    plan = build_plan(script, ui_map, generated)
    write_json(out_dir / "plan.json", plan)
    write_json(out_dir / "ui-map.used.json", ui_map)
    print("Шаг 3 из 4. Выполняю шаги и сохраняю кадры…", flush=True)
    if driver_name == "mock":
        journal = MockDriver().run(plan, ui_map, out_dir)
    elif driver_name == "windows":
        journal = WindowsUiDriver(project_root, confirm, dry_run, process_id).run(plan, ui_map, out_dir)
    else:
        raise RuntimeError("Исполнитель должен быть mock или windows.")
    print("Шаг 4 из 4. Озвучка, субтитры, рамки и сборка видео…", flush=True)
    result = assemble(plan, journal, out_dir, tts_name, voice, project_root)
    write_json(out_dir / "video-result.json", result)
    print(f"Готово: {result['video']}", flush=True)
    if result.get("tts_warning"):
        print(result["tts_warning"], flush=True)
    if result.get("needs_review"):
        print("Ролик помечен для проверки: " + (result.get("disclaimer") or "сверьте шаги с учебной базой."), flush=True)
    return result
