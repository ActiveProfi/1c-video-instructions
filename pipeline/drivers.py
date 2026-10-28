"""Исполнители шагов: макет для проверки и локальный проход по живой 1С."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.frames import apply_compute, render_screen
from pipeline.proc import run_captured, text_of


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _seed_values(ui_map: dict) -> dict:
    values = {}
    for screen in (ui_map.get("screens") or {}).values():
        for key, element in (screen.get("elements") or {}).items():
            if element.get("value") not in (None, ""):
                values.setdefault(key, element["value"])
            if element.get("type") == "check" and element.get("checked"):
                values.setdefault(key, "1")
    return values


class MockDriver:
    """Рисует кадры по карте интерфейса. Базу 1С не открывает."""

    name = "mock"

    def run(self, plan: dict, ui_map: dict, out_dir: Path) -> dict:
        frames = out_dir / "frames"
        frames.mkdir(parents=True, exist_ok=True)
        values = _seed_values(ui_map)
        events = []
        for step in plan["steps"]:
            if step["action"] == "set_text" and step.get("value") is not None:
                values[step["target"]] = step["value"]
            if step.get("sets_value") is not None:
                values[step["target"]] = step["sets_value"]
            apply_compute(values, ui_map)
            relative = f"frames/{step['id']}.png"
            render_screen(ui_map, step["screen"], values, out_dir / relative, mock_banner=True)
            events.append(
                {
                    "step_id": step["id"],
                    "scene_id": step["scene_id"],
                    "action": step["action"],
                    "target": step["target"],
                    "automation_name": step["automation_name"],
                    "caption": step["caption"],
                    "bounds": step["bounds"],
                    "bounds_space": "frame",
                    "frame": relative,
                }
            )
        journal = {
            "driver": self.name,
            "status": "passed",
            "mocked": True,
            "dry_run": False,
            "training_copy": False,
            "raw_screen": None,
            "events": events,
        }
        _write_json(out_dir / "journal.json", journal)
        return journal


def windows_argv(project_root: Path, out_dir: Path, process_id: int, dry_run: bool) -> list[str]:
    script = project_root / "tools" / "Run-LessonPlan.ps1"
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-PlanPath",
        str(out_dir / "plan.json"),
        "-OutputDir",
        str(out_dir),
        "-ConfirmTrainingCopy",
    ]
    if dry_run:
        command.append("-DryRun")
    if process_id:
        command.extend(["-TargetProcessId", str(process_id)])
    return command


class WindowsUiDriver:
    """Запускает PowerShell в интерактивном сеансе Windows, где открыта учебная 1С."""

    name = "windows"

    def __init__(self, project_root: Path, confirm: bool, dry_run: bool = False, process_id: int = 0):
        self.project_root = project_root
        self.confirm = confirm
        self.dry_run = dry_run
        self.process_id = process_id

    def run(self, plan: dict, ui_map: dict, out_dir: Path) -> dict:
        del plan, ui_map
        if not self.confirm:
            raise RuntimeError(
                "Запись в живой базе выключена. Добавьте --confirm-training-copy "
                "только если сейчас открыта учебная копия, не рабочая база."
            )
        script = self.project_root / "tools" / "Run-LessonPlan.ps1"
        if not script.exists():
            raise RuntimeError(f"Не найден локальный исполнитель: {script}")
        completed = run_captured(windows_argv(self.project_root, out_dir, self.process_id, self.dry_run))
        journal_path = out_dir / "journal.json"
        detail = ""
        if journal_path.exists():
            try:
                saved = json.loads(journal_path.read_text(encoding="utf-8-sig"))
                detail = saved.get("error") or ""
            except json.JSONDecodeError:
                detail = ""
        if completed.returncode != 0:
            tail = (text_of(completed.stdout) + "\n" + text_of(completed.stderr)).strip()[-1500:]
            raise RuntimeError(
                "Запись в 1С остановилась. "
                + (detail or "Смотрите текст ниже.")
                + (("\n" + tail) if tail else "")
            )
        if not journal_path.exists():
            raise RuntimeError("Исполнитель 1С завершился без файла journal.json.")
        return json.loads(journal_path.read_text(encoding="utf-8-sig"))
