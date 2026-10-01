import win32con

from app.input import key_lparam


def test_down_arrow_uses_extended_key_flag():
    down = key_lparam(win32con.VK_DOWN, True)
    letter = key_lparam(ord("A"), True)

    assert down & (1 << 24)
    assert not letter & (1 << 24)
