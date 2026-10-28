"""Сценарий урока: из карточки источников или из файла пользователя."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.research import load_json
from pipeline.text_ru import speak_account, speak_configuration, speak_int, speak_rubles


def _fill(text: str, values: dict) -> str:
    return text.format(**values)


def _example_values(lesson: dict, configuration: str) -> dict:
    example = lesson["example"]
    return {
        "supplier": example["supplier"],
        "incoming_number": example["incoming_number"],
        "incoming_date": example["incoming_date"],
        "incoming_date_spoken": example["incoming_date_spoken"],
        "item": example["item"],
        "quantity": str(example["quantity"]),
        "price": str(example["price"]),
        "quantity_words": speak_int(example["quantity"]),
        "price_words": speak_rubles(example["price"]),
        "total_words": speak_rubles(example["total"]),
        "vat_words": speak_rubles(example["vat"]),
        "vat_rate": example["vat_rate"],
        "debit": example["debit"],
        "credit": example["credit"],
        "debit_spoken": speak_account(example["debit"]),
        "credit_spoken": speak_account(example["credit"]),
        "configuration_spoken": speak_configuration(configuration),
        "start_section": lesson["start_section"],
    }


def _from_lesson(topic: str, configuration: str, lesson: dict, materials_text: str) -> dict:
    values = _example_values(lesson, configuration)
    intro = _fill(lesson["intro_template"], values)
    outro = _fill(lesson["outro_template"], values)
    if materials_text:
        intro += " К уроку приложены ваши материалы: сверьте их с этими шагами."
    scenes = []
    for scene in lesson["scenes"]:
        steps = []
        for step in scene["steps"]:
            copied = dict(step)
            if copied.get("value"):
                copied["value"] = _fill(str(copied["value"]), values)
            steps.append(copied)
        scenes.append(
            {
                "id": scene["id"],
                "title": scene["title"],
                "narration": _fill(scene["narration"], values),
                "steps": steps,
            }
        )
    return {
        "title": lesson["title"],
        "topic": topic,
        "configuration": configuration,
        "goal": lesson["goal"],
        "intro_narration": intro,
        "outro_narration": outro,
        "scenes": scenes,
        "sources": lesson["sources"],
        "disclaimer": lesson["disclaimer"],
        "needs_review": True,
        "generated": True,
        "source_kind": "fixture",
    }


def _draft(topic: str, configuration: str, materials_text: str) -> dict:
    spoken = speak_configuration(configuration)
    extra = ""
    if materials_text:
        extra = " К сценарию приложены ваши материалы, но подписи кнопок по ним автоматически не угаданы."
    narration = (
        "Это черновик, а не проверенный проход по базе. "
        "Откройте учебную копию, найдите нужный раздел и сверьте подписи со справкой F1. "
        "Пока интерфейс не описан в карточке источников, ролик публиковать нельзя."
        + extra
    )
    return {
        "title": topic,
        "topic": topic,
        "configuration": configuration,
        "goal": f"Черновик по теме «{topic}».",
        "intro_narration": (
            f"В этом уроке разбираем тему: {topic}. "
            f"Программа — {spoken}. "
            "Сценарий собран автоматически и помечен для проверки. "
            "Начните с раздела, который видите в учебной базе."
            + extra
        ),
        "outro_narration": (
            "Черновик закончен. Дальше нужно вписать реальные подписи кнопок "
            "и ещё раз собрать урок уже по своей базе."
        ),
        "scenes": [
            {
                "id": "draft",
                "title": "Черновик шагов",
                "narration": narration,
                "steps": [
                    {
                        "id": "draft_open",
                        "action": "focus",
                        "target": "draft_start",
                        "caption": "Сверьте раздел с учебной базой",
                    }
                ],
            }
        ],
        "sources": [
            {
                "title": "1С:ИТС",
                "url": "https://its.1c.ru/",
                "note": "Найдите методику по своей теме в документации конфигурации. Текст ИТС в проект не копируется.",
            }
        ],
        "disclaimer": "Тема не совпала с готовой карточкой. Интерфейс нарисован как заглушка.",
        "needs_review": True,
        "generated": True,
        "source_kind": "draft",
    }


def _load_user_script(path: Path, configuration: str, topic: str) -> dict:
    data = load_json(path)
    if "scenes" not in data or "intro_narration" not in data or "outro_narration" not in data:
        raise ValueError(
            "В файле сценария нужны поля intro_narration, outro_narration и scenes."
        )
    data.setdefault("title", topic)
    data.setdefault("topic", topic)
    data["configuration"] = data.get("configuration") or configuration
    data["generated"] = False
    data["source_kind"] = "user"
    data.setdefault("needs_review", True)
    data.setdefault("sources", [])
    data.setdefault("disclaimer", "Сценарий передан пользователем. Сверьте его с учебной базой.")
    data.setdefault("goal", data["title"])
    return data


def build_script(project_root: Path, request: dict, research: dict) -> dict:
    script_path = request.get("script")
    if script_path:
        path = Path(script_path)
        if not path.is_absolute():
            path = project_root / path
        if not path.exists():
            raise FileNotFoundError(f"Не найден файл сценария: {path}")
        return _load_user_script(path, research["configuration"], research["topic"])
    if research["lesson"]:
        return _from_lesson(
            research["topic"],
            research["configuration"],
            research["lesson"],
            research["materials_text"],
        )
    return _draft(research["topic"], research["configuration"], research["materials_text"])
