"""План шагов: каждый шаг привязан к элементу карты интерфейса."""

from __future__ import annotations

import copy

FORBIDDEN = (
    "отправить",
    "фнс",
    "клиент-банк",
    "клиент банк",
    "выгрузить платеж",
    "подписать и отправить",
)


class UnsafeStep(RuntimeError):
    pass


def _empty_map() -> dict:
    return {
        "canvas": {"width": 1280, "height": 720},
        "window_title": "1С",
        "screens": {},
        "compute": [],
    }


def find_element(ui_map: dict, target: str):
    for screen_id, screen in (ui_map.get("screens") or {}).items():
        elements = screen.get("elements") or {}
        if target in elements:
            return screen_id, elements[target]
    return None, None


def complete_ui_map(ui_map: dict | None, script: dict) -> tuple[dict, bool]:
    """Если подписи ещё не описаны, рисуем заглушку, чтобы черновик всё равно собрался."""
    ui_map = copy.deepcopy(ui_map) if ui_map else _empty_map()
    ui_map.setdefault("canvas", {"width": 1280, "height": 720})
    ui_map.setdefault("screens", {})
    ui_map.setdefault("compute", [])
    missing = []
    for scene in script["scenes"]:
        for step in scene["steps"]:
            if find_element(ui_map, step["target"])[0] is None:
                missing.append(step)
    if not missing:
        return ui_map, False
    screen = ui_map["screens"].setdefault(
        "generated",
        {"title": "Черновик: подписи не проверены", "elements": {}},
    )
    y = 90
    for step in missing:
        if step["target"] in screen["elements"]:
            continue
        screen["elements"][step["target"]] = {
            "name": step.get("caption") or step["target"],
            "type": "button",
            "control_type": "button",
            "bounds": {"x": 250, "y": y, "width": 680, "height": 44},
            "generated": True,
        }
        y += 58
    return ui_map, True


def _assert_safe(text: str) -> None:
    lowered = (text or "").lower().replace("ё", "е")
    for word in FORBIDDEN:
        if word in lowered:
            raise UnsafeStep(
                f"Шаг «{text}» похож на отправку отчёта или платёж. "
                "Учебный ролик такие действия не выполняет."
            )


def build_plan(script: dict, ui_map: dict, ui_generated: bool) -> dict:
    steps = []
    scenes = []
    for scene in script["scenes"]:
        step_ids = []
        for index, step in enumerate(scene["steps"], start=1):
            screen_id, element = find_element(ui_map, step["target"])
            if element is None:
                raise KeyError(f"В карте интерфейса нет элемента «{step['target']}».")
            automation_name = element.get("name") or step["target"]
            _assert_safe(" ".join(
                filter(None, [automation_name, step.get("caption"), step.get("value"), step.get("expected")])
            ))
            step_id = step.get("id") or f"{scene['id']}_{index}"
            step_ids.append(step_id)
            steps.append(
                {
                    "id": step_id,
                    "scene_id": scene["id"],
                    "action": step.get("action") or "focus",
                    "target": step["target"],
                    "automation_name": automation_name,
                    "control_type": element.get("control_type") or element.get("type") or "",
                    "screen": screen_id,
                    "bounds": element["bounds"],
                    "caption": step.get("caption") or automation_name,
                    "value": step.get("value"),
                    "expected": step.get("expected"),
                    "sets_value": step.get("sets_value"),
                }
            )
        scenes.append(
            {
                "id": scene["id"],
                "title": scene["title"],
                "narration": scene["narration"],
                "step_ids": step_ids,
            }
        )
    title = script["title"]
    card = title if title.startswith("Урок:") else f"Урок: {title}"
    return {
        "title": title,
        "card_title": card,
        "configuration": script["configuration"],
        "goal": script.get("goal") or "",
        "intro_narration": script["intro_narration"],
        "outro_narration": script["outro_narration"],
        "needs_review": bool(script.get("needs_review", True) or ui_generated),
        "disclaimer": script.get("disclaimer") or "",
        "sources": script.get("sources") or [],
        "generated": bool(script.get("generated")),
        "source_kind": script.get("source_kind"),
        "ui_map_generated": ui_generated,
        "scenes": scenes,
        "steps": steps,
        "safety": {
            "training_copy_only": True,
            "forbidden": list(FORBIDDEN),
        },
    }
