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


def guard_name(name: str) -> None:
    if is_forbidden_name(name):
        raise RuntimeError(
            f"Кнопка «{name}» не нажимается: это отправка, выгрузка или удаление. "
            "В учебной записи такие действия только показываем."
        )


class FakeSession:
    """Имитация окна для проверки без живой 1С."""

    def __init__(self, names: list[str], values: dict | None = None, texts: list[str] | None = None, watcher: EscWatcher | None = None):
        self.names = list(names)
        self.values = dict(values or {})
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

    def find(self, name: str, timeout: float = 0.2) -> Found:
        self.watcher.poll()
        deadline = time.time() + timeout
        while True:
            self.watcher.poll()
            found = [item for item in self.names if norm(item) == norm(name) or norm(name) in norm(item)]
            exact = [item for item in found if norm(item) == norm(name)]
            chosen = exact or found
            if len(chosen) == 1:
                title = chosen[0]
                return Found(name=title, value=str(self.values.get(title, "")))
            if len(chosen) > 1:
                raise RuntimeError(
                    f"Элемент «{name}» найден несколько раз ({len(chosen)}). "
                    "Оставьте на экране одну такую подпись."
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

    def find(self, name: str, timeout: float = 8) -> Found:
        deadline = time.time() + timeout
        duplicates = 0
        while True:
            self.watcher.poll()
            matches = []
            try:
                items = self.win.descendants()
            except Exception:
                items = []
            for item in items:
                try:
                    title = item.window_text() or ""
                except Exception:
                    continue
                if not title:
                    continue
                if norm(title) == norm(name) or norm(name) in norm(title):
                    matches.append(item)
            exact = [item for item in matches if norm(item.window_text() or "") == norm(name)]
            chosen = exact or matches
            if len(chosen) == 1:
                item = chosen[0]
                rect = item.rectangle()
                return Found(
                    name=item.window_text() or name,
                    value="",
                    left=int(rect.left),
                    top=int(rect.top),
                    right=int(rect.right),
                    bottom=int(rect.bottom),
                    raw=item,
                )
            duplicates = len(chosen)
            if time.time() >= deadline:
                break
            time.sleep(0.45)
        if duplicates > 1:
            raise RuntimeError(
                f"Элемент «{name}» найден несколько раз. Закройте лишние окна и оставьте одну такую подпись."
            )
        raise LookupError(name)

    def click(self, element: Found) -> None:
        self.watcher.poll()
        guard_name(element.name)
        self.move_cursor((element.left + element.right) // 2, (element.top + element.bottom) // 2)
        element.raw.click_input()

    def set_text(self, element: Found, value: str) -> None:
        self.watcher.poll()
        guard_name(element.name)
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
