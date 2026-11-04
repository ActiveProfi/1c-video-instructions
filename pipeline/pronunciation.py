"""Словарь произношения для синтеза речи (config/pronunciation.json).

Применяется только к тексту, который уходит в TTS. Субтитры берутся из исходного
текста и остаются без знаков ударения и замен.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

STRESS = "\u0301"
CONFIG = Path(__file__).resolve().parents[1] / "config" / "pronunciation.json"


@lru_cache(maxsize=4)
def load(path: str | None = None) -> tuple[dict, dict]:
    data = json.loads(Path(path or CONFIG).read_text(encoding="utf-8-sig"))
    words = {rule["word"].lower().replace("ё", "е"): rule["pronunciation"] for rule in data.get("rules") or []}
    return words, dict(data.get("abbreviations") or {})


WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё\u0301]+(?:-[0-9A-Za-zА-Яа-яЁё\u0301]+)*")


def _match_case(original: str, replacement: str) -> str:
    if original.isupper() and len(original) > 1:
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def pronounce(text: str, path: str | None = None) -> str:
    """Текст для синтеза: ударения в терминах и чтение аббревиатур."""
    words, abbreviations = load(path)

    def replace(match: re.Match) -> str:
        token = match.group()
        if STRESS in token:
            return token  # ударение уже стоит в сценарии
        if token in abbreviations:
            return abbreviations[token]
        key = token.lower().replace("ё", "е")
        if key in words:
            return _match_case(token, words[key])
        return token

    return WORD.sub(replace, text or "")


def strip_stress(text: str) -> str:
    return (text or "").replace(STRESS, "")


def changed_terms(text: str, path: str | None = None) -> list[str]:
    """Какие слова текста меняет словарь (для отчёта и выборочной переозвучки)."""
    words, abbreviations = load(path)
    found = []
    for match in WORD.finditer(text or ""):
        token = match.group()
        if STRESS in token:
            continue
        if token in abbreviations or token.lower().replace("ё", "е") in words:
            found.append(token)
    return found


def revoice_needed(text: str, path: str | None = None) -> list[str]:
    """Слова из revoice_stems, ради которых готовую озвучку стоит переписать."""
    data = json.loads(Path(path or CONFIG).read_text(encoding="utf-8-sig"))
    stems = [s.lower().replace("ё", "е") for s in data.get("revoice_stems") or []]
    hits = []
    for token in changed_terms(text, path):
        key = token.lower().replace("ё", "е")
        if any(key.startswith(stem) for stem in stems):
            hits.append(token)
    return hits
