import pytest

from app import windows as windows_module
from app.windows import GameWindow, resolve_window


def test_stale_hwnd_is_rebound_by_unique_window_title(monkeypatch):
    wanted = GameWindow(222, "Ellnalise", 1440, 1080, False, 2)
    monkeypatch.setattr(
        windows_module,
        "game_windows",
        lambda: [GameWindow(111, "Other", 1440, 1080, False, 1), wanted],
    )

    assert resolve_window(999, 0, "Ellnalise") == wanted


def test_named_character_never_falls_back_to_another_window(monkeypatch):
    monkeypatch.setattr(
        windows_module,
        "game_windows",
        lambda: [GameWindow(111, "Other", 1440, 1080, False, 1)],
    )

    with pytest.raises(RuntimeError, match="d_title.*Ellnalise"):
        resolve_window(None, 0, "Ellnalise")
