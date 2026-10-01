from PIL import Image, ImageDraw

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


def test_two_sided_top_stack_skips_unreliable_tooltip(monkeypatch):
    grid = AppConfig().geometry.bag_grid
    frame = Image.new("RGB", (1440, 1080), "black")
    background = Driver()
    foreground = Driver()
    monkeypatch.setattr(inventory, "occupied_grid_indexes", lambda *_: [0])
    monkeypatch.setattr(inventory, "icon_similarity", lambda *_: 0.99)
    monkeypatch.setattr(
        inventory,
        "tooltip_count",
        lambda *_: (_ for _ in ()).throw(AssertionError("tooltip must not be read")),
    )

    result = scan_templates(
        Capture(frame),
        background,
        {"loot": Image.new("RGB", (26, 26), "white")},
        grid,
        0.8,
        wait=lambda _: None,
        tooltip_driver=foreground,
        skip_quantity_for={"loot"},
    )

    assert result["loot"][0].quantity == 1
    assert foreground.moves == []


def test_inventory_identifies_loot_from_upper_icon_half(monkeypatch):
    grid = AppConfig().geometry.bag_grid
    frame = Image.new("RGB", (1440, 1080), "black")
    upper_half_flags = []

    def similarity(_template, _cell, upper_half=False):
        upper_half_flags.append(upper_half)
        return 0.99

    monkeypatch.setattr(inventory, "occupied_grid_indexes", lambda *_: [16])
    monkeypatch.setattr(inventory, "icon_similarity", similarity)

    result = scan_templates(
        Capture(frame),
        Driver(),
        {"loot": Image.new("RGB", (26, 26), "white")},
        grid,
        0.8,
        wait=lambda _: None,
    )

    assert result["loot"]
    assert upper_half_flags == [True]


def test_inventory_does_not_force_unrelated_item_into_closest_template(monkeypatch):
    grid = AppConfig().geometry.bag_grid
    frame = Image.new("RGB", (1440, 1080), "black")
    monkeypatch.setattr(inventory, "occupied_grid_indexes", lambda *_: [0])
    monkeypatch.setattr(inventory, "icon_similarity", lambda *_: 0.86)

    result = scan_templates(
        Capture(frame),
        Driver(),
        {"loot": Image.new("RGB", (26, 26), "white")},
        grid,
        0.60,
        wait=lambda _: None,
    )

    assert result["loot"] == []


def test_quantity_signature_ignores_blinking_caret():
    point = AppConfig().geometry.dialog_quantity
    plain = Image.new("RGB", (1440, 1080), "black")
    focused = plain.copy()
    ImageDraw.Draw(plain).rectangle(
        (point.x - 18, point.y - 5, point.x - 14, point.y + 4), fill="white"
    )
    ImageDraw.Draw(focused).rectangle(
        (point.x - 18, point.y - 5, point.x - 14, point.y + 4), fill="white"
    )
    ImageDraw.Draw(focused).line(
        (point.x + 4, point.y - 5, point.x + 4, point.y + 4), fill="white"
    )

    assert inventory.dialog_quantity_signature(plain, point) == inventory.dialog_quantity_signature(
        focused, point
    )


def test_pixel_quantity_reader_recognizes_live_pw_100_bitmap():
    point = AppConfig().geometry.dialog_quantity
    frame = Image.new("RGB", (1440, 1080), "black")
    rows = (
        "..#...####..####.",
        ".##...#..#..#..#.",
        "#.#..##..#.##..#.",
        "..#..##..####..##",
        "..#..##..####..##",
        "..#..##..####..##",
        "..#..##..#.##..#.",
        "..#...#..#..#..#.",
        "..#...####..####.",
    )
    draw = ImageDraw.Draw(frame)
    start_x, start_y = point.x - 20, point.y - 5
    for y, row in enumerate(rows):
        for x, value in enumerate(row):
            if value == "#":
                draw.point((start_x + x, start_y + y), fill="white")

    assert inventory.dialog_quantity_pixels(frame, point) == 100
