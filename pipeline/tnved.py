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


NOT_FOUND_MESSAGE = "Номенклатура Смартфон не найдена, будет создана в уроке"
ITEM_NAME = "Смартфон"


def open_nomenclature(session) -> None:
    """Справочники → Номенклатура. Список только открывается."""
    try:
        section = session.find("Справочники", timeout=8, role="menu")
        session.click(section)
        if hasattr(session, "refresh"):
            session.refresh(1.0)
        try:
            link = session.find("Номенклатура", timeout=4, role="hyperlink", within="Справочники")
        except LookupError:
            session.global_search("Номенклатура")
            link = session.find("Номенклатура", timeout=4, role="hyperlink")
        session.click(link)
    except LookupError as error:
        raise RuntimeError(
            f"Не удалось открыть Справочники → Номенклатура: на экране нет «{error}». "
            "Оставьте главное окно 1С открытым."
        ) from None


def search_list(session, query: str) -> bool:
    for field_name in ("Поиск", "Найти", "Строка поиска"):
        try:
            box = session.find(field_name, timeout=1.5, role="field", within="Номенклатура")
        except LookupError:
            continue
        session.set_text(box, query)
        return True
    return False


def find_list_item(session, title: str, timeout: float = 3):
    search_list(session, title)
    try:
        return session.find(title, timeout=timeout, within="Номенклатура")
    except LookupError:
        return None


def read_smartphone_tnved(session):
    """Открывает справочник и ищет карточку. Ничего не проводит и не удаляет.

    Если позиции нет, возвращает None.
    """
    open_nomenclature(session)
    item = find_list_item(session, ITEM_NAME)
    if item is None:
        return None
    session.click(item)
    field = session.find("ТН ВЭД", timeout=6, role="field")
    return session.read(field).strip()


def ensure_smartphone(session) -> dict:
    """С главного окна создаёт номенклатуру, только если её ещё нет."""
    open_nomenclature(session)
    if find_list_item(session, ITEM_NAME) is not None:
        return {"created": False, "found": True, "message": "Номенклатура Смартфон уже есть в справочнике."}
    button = session.find("Создать", timeout=6, role="button", within="Номенклатура")
    session.click(button)
    name = session.find("Наименование", timeout=6, role="field")
    session.set_text(name, ITEM_NAME)
    code = session.find("ТН ВЭД", timeout=6, role="field")
    session.set_text(code, EXPECTED_CODE)
    saved = False
    for save_name in ("Записать и закрыть", "Записать"):
        try:
            save = session.find(save_name, timeout=2, role="button")
        except LookupError:
            continue
        session.click(save)
        saved = True
        break
    if not saved:
        raise RuntimeError(
            "Карточка «Смартфон» заполнена, но кнопка «Записать» не найдена. Ничего не удалялось."
        )
    return {
        "created": True,
        "found": False,
        "message": f"Номенклатура Смартфон создана, код ТН ВЭД {EXPECTED_CODE}.",
    }


def write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
