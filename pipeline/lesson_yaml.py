"""Сценарии уроков: текст диктора и пояснение «почему так» сохраняются."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from pipeline.planner import UnsafeStep

ARROW = re.compile(r"\s*(?:→|->)\s*")
MUTATING_PARTS = ("создать", "провести", "записать", "заполнить", "распредел", "сохранить")


def norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е").strip()


def is_forbidden_name(name: str) -> bool:
    """Запрещена именно кнопка отправки, выгрузки или удаления, а не слово «отправка» в названии раздела."""
    value = norm(name).strip("«»() ")
    verbs = ("отправить", "отправка", "выгрузить", "удалить", "пометить на удаление", "очистить")
    if value in verbs:
        return True
    return value.startswith("отправить") or value.startswith("выгрузить") or value.startswith("удалить")


def is_mutating_name(name: str) -> bool:
    value = norm(name)
    if is_forbidden_name(value):
        return True
    return any(part in value for part in MUTATING_PARTS)


def _clean_menu_part(part: str) -> str:
    part = re.split(r"\(или\b| или ", part, maxsplit=1)[0].strip(" ()")
    quoted = re.findall(r"«([^»]+)»", part)
    lowered = norm(part)
    if lowered.startswith("закладка") and quoted:
        return quoted[0]
    if quoted and len(part) < 80 and part.startswith("«"):
        return quoted[0]
    return part.strip()


def menu_clicks(menu_path: str) -> list[str]:
    clicks = []
    for part in ARROW.split(menu_path or ""):
        part = _clean_menu_part(part)
        if not part:
            continue
        lowered = norm(part)
        if lowered.startswith("тот же") or lowered.startswith("там же"):
            continue
        if part.startswith("≡") or "главное меню" in lowered:
            clicks.append("Главное меню")
            continue
        clicks.append(part)
    return clicks


def ui_targets(ui_element: str) -> list[str]:
    quoted = re.findall(r"«([^»]+)»", ui_element or "")
    if quoted:
        return [item.strip() for item in quoted if item.strip()]
    return [item.strip() for item in re.split(r";", ui_element or "") if item.strip()]


def show_only_text(*parts: str) -> bool:
    blob = norm(" ".join(parts))
    return any(
        token in blob
        for token in ("не нажима", "не отправ", "только показать", "ничего не отправ", "не отправля")
    )


def from_source(raw: dict) -> dict:
    """Исходный YAML урока → схема пайплайна. Тексты не сокращаются."""
    show_notes = " ".join(raw.get("recording_rules") or [])
    scenes = []
    for step in raw["steps"]:
        action_text = step.get("action") or ""
        narration = step.get("narration") or ""
        highlight = step.get("highlight") or ""
        legal_note = step.get("legal_note") or ""
        only_show = show_only_text(action_text, narration, highlight, show_notes if "не нажима" in norm(highlight) else "")
        # Пояснение в шаге важнее общего правила: «только показать» относится к отправке.
        only_show = show_only_text(action_text, narration, highlight)
        clicks = menu_clicks(step.get("menu_path") or "")
        scenes.append(
            {
                "id": str(step.get("step")),
                "title": (step.get("menu_path") or f"Шаг {step.get('step')}")[:180],
                "narration": narration,
                "legal_note": legal_note,
                "menu_path": step.get("menu_path") or "",
                "ui_element": step.get("ui_element") or "",
                "action_text": action_text,
                "value_text": step.get("value") or "",
                "highlight": highlight,
                "show_only": only_show,
                "clicks": clicks,
                "targets": ui_targets(step.get("ui_element") or ""),
                "fields": list(step.get("fields") or []),
            }
        )
    goal = raw.get("goal") or ""
    software = raw.get("software") or "1С:Бухгалтерия предприятия, ред. 3.0"
    organization = raw.get("organization") or "Альфа ООО"
    return {
        "schema_version": 1,
        "lesson": raw.get("lesson"),
        "title": raw.get("title_card") or raw.get("title") or f"Урок {raw.get('lesson')}",
        "card_title": raw.get("title_card") or raw.get("title") or "",
        "configuration": "Бухгалтерия предприятия, редакция 3.0",
        "interface": "Такси",
        "organization": organization.split("(")[0].strip(),
        "software": software,
        "goal": goal,
        "intro_narration": (
            f"{goal} Программа — {software}. Организация — {organization}."
        ),
        "outro_narration": raw.get("outro") or "",
        "recording_rules": list(raw.get("recording_rules") or []),
        "training_data": list(raw.get("training_data") or []),
        "summary": list(raw.get("summary") or []),
        "open_questions": list(raw.get("open_questions") or []),
        "review_corrections_applied": list(raw.get("review_corrections_applied") or []),
        "scenes": scenes,
    }


def load_lesson(path: Path) -> dict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError(f"В файле урока нет данных: {path}")
    if raw.get("schema_version") == 1 and raw.get("scenes"):
        return raw
    if raw.get("steps"):
        return from_source(raw)
    raise ValueError(f"Неизвестный формат урока: {path}")


def action_plan(scene: dict) -> list[dict]:
    """Что делать на экране. Удаление и отправка в этом списке не нажимаются."""
    actions = []
    show_only = bool(scene.get("show_only"))
    action_text = norm(scene.get("action_text") or "")

    def add(kind: str, name: str, value: str | None = None) -> None:
        if is_forbidden_name(name):
            if show_only or kind == "show":
                actions.append({"kind": "show", "name": name, "value": None, "scene_id": scene["id"]})
                return
            raise UnsafeStep(
                f"Шаг {scene['id']}: «{name}» похоже на отправку, выгрузку или удаление. "
                "Такие кнопки учебный проход не нажимает."
            )
        if show_only and is_mutating_name(name):
            actions.append({"kind": "show", "name": name, "value": None, "scene_id": scene["id"]})
            return
        actions.append({"kind": kind, "name": name, "value": value, "scene_id": scene["id"]})

    for name in scene.get("clicks") or []:
        if is_mutating_name(name):
            add("mutate", name)
        else:
            add("navigate", name)
    if not show_only and "создать" in action_text and not any(norm(item["name"]) == "создать" for item in actions):
        add("mutate", "Создать")
    if not show_only and "провести" in action_text and not any("провест" in norm(item["name"]) for item in actions):
        add("mutate", "Провести")
    for name in scene.get("targets") or []:
        if any(norm(item["name"]) == norm(name) for item in actions):
            continue
        add("show", name)
    for field in scene.get("fields") or []:
        add("fill", field.get("name") or "", field.get("value"))
    if not actions:
        raise UnsafeStep(f"У шага {scene['id']} нет действий на экране.")
    return actions
