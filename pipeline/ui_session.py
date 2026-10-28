"""Окно 1С: поиск элементов, курсор, щелчок. Удаление и отправка запрещены."""

from __future__ import annotations

import time
from dataclasses import dataclass

from pipeline.lesson_yaml import is_forbidden_name, norm


class StopRequested(RuntimeError):
    pass


class EscWatcher:
    def __init__(self) -> None:
        self.stopped = False

    def request_stop(self) -> None:
        self.stopped = True

    def poll(self) -> None:
        if self.stopped:
            raise StopRequested(
                "Остановлено клавишей Esc. Документы не удалялись. Текущий шаг не проводился."
            )
        if _escape_pressed():
            self.stopped = True
            raise StopRequested(
                "Остановлено клавишей Esc. Документы не удалялись. Текущий шаг не проводился."
            )


def _escape_pressed() -> bool:
    try:
        import ctypes
        import os

        if os.name != "nt":
            return False
        return bool(ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000)
    except Exception:
        return False


@dataclass
class Found:
    name: str
    value: str = ""
    left: int = 40
    top: int = 80
    right: int = 240
    bottom: int = 120
    raw: object = None
    role: str = ""
    parents: tuple = ()
    ambiguous: bool = False
    note: str = ""


ROLE_ALIASES = {
    "menu": "menu",
    "раздел": "menu",
    "пункт": "menu",
    "listitem": "menu",
    "treeitem": "menu",
    "menuitem": "menu",
    "hyperlink": "hyperlink",
    "ссылка": "hyperlink",
    "гиперссылка": "hyperlink",
    "field": "field",
    "edit": "field",
    "combobox": "field",
    "поле": "field",
    "button": "button",
    "кнопка": "button",
    "text": "text",
    "надпись": "text",
}


def canonical_role(control_type: str) -> str:
    value = norm(control_type).replace("controltype.", "")
    if not value:
        return ""
    if value in ROLE_ALIASES:
        return ROLE_ALIASES[value]
    for alias, role in ROLE_ALIASES.items():
        if alias in value:
            return role
    return "other"


def choose_match(candidates: list[dict], name: str, role: str = "", within: str = "", index=None):
    """Выбирает один элемент. Неоднозначная подпись не считается ошибкой."""
    wanted = norm(name)
    named = [item for item in candidates if norm(item.get("name") or "") == wanted or wanted in norm(item.get("name") or "")]
    if not named:
        raise LookupError(name)
    exact = [item for item in named if norm(item.get("name") or "") == wanted]
    pool = exact or named
    notes = []
    several = len(pool) > 1
    if several:
        notes.append(f"совпадений: {len(pool)}")
    wanted_role = canonical_role(role) if role else ""
    if wanted_role:
        by_role = [item for item in pool if item.get("role") == wanted_role]
        if by_role:
            pool = by_role
            notes.append(f"тип {wanted_role}")
    if within:
        parent_token = norm(within)
        by_parent = [
            item for item in pool
            if any(parent_token in norm(parent) for parent in item.get("parents") or [])
        ]
        if by_parent:
            pool = by_parent
            notes.append(f"внутри «{within}»")
    visible = [item for item in pool if item.get("visible", True)]
    if visible:
        pool = visible
        if several:
            notes.append("видимый в активном окне")
    chosen = pool[0]
    if index is not None:
        try:
            position = int(index)
        except (TypeError, ValueError):
            position = -1
        if 0 <= position < len(pool):
            chosen = pool[position]
            notes.append(f"номер {position}")
    return chosen, "; ".join(notes), several


def guard_name(name: str) -> None:
    if is_forbidden_name(name):
        raise RuntimeError(
            f"Кнопка «{name}» не нажимается: это отправка, выгрузка или удаление. "
            "В учебной записи такие действия только показываем."
        )


class FakeSession:
    """Имитация окна для проверки без живой 1С."""

    def __init__(self, names: list, values: dict | None = None, texts: list[str] | None = None, watcher: EscWatcher | None = None):
        self.values = dict(values or {})
        self.controls = []
        for item in names:
            if isinstance(item, dict):
                title = item.get("name") or ""
                self.controls.append({
                    "name": title,
                    "role": canonical_role(item.get("role") or item.get("control_type") or "menu"),
                    "parents": list(item.get("parents") or ([item["parent"]] if item.get("parent") else [])),
                    "visible": item.get("visible", True),
                    "value": str(item.get("value", self.values.get(title, ""))),
                })
            else:
                self.controls.append({
                    "name": item,
                    "role": "menu",
                    "parents": [],
                    "visible": True,
                    "value": str(self.values.get(item, "")),
                })
        self.names = [item["name"] for item in self.controls]
        self.texts = list(texts or [])
        self.watcher = watcher or EscWatcher()
        self.clicks: list[str] = []
        self.fills: list[tuple[str, str]] = []
        self.cursor: tuple[int, int] | None = None

    def connect(self) -> None:
        self.watcher.poll()

    def organization_visible(self, organization: str) -> bool:
        blob = norm(" ".join(self.texts + self.names + list(self.values.values())))
        token = norm(organization).split()[0]
        return token in blob

    def window_rect(self) -> tuple[int, int, int, int]:
        return (10, 10, 1290, 730)

    def move_cursor(self, x: int, y: int) -> None:
        self.cursor = (x, y)

    def find(self, name: str, timeout: float = 0.2, role: str = "", within: str = "", index=None) -> Found:
        self.watcher.poll()
        deadline = time.time() + timeout
        while True:
            self.watcher.poll()
            try:
                chosen, note, several = choose_match(self.controls, name, role, within, index)
            except LookupError:
                chosen = None
                note = ""
                several = False
            if chosen is not None:
                title = chosen["name"]
                return Found(
                    name=title,
                    value=str(chosen.get("value") or self.values.get(title, "")),
                    role=chosen.get("role") or "",
                    parents=tuple(chosen.get("parents") or []),
                    ambiguous=several,
                    note=note,
                )
            if time.time() >= deadline:
                raise LookupError(name)
            time.sleep(0.01)

    def click(self, element: Found) -> None:
        self.watcher.poll()
        guard_name(element.name)
        self.clicks.append(element.name)
        center_x = (element.left + element.right) // 2
        center_y = (element.top + element.bottom) // 2
        self.move_cursor(center_x, center_y)

    def set_text(self, element: Found, value: str) -> None:
        self.watcher.poll()
        guard_name(element.name)
        self.fills.append((element.name, value))
        self.values[element.name] = value

    def read(self, element: Found) -> str:
        return element.value or self.values.get(element.name, "")


class PywinautoSession:
    """Живое окно 1С через UI Automation (тот же источник, что Inspect-1C.ps1)."""

    def __init__(self, watcher: EscWatcher | None = None):
        self.watcher = watcher or EscWatcher()
        self.win = None
        self._cache = None
        self._cache_at = 0.0

    def connect(self) -> None:
        self.watcher.poll()
        try:
            from pywinauto import Desktop
        except ImportError as error:
            raise RuntimeError(
                "Не установлен пакет pywinauto. В папке проекта выполните: py -3 -m pip install pywinauto"
            ) from error
        desktop = Desktop(backend="uia")
        found = []
        for window in desktop.windows():
            self.watcher.poll()
            try:
                title = window.window_text() or ""
            except Exception:
                continue
            if any(token in title for token in ("1С", "1C", "Бухгалтерия")):
                found.append(window)
        if not found:
            raise RuntimeError(
                "Окно 1С не найдено. Откройте учебную базу «Альфа ООО» в этом сеансе Windows "
                "и оставьте её на экране."
            )
        if len(found) > 1:
            raise RuntimeError("Открыто несколько окон 1С. Закройте лишние и оставьте одно.")
        self.win = found[0]
        try:
            self.win.set_focus()
        except Exception:
            pass

    def organization_visible(self, organization: str) -> bool:
        token = norm(organization).split()[0]
        try:
            blob = norm(self.win.window_text() or "")
        except Exception:
            blob = ""
        if token in blob:
            return True
        try:
            for item in self.win.descendants():
                self.watcher.poll()
                if token in norm(item.window_text() or ""):
                    return True
        except Exception:
            return False
        return False

    def window_rect(self) -> tuple[int, int, int, int]:
        rect = self.win.rectangle()
        return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))

    def move_cursor(self, x: int, y: int) -> None:
        try:
            import ctypes

            ctypes.windll.user32.SetCursorPos(int(x), int(y))
        except Exception:
            pass

    def _snapshot(self) -> list[dict]:
        if self._cache is not None and time.time() - self._cache_at < 1:
            return self._cache
        rows = []
        try:
            items = self.win.descendants()
        except Exception:
            items = []
        for item in items:
            self.watcher.poll()
            try:
                title = item.window_text() or ""
            except Exception:
                continue
            if not title:
                continue
            control = ""
            try:
                control = str(item.element_info.control_type or "")
            except Exception:
                control = ""
            visible = True
            try:
                visible = bool(item.is_visible())
            except Exception:
                visible = True
            parents = []
            parent = item
            for _ in range(8):
                try:
                    parent = parent.parent()
                except Exception:
                    break
                if parent is None:
                    break
                try:
                    parent_name = parent.window_text() or ""
                except Exception:
                    parent_name = ""
                if parent_name:
                    parents.append(parent_name)
            try:
                rect = item.rectangle()
                box = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
            except Exception:
                box = (40, 80, 240, 120)
            rows.append({
                "name": title,
                "role": canonical_role(control),
                "parents": parents,
                "visible": visible,
                "box": box,
                "raw": item,
            })
        self._cache = rows
        self._cache_at = time.time()
        return rows

    def find(self, name: str, timeout: float = 8, role: str = "", within: str = "", index=None) -> Found:
        deadline = time.time() + timeout
        while True:
            self.watcher.poll()
            try:
                chosen, note, several = choose_match(self._snapshot(), name, role, within, index)
            except LookupError:
                chosen = None
            if chosen is not None:
                left, top, right, bottom = chosen["box"]
                return Found(
                    name=chosen["name"],
                    left=left,
                    top=top,
                    right=right,
                    bottom=bottom,
                    raw=chosen["raw"],
                    role=chosen.get("role") or "",
                    parents=tuple(chosen.get("parents") or []),
                    ambiguous=several,
                    note=note,
                )
            if time.time() >= deadline:
                raise LookupError(name)
            self._cache = None
            time.sleep(0.45)

    def click(self, element: Found) -> None:
        self.watcher.poll()
        guard_name(element.name)
        self._cache = None
        self.move_cursor((element.left + element.right) // 2, (element.top + element.bottom) // 2)
        element.raw.click_input()

    def set_text(self, element: Found, value: str) -> None:
        self.watcher.poll()
        guard_name(element.name)
        self._cache = None
        try:
            element.raw.set_edit_text(value)
        except Exception:
            element.raw.click_input()
            element.raw.type_keys(value, with_spaces=True, pause=0.02)

    def read(self, element: Found) -> str:
        item = element.raw
        for reader in (
            lambda: item.get_value(),
            lambda: item.iface_value.CurrentValue,
            lambda: item.window_text(),
        ):
            try:
                value = reader()
            except Exception:
                continue
            if value:
                return str(value)
        return element.value or ""
