from __future__ import annotations

import subprocess
from dataclasses import dataclass

import win32api
import win32con
import win32gui
import win32process


@dataclass(frozen=True)
class GameWindow:
    hwnd: int
    title: str
    width: int
    height: int
    iconic: bool
    pid: int
    executable: str = ""

    def as_dict(self) -> dict:
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "width": self.width,
            "height": self.height,
            "iconic": self.iconic,
            "pid": self.pid,
            "executable": self.executable,
        }


def _process_executable(pid: int) -> str:
    try:
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
            False,
            pid,
        )
        try:
            return win32process.GetModuleFileNameEx(handle, 0)
        finally:
            win32api.CloseHandle(handle)
    except Exception:
        return ""


def _element_pids() -> set[int]:
    try:
        output = subprocess.check_output(
            'tasklist /FI "IMAGENAME eq ElementClient.exe" /FO CSV /NH',
            shell=True,
            stderr=subprocess.DEVNULL,
        ).decode("cp866", "ignore")
    except (OSError, subprocess.SubprocessError):
        return set()
    return {int(line.split('\",\"')[1]) for line in output.splitlines() if line.startswith('"')}


def game_windows() -> list[GameWindow]:
    pids = _element_pids()
    found: list[GameWindow] = []

    def callback(hwnd: int, _: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        if win32gui.GetClassName(hwnd) != "ElementClient Window" and pid not in pids:
            return
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        found.append(GameWindow(
            hwnd=hwnd,
            title=win32gui.GetWindowText(hwnd),
            width=right - left,
            height=bottom - top,
            iconic=bool(win32gui.IsIconic(hwnd)),
            pid=pid,
            executable=_process_executable(pid),
        ))

    win32gui.EnumWindows(callback, None)
    return found


def resolve_window(
    hwnd: int | None = None,
    index: int = 0,
    title: str | None = None,
) -> GameWindow:
    windows = game_windows()
    if hwnd is not None:
        for window in windows:
            if window.hwnd == hwnd:
                return window
    if title:
        expected = title.casefold().strip()
        exact = [window for window in windows if window.title.casefold() == expected]
        if exact:
            return exact[0]
        partial = [window for window in windows if expected in window.title.casefold()]
        if len(partial) == 1:
            return partial[0]
        raise RuntimeError(f"клієнт із d_title «{title}» не запущено")
    if hwnd is not None:
        raise RuntimeError(f"вікно {hwnd} не знайдено")
    if index >= len(windows):
        raise RuntimeError(f"вікно PW №{index + 1} не знайдено")
    return windows[index]
