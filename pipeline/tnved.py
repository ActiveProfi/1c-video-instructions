"""Только чтение карточки номенклатуры: код ТН ВЭД смартфона."""

from __future__ import annotations

import json
import re
from pathlib import Path

EXPECTED_CODE = "8517 13 000 0"
EXPECTED_DIGITS = "8517130000"
FEE_RUB = "73 860"
DECISION = "ПП РФ № 1637"


def digits(code: str) -> str:
    return re.sub(r"\D", "", code or "")


def assess_tnved(code: str) -> dict:
    got = digits(code)
    ok = got == EXPECTED_DIGITS
    warning = None
    if not ok:
        warning = (
            f"В карточке код ТН ВЭД «{code or 'пусто'}», а для ставки сбора {FEE_RUB} ₽ "
            f"по {DECISION} нужен {EXPECTED_CODE}. Если код другой и его нет в приложении № 1, "
            "ставка 73 860 ₽ может не подойти."
        )
    return {
        "code": code,
        "expected": EXPECTED_CODE,
        "ok": ok,
        "warning": warning,
    }


def read_smartphone_tnved(session) -> str:
    """Открывает справочник и карточку. Ничего не проводит и не удаляет."""
    for name in ("Справочники", "Номенклатура"):
        element = session.find(name, timeout=8)
        session.click(element)
    item = session.find("Смартфон", timeout=8)
    session.click(item)
    field = session.find("ТН ВЭД", timeout=8)
    return session.read(field).strip()


def write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
