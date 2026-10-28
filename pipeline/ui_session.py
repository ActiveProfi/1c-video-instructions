"""Окно 1С: поиск элементов, курсор, щелчок. Удаление и отправка запрещены."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from pipeline.lesson_yaml import is_forbidden_name, norm


class StopRequested(RuntimeError):
    pass


class EscWatcher:
    def __init__(self) -> None:
        self.stopped = False
        self.ignore_until = 0.0

    def request_stop(self) -> None:
        self.stopped = True

    def ignore_for(self, seconds: float) -> None:
        self.ignore_until = time.time() + seconds

    def poll(self) -> None:
        if time.time() < self.ignore_until:
            return
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


def read_element_texts(item) -> dict:
    """Все подписи элемента. В 1С текст часто лежит не в window_text."""
    texts = {
        "window_text": "",
        "name": "",
        "automation_id": "",
        "value": "",
        "help_text": "",
    }
    try:
        texts["window_text"] = item.window_text() or ""
    except Exception:
        pass
    info = None
    try:
        info = item.element_info
    except Exception:
        info = None
    if info is not None:
        try:
            texts["name"] = info.name or ""
        except Exception:
            pass
        try:
            texts["automation_id"] = info.automation_id or ""
        except Exception:
            pass
    legacy = {}
    try:
        legacy = item.legacy_properties() or {}
    except Exception:
        legacy = {}
    try:
        if not texts["name"]:
            texts["name"] = str(legacy.get("Name") or "")
    except Exception:
        pass
    try:
        texts["value"] = str(legacy.get("Value") or "")
    except Exception:
        pass
    try:
        texts["help_text"] = str(legacy.get("Help") or "")
    except Exception:
        pass
    return texts


def best_label(texts: dict) -> str:
    for key in ("name", "window_text", "value", "help_text", "automation_id"):
        value = str(texts.get(key) or "").strip()
        if value:
            return value
    return ""


def text_variants(item: dict) -> list[str]:
    values = [str(item.get("name") or "")]
    texts = item.get("texts") or {}
    if isinstance(texts, dict):
        values.extend(str(value) for value in texts.values())
    return [value for value in values if value and value.strip()]


def choose_match(candidates: list[dict], name: str, role: str = "", within: str = "", index=None):
    """Выбирает один элемент. Неоднозначная подпись не считается ошибкой."""
    wanted = norm(name)

    def matches(item) -> bool:
        for value in text_variants(item):
            folded = norm(value)
            if folded == wanted or wanted in folded:
                return True
        return False

    def exact(item) -> bool:
        return any(norm(value) == wanted for value in text_variants(item))

    named = [item for item in candidates if matches(item)]
    if not named:
        raise LookupError(name)
    exact_items = [item for item in named if exact(item)]
    pool = exact_items or named
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


def save_diagnostics(session, folder: Path, reason: str = "") -> dict:
    """Снимок экрана и список элементов. Пишется при любой остановке."""
    folder.mkdir(parents=True, exist_ok=True)
    saved = {"reason": reason}
    try:
        rows = session.dump_ui()
        path = folder / "ui-dump.json"
        path.write_text(json.dumps({"reason": reason, "elements": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        saved["ui_dump"] = str(path)
    except Exception as error:
        saved["ui_dump_error"] = str(error)
    try:
        shot = folder / "screen.png"
        session.save_screenshot(shot)
        saved["screenshot"] = str(shot)
    except Exception as error:
        saved["screenshot_error"] = str(error)
    return saved


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

    def refresh(self, pause: float = 0.8) -> None:
        del pause

    def close_panels(self) -> None:
        self.watcher.ignore_for(0.2)
        self.closed_panels = getattr(self, "closed_panels", 0) + 1

    def return_home(self) -> str:
        try:
            tab = self.find("Начальная страница", timeout=0.05)
        except LookupError:
            self.cursor = (94, 41)
            self.home = "coordinates"
            return "coordinates"
        self.click(tab)
        self.home = "element"
        return "element"

    def prepare(self) -> None:
        self.close_panels()
        self.return_home()

    def finish(self) -> None:
        self.close_panels()
        self.return_home()

    def global_search(self, query: str) -> None:
        self.searches = getattr(self, "searches", [])
        self.searches.append(query)
        self.controls.append({
            "name": query,
            "role": "hyperlink",
            "parents": ["Поиск"],
            "visible": True,
            "value": "",
            "texts": {"name": query, "window_text": "", "value": "", "help_text": "", "automation_id": ""},
        })
        self.names.append(query)

    def dump_ui(self) -> list[dict]:
        rows = []
        for item in self.controls:
            if not item.get("visible", True):
                continue
            rows.append({
                "texts": item.get("texts") or {"name": item.get("name") or ""},
                "control_type": item.get("role") or "",
                "rect": {"left": 0, "top": 0, "right": 10, "bottom": 10},
                "visible": True,
            })
        return rows

    def save_screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")

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
        """Ищет название организации в window_text, element_info.name и legacy Value."""
        token = norm(organization).split()[0]
        if not token:
            return False
        try:
            if token in norm(self.win.window_text() or ""):
                return True
        except Exception:
            pass
        try:
            items = self.win.descendants()
        except Exception:
            return False
        for item in items:
            self.watcher.poll()
            try:
                if token in norm(item.window_text() or ""):
                    return True
            except Exception:
                pass
            try:
                if token in norm(item.element_info.name or ""):
                    return True
            except Exception:
                pass
            try:
                legacy = item.legacy_properties() or {}
                if token in norm(str(legacy.get("Value") or "")):
                    return True
            except Exception:
                pass
        return False

    def refresh(self, pause: float = 1.0) -> None:
        self._cache = None
        time.sleep(pause)

    def close_panels(self) -> None:
        """Esc закрывает панель поиска или раздел. Свой Esc не считается остановкой урока."""
        self.watcher.ignore_for(1.5)
        try:
            self.win.set_focus()
        except Exception:
            pass
        try:
            self.win.type_keys("{ESC}{ESC}", pause=0.25)
        except Exception:
            pass
        time.sleep(0.4)
        self._cache = None

    def return_home(self) -> str:
        """Вкладка «Начальная страница», иначе клик в левый верх окна."""
        self._cache = None
        try:
            tab = self.find("Начальная страница", timeout=1.5)
            self.click(tab)
            return "element"
        except LookupError:
            left, top, _right, _bottom = self.window_rect()
            x = int(left) + 94
            y = int(top) + 41
            self.move_cursor(x, y)
            try:
                from pywinauto import mouse

                mouse.click(coords=(x, y))
            except Exception:
                pass
            time.sleep(0.5)
            self._cache = None
            return "coordinates"

    def prepare(self) -> None:
        self.close_panels()
        self.return_home()

    def finish(self) -> None:
        self.close_panels()
        self.return_home()

    def global_search(self, query: str) -> None:
        """Запасной путь: Ctrl+Shift+F и поле «Поиск» сверху."""
        self.watcher.ignore_for(0.4)
        try:
            self.win.set_focus()
        except Exception:
            pass
        try:
            self.win.type_keys("^+f", pause=0.05)
        except Exception:
            pass
        time.sleep(0.7)
        self._cache = None
        box = self.find("Поиск", timeout=3, role="field")
        self.set_text(box, query)
        try:
            self.win.type_keys("{ENTER}")
        except Exception:
            pass
        time.sleep(0.8)
        self._cache = None

    def save_screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = self.win.capture_as_image()
        image.save(str(path))

    def dump_ui(self) -> list[dict]:
        self._cache = None
        rows = []
        for item in self._snapshot():
            if not item.get("visible", True):
                continue
            left, top, right, bottom = item["box"]
            rows.append({
                "texts": item.get("texts") or {},
                "control_type": item.get("control_type") or "",
                "role": item.get("role") or "",
                "rect": {"left": left, "top": top, "right": right, "bottom": bottom},
                "visible": True,
            })
        return rows

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
            texts = read_element_texts(item)
            title = best_label(texts)
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
                if not parent_name:
                    try:
                        parent_name = parent.element_info.name or ""
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
                "texts": texts,
                "control_type": control,
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
