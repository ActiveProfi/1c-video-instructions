"""Запуск внешних программ.

На русском Windows вывод читается как cp1251. У ffmpeg в этом потоке
встречается байт 0x98, из‑за него поток обрывается, а текст результата
становится пустым. Здесь вывод всегда читается как UTF-8, плохие байты
заменяются, пустой ответ не отдаётся в json.loads.
"""

from __future__ import annotations

import json
import subprocess


def run_captured(args: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(part) for part in args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def text_of(value: str | None) -> str:
    if isinstance(value, str):
        return value
    return ""


def loads_json(value: str | None, what: str) -> dict:
    raw = text_of(value).strip()
    if not raw:
        raise RuntimeError(
            f"{what} ничего не вернул. Проверьте, что программа установлена, "
            "а путь к файлу открывается. В пути могут быть русские буквы и пробелы."
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{what} вернул не JSON: {raw[:400]}") from error
    if not isinstance(data, dict):
        raise RuntimeError(f"{what} вернул не объект JSON.")
    return data
