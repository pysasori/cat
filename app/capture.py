from __future__ import annotations

import ctypes

import win32con
import win32gui
import win32ui
from PIL import Image

_user32 = ctypes.windll.user32
_PW_CLIENTONLY = 0x1
_PW_RENDERFULLCONTENT = 0x2


class WindowCapture:
    def __init__(self, hwnd: int) -> None:
        self.hwnd = hwnd

    def grab(self) -> Image.Image:
        if not win32gui.IsWindow(self.hwnd):
            raise RuntimeError("вікно гри закрите")
        if win32gui.IsIconic(self.hwnd):
            raise RuntimeError("вікно гри згорнуте")
        image = self._grab(False)
        if image.convert("L").getextrema()[1] <= 8:
            image = self._grab(True)
        return image

    def grab_screen(self) -> Image.Image:
        """Capture the visible client, including PW tooltips omitted by BitBlt."""
        import pyautogui

        left, top = win32gui.ClientToScreen(self.hwnd, (0, 0))
        _, _, width, height = win32gui.GetClientRect(self.hwnd)
        return pyautogui.screenshot(region=(left, top, width, height)).convert("RGB")

    def _grab(self, print_window: bool) -> Image.Image:
        _, _, width, height = win32gui.GetClientRect(self.hwnd)
        hwnd_dc = win32gui.GetDC(self.hwnd)
        source = win32ui.CreateDCFromHandle(hwnd_dc)
        target = source.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source, width, height)
        target.SelectObject(bitmap)
        try:
            if print_window:
                _user32.PrintWindow(
                    self.hwnd,
                    target.GetSafeHdc(),
                    _PW_CLIENTONLY | _PW_RENDERFULLCONTENT,
                )
            else:
                target.BitBlt((0, 0), (width, height), source, (0, 0), win32con.SRCCOPY)
            info = bitmap.GetInfo()
            return Image.frombuffer(
                "RGB",
                (info["bmWidth"], info["bmHeight"]),
                bitmap.GetBitmapBits(True),
                "raw",
                "BGRX",
                0,
                1,
            )
        finally:
            win32gui.DeleteObject(bitmap.GetHandle())
            target.DeleteDC()
            source.DeleteDC()
            win32gui.ReleaseDC(self.hwnd, hwnd_dc)
