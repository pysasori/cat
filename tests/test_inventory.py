from PIL import Image

from app import inventory
from app.inventory import scan_templates
from app.models import AppConfig


class Capture:
    def __init__(self, frame):
        self.frame = frame

    def grab(self):
        return self.frame


class Driver:
    def __init__(self):
        self.moves = []

    def move(self, x, y):
        self.moves.append((x, y))


def test_lower_sample_scan_never_uses_foreground_tooltip_driver(monkeypatch):
    grid = AppConfig().geometry.bag_grid
    frame = Image.new("RGB", (1440, 1080), "black")
    background = Driver()
    foreground = Driver()
    monkeypatch.setattr(inventory, "occupied_grid_indexes", lambda *_: [16])
    monkeypatch.setattr(inventory, "icon_similarity", lambda *_: 0.99)

    result = scan_templates(
        Capture(frame),
        background,
        {"loot": Image.new("RGB", (26, 26), "white")},
        grid,
        0.8,
        wait=lambda _: None,
        tooltip_driver=foreground,
    )

    assert result["loot"][0].quantity == 1
    assert len(background.moves) == 1
    assert foreground.moves == []


def test_top_stack_uses_separate_tooltip_driver(monkeypatch):
    grid = AppConfig().geometry.bag_grid
    frame = Image.new("RGB", (1440, 1080), "black")
    background = Driver()
    foreground = Driver()
    monkeypatch.setattr(inventory, "occupied_grid_indexes", lambda *_: [0])
    monkeypatch.setattr(inventory, "icon_similarity", lambda *_: 0.99)
    monkeypatch.setattr(inventory, "tooltip_count", lambda *_: 37)

    result = scan_templates(
        Capture(frame),
        background,
        {"loot": Image.new("RGB", (26, 26), "white")},
        grid,
        0.8,
        wait=lambda _: None,
        tooltip_driver=foreground,
    )

    assert result["loot"][0].quantity == 37
    assert len(background.moves) == 1
    assert len(foreground.moves) == 2
