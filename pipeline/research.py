"""Источники урока.

Полный текст портала 1С:ИТС в проект не копируется: он доступен по подписке.
Здесь карточка со ссылками и короткими шагами, которые можно сверить с учебной
базой и со справкой F1. Свои материалы пользователь кладёт рядом и передаёт
в запросе — они добавляются к карточке, но не заменяют проверку в базе.
"""

from __future__ import annotations

import json
from pathlib import Path


def project_root_from(here: Path) -> Path:
    return here.resolve().parents[1]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _normalize(text: str) -> str:
    return text.lower().replace("ё", "е")


def lesson_matches(topic: str, lesson: dict) -> bool:
    text = _normalize(topic)
    groups = lesson.get("match") or {}
    if not groups:
        return False
    for variants in groups.values():
        if not any(_normalize(item) in text for item in variants):
            return False
    return True


def read_materials(project_root: Path, paths: list) -> str:
    chunks = []
    for raw in paths or []:
        path = Path(raw)
        if not path.is_absolute():
            path = project_root / path
        if not path.exists():
            raise FileNotFoundError(f"Не найден файл материалов: {path}")
        text = path.read_text(encoding="utf-8-sig").strip()
        if text:
            chunks.append(text[:4000])
    return "\n\n".join(chunks)


def load_research(project_root: Path, request: dict) -> dict:
    topic = (request.get("topic") or "").strip()
    if not topic:
        raise ValueError("Укажите тему урока.")
    materials = read_materials(project_root, request.get("materials") or [])
    lesson_path = project_root / "fixtures" / "vat-usn" / "lesson.json"
    lesson = load_json(lesson_path)
    matched = lesson_matches(topic, lesson)
    return {
        "topic": topic,
        "configuration": request.get("configuration") or lesson["configuration_default"],
        "lesson": lesson if matched else None,
        "materials_text": materials,
        "matched_fixture": matched,
    }
