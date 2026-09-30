from PIL import Image, ImageDraw

from app.models import Grid, Point
from app.vision import (
    crop_icon,
    find_empty_cell,
    icon_similarity,
    normalize_icon,
    occupied_grid_indexes,
    search_grid,
)


def icon(color: tuple[int, int, int], mark: str = "x") -> Image.Image:
    image = Image.new("RGB", (26, 26), (12, 16, 20))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 3, 21, 20), fill=color)
    draw.line((4, 4, 20, 20), fill=(240, 240, 240), width=2)
    if mark == "number":
        draw.rectangle((0, 18, 11, 25), fill=(255, 255, 255))
    return image


def test_similarity_ignores_stack_number_corner():
    base = icon((190, 30, 80))
    numbered = base.copy()
    ImageDraw.Draw(numbered).rectangle((0, 18, 11, 25), fill=(255, 255, 255))
    assert icon_similarity(base, numbered) > 0.98


def test_similarity_tolerates_small_render_shift():
    base = icon((190, 70, 30))
    shifted = Image.new("RGB", (26, 26), (12, 16, 20))
    shifted.paste(base, (1, -1))
    assert icon_similarity(base, shifted) > 0.90


def test_upper_half_similarity_ignores_large_stack_count_overlay():
    base = icon((70, 120, 230))
    covered = base.copy()
    ImageDraw.Draw(covered).rectangle((0, 13, 25, 25), fill=(250, 250, 250))

    assert icon_similarity(base, covered, upper_half=True) > 0.98
    assert icon_similarity(base, covered) < 0.90


def test_normalize_icon_removes_comebackpw_frame():
    inner = icon((70, 120, 230))
    framed = Image.new("RGB", (32, 32), (40, 65, 100))
    framed.paste(inner, (3, 3))

    assert normalize_icon(framed).size == (26, 26)
    assert icon_similarity(inner, framed) > 0.99


def test_searches_every_bag_cell_and_returns_best():
    frame = Image.new("RGB", (180, 100), (5, 8, 10))
    grid = Grid(first=Point(x=30, y=30), step=Point(x=35, y=35), columns=4, rows=2)
    wanted = icon((70, 120, 230))
    other = icon((220, 80, 30))
    for index in range(grid.count):
        point = grid.point(index)
        tile = wanted if index == 6 else other
        frame.paste(tile, (point.x - 13, point.y - 13))
    best = search_grid(frame, wanted, grid)[0]
    assert best.index == 6
    assert best.score > 0.99


def test_crop_icon_is_centered():
    frame = Image.new("RGB", (100, 100), "black")
    frame.paste(icon((20, 220, 90)), (37, 37))
    assert crop_icon(frame, 50, 50).getpixel((13, 13))[1] > 150


def test_find_empty_cell_uses_repeated_empty_slot_pattern():
    frame = Image.new("RGB", (180, 100), (5, 8, 10))
    grid = Grid(first=Point(x=30, y=30), step=Point(x=35, y=35), columns=4, rows=2)
    empty = Image.new("RGB", (26, 26), (18, 22, 26))
    for index in range(grid.count):
        point = grid.point(index)
        tile = icon((30 + index * 20, 80, 180)) if index < 3 else empty
        frame.paste(tile, (point.x - 13, point.y - 13))
    match = find_empty_cell(frame, grid, {0})
    assert match is not None
    assert match.index >= 3
    assert match.score > 0.98


def test_occupied_grid_indexes_ignores_empty_and_locked_slot_clusters():
    frame = Image.new("RGB", (220, 150), (5, 8, 10))
    grid = Grid(first=Point(x=30, y=30), step=Point(x=35, y=35), columns=5, rows=4)
    empty = Image.new("RGB", (26, 26), (18, 22, 26))
    locked = icon((180, 25, 30))
    for index in range(grid.count):
        point = grid.point(index)
        if index < 3:
            tile = icon((40 + index * 60, 100, 190))
        elif index < 12:
            tile = empty
        else:
            tile = locked
        frame.paste(tile, (point.x - 13, point.y - 13))

    assert occupied_grid_indexes(frame, grid) == [0, 1, 2]
