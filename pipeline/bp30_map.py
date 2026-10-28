"""Карта полного интерфейса «Такси» как основной источник имён, не догадка во время записи."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

MAP_PATH = Path(__file__).with_name("bp30_ui_map.yaml")


@lru_cache(maxsize=1)
def load_map(path: str | None = None) -> dict:
    file_path = Path(path) if path else MAP_PATH
    data = yaml.safe_load(file_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or "entries" not in data:
        raise ValueError(f"В карте интерфейса нет раздела entries: {file_path}")
    return data


def get_entry(key: str, ui_map: dict | None = None) -> dict:
    data = ui_map or load_map()
    try:
        return data["entries"][key]
    except KeyError as error:
        raise KeyError(f"В карте интерфейса нет ключа «{key}».") from error


def button(key: str, ui_map: dict | None = None) -> dict:
    data = ui_map or load_map()
    try:
        return data["buttons"][key]
    except KeyError as error:
        raise KeyError(f"В карте нет кнопки «{key}».") from error


def click_steps(key: str, ui_map: dict | None = None) -> list[dict]:
    """Только те пункты пути, по которым нужно щелкнуть. Заголовки групп остаются within."""
    entry = get_entry(key, ui_map)
    steps = []
    for item in entry.get("path") or []:
        if item.get("click") is False:
            continue
        steps.append(
            {
                "name": item.get("name") or "",
                "role": item.get("role") or "",
                "within": item.get("within") or "",
                "index": item.get("index"),
                "aliases": list(item.get("aliases") or []),
                "map_key": key,
            }
        )
    return steps


def names_to_try(action: dict) -> list[str]:
    names = [action.get("name") or ""]
    for alias in action.get("aliases") or []:
        if alias and alias not in names:
            names.append(alias)
    return [name for name in names if name]


def find_first(session, action: dict, timeout: float):
    """Сначала точное имя из карты и алиасы релиза, потом ошибка «не найден»."""
    last = None
    for name in names_to_try(action):
        try:
            return session.find(
                name,
                timeout=timeout,
                role=action.get("role") or "",
                within=action.get("within") or "",
                index=action.get("index"),
            )
        except LookupError as error:
            last = error
    if last is None:
        raise LookupError(action.get("name") or "")
    raise last
