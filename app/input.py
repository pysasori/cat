from __future__ import annotations

import ctypes
import time
from abc import ABC, abstractmethod

import win32api
import win32con
import win32gui

from app.models import InputMode

_user32 = ctypes.windll.user32

VK = {
    "alt": win32con.VK_MENU,
    "ctrl": win32con.VK_CONTROL,
    "shift": win32con.VK_SHIFT,
    "enter": win32con.VK_RETURN,
    "esc": win32con.VK_ESCAPE,
    "backspace": win32con.VK_BACK,
    "delete": win32con.VK_DELETE,
    "down": win32con.VK_DOWN,
    **{f"f{i}": win32con.VK_F1 + i - 1 for i in range(1, 13)},
    **{char: ord(char.upper()) for char in "abcdefghijklmnopqrstuvwxyz0123456789"},
}


def virtual_key(key: str) -> int:
    try:
        return VK[key.lower()]
    except KeyError as error:
        raise ValueError(f"невідома клавіша {key!r}") from error


def key_lparam(vk: int, down: bool) -> int:
    value = 1 | (_user32.MapVirtualKeyW(vk, 0) << 16)
    if not down:
        value |= (1 << 30) | (1 << 31)
    return value


class InputDriver(ABC):
    @abstractmethod
    def press(self, key: str) -> None: ...

    @abstractmethod
    def hotkey(self, *keys: str) -> None: ...

    @abstractmethod
    def type_text(self, text: str) -> None: ...

    @abstractmethod
    def clear_text(self, length: int = 16) -> None: ...

    @abstractmethod
    def move(self, x: int, y: int) -> None: ...

    @abstractmethod
    def click(self, x: int, y: int) -> None: ...

    @abstractmethod
    def drag(self, x1: int, y1: int, x2: int, y2: int, *, modifier: str | None = None) -> None: ...


class BackgroundInput(InputDriver):
    """Фоновий ввід через PostMessage — фізичні миша й клавіатура вільні."""

    def __init__(self, hwnd: int, duration: float = 0.35) -> None:
        self.hwnd = hwnd
        self.duration = duration

    @staticmethod
    def _point(x: int, y: int) -> int:
        return (y << 16) | (x & 0xFFFF)

    def _key(self, key: str, down: bool) -> None:
        vk = virtual_key(key)
        if down and win32gui.GetForegroundWindow() != self.hwnd:
            win32api.PostMessage(self.hwnd, win32con.WM_ACTIVATEAPP, 1, 0)
            win32api.PostMessage(self.hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
            win32api.PostMessage(self.hwnd, win32con.WM_SETFOCUS, 0, 0)
        message = win32con.WM_KEYDOWN if down else win32con.WM_KEYUP
        if key.lower() == "alt":
            message = win32con.WM_SYSKEYDOWN if down else win32con.WM_SYSKEYUP
        win32api.PostMessage(self.hwnd, message, vk, key_lparam(vk, down))

    def press(self, key: str) -> None:
        self._key(key, True)
        time.sleep(0.06)
        self._key(key, False)

    def hotkey(self, *keys: str) -> None:
        for key in keys:
            self._key(key, True)
        time.sleep(0.06)
        for key in reversed(keys):
            self._key(key, False)

    def type_text(self, text: str) -> None:
        for char in text:
            win32api.PostMessage(self.hwnd, win32con.WM_CHAR, ord(char), 0)
            time.sleep(0.025)

    def clear_text(self, length: int = 16) -> None:
        for _ in range(length):
            self.press("backspace")

    def move(self, x: int, y: int) -> None:
        win32api.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, 0, self._point(x, y))

    def click(self, x: int, y: int) -> None:
        point = self._point(x, y)
        win32api.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, 0, point)
        time.sleep(0.04)
        win32api.PostMessage(self.hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, point)
        time.sleep(0.06)
        win32api.PostMessage(self.hwnd, win32con.WM_LBUTTONUP, 0, point)

    def drag(self, x1: int, y1: int, x2: int, y2: int, *, modifier: str | None = None) -> None:
        if modifier:
            self._key(modifier, True)
        try:
            start = self._point(x1, y1)
            win32api.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, 0, start)
            time.sleep(0.08)
            win32api.PostMessage(self.hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, start)
            steps = max(8, int(self.duration / 0.02))
            for step in range(1, steps + 1):
                x = x1 + (x2 - x1) * step // steps
                y = y1 + (y2 - y1) * step // steps
                win32api.PostMessage(
                    self.hwnd,
                    win32con.WM_MOUSEMOVE,
                    win32con.MK_LBUTTON,
                    self._point(x, y),
                )
                time.sleep(self.duration / steps)
            win32api.PostMessage(self.hwnd, win32con.WM_LBUTTONUP, 0, self._point(x2, y2))
        finally:
            if modifier:
                self._key(modifier, False)


class ForegroundInput(InputDriver):
    """Реальний курсор через PyAutoGUI; тимчасово забирає керування мишею."""

    def __init__(self, hwnd: int, duration: float = 0.35) -> None:
        self.hwnd = hwnd
        self.duration = duration

    def _activate(self) -> None:
        if win32gui.IsIconic(self.hwnd):
            win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(self.hwnd)
        except win32gui.error:
            win32gui.BringWindowToTop(self.hwnd)
        time.sleep(0.12)

    def _screen(self, x: int, y: int) -> tuple[int, int]:
        return win32gui.ClientToScreen(self.hwnd, (x, y))

    def press(self, key: str) -> None:
        import pyautogui

        self._activate()
        pyautogui.press(key)

    def hotkey(self, *keys: str) -> None:
        import pyautogui

        self._activate()
        pyautogui.hotkey(*keys)

    def type_text(self, text: str) -> None:
        import pyautogui

        self._activate()
        pyautogui.write(text, interval=0.025)

    def clear_text(self, length: int = 16) -> None:
        import pyautogui

        self._activate()
        pyautogui.press("backspace", presses=length, interval=0.01)

    def move(self, x: int, y: int) -> None:
        import pyautogui

        self._activate()
        pyautogui.moveTo(*self._screen(x, y), duration=0.12)

    def click(self, x: int, y: int) -> None:
        import pyautogui

        self._activate()
        pyautogui.click(*self._screen(x, y))

    def drag(self, x1: int, y1: int, x2: int, y2: int, *, modifier: str | None = None) -> None:
        import pyautogui

        self._activate()
        pyautogui.moveTo(*self._screen(x1, y1), duration=0.12)
        if modifier:
            pyautogui.keyDown(modifier)
        try:
            pyautogui.dragTo(*self._screen(x2, y2), duration=self.duration, button="left")
        finally:
            if modifier:
                pyautogui.keyUp(modifier)


def make_input(mode: InputMode, hwnd: int, duration: float) -> InputDriver:
    if mode is InputMode.FOREGROUND:
        return ForegroundInput(hwnd, duration)
    return BackgroundInput(hwnd, duration)
