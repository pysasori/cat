from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageStat

from app.models import Grid, Point


@dataclass(frozen=True)
class Match:
    index: int
    point: Point
    score: float


def crop_icon(image: Image.Image, x: int, y: int, size: int = 26) -> Image.Image:
    half = size // 2
    return image.crop((x - half, y - half, x - half + size, y - half + size)).convert("RGB")


def normalize_icon(image: Image.Image) -> Image.Image:
    """Remove the 3 px frame used by 32x32 ComebackPW icons."""
    icon = image.convert("RGB")
    if icon.size == (32, 32):
        return icon.crop((3, 3, 29, 29))
    if icon.size != (26, 26):
        return icon.resize((26, 26))
    return icon


def icon_similarity(
    first: Image.Image,
    second: Image.Image,
    upper_half: bool = False,
) -> float:
    """Compare icons despite stack digits, highlighting and a tiny pixel shift."""
    a = np.asarray(normalize_icon(first), dtype=np.float32) / 255.0
    b = np.asarray(normalize_icon(second), dtype=np.float32) / 255.0
    base_mask = np.ones((26, 26), dtype=bool)
    if upper_half:
        # Large stack counts can cover most of the lower icon. Inventory
        # discovery therefore identifies loot only from the unobstructed top.
        base_mask[13:26, :] = False
    else:
        base_mask[17:26, 0:14] = False
    best = -1.0
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            shifted = np.roll(b, (dy, dx), axis=(0, 1))
            mask = base_mask.copy()
            if dy > 0:
                mask[:dy, :] = False
            elif dy < 0:
                mask[dy:, :] = False
            if dx > 0:
                mask[:, :dx] = False
            elif dx < 0:
                mask[:, dx:] = False
            left = a[mask]
            right = shifted[mask]
            left = (left - left.mean(axis=0)) / (left.std(axis=0) + 1e-6)
            right = (right - right.mean(axis=0)) / (right.std(axis=0) + 1e-6)
            best = max(best, float((left * right).mean()))
    return max(0.0, min(1.0, (best + 1.0) / 2.0))


def search_grid(frame: Image.Image, template: Image.Image, grid: Grid) -> list[Match]:
    matches = []
    for index in range(grid.count):
        point = grid.point(index)
        candidate = crop_icon(frame, point.x, point.y, grid.icon_size)
        matches.append(Match(index=index, point=point, score=icon_similarity(template, candidate)))
    return sorted(matches, key=lambda item: item.score, reverse=True)


def occupied_grid_indexes(frame: Image.Image, grid: Grid) -> list[int]:
    """Return textured cells while ignoring repeated empty/locked slot backgrounds."""
    tiles = {
        index: np.asarray(
            crop_icon(frame, grid.point(index).x, grid.point(index).y, grid.icon_size),
            dtype=np.float32,
        )
        for index in range(grid.count)
    }
    occupied = []
    for index, tile in tiles.items():
        neighbours = 0
        for other_index, other in tiles.items():
            if other_index == index:
                continue
            similarity = 1.0 - float(np.mean(np.abs(tile - other))) / 255.0
            if similarity >= 0.94:
                neighbours += 1
        # Empty cells and red locked cells form large identical clusters.
        # A real item is textured and normally unique (or occurs only twice).
        if neighbours < 3 and float(tile.std()) >= 12.0:
            occupied.append(index)
    return occupied


def find_empty_cell(
    frame: Image.Image,
    grid: Grid,
    excluded: set[int] | None = None,
    allowed: set[int] | None = None,
) -> Match | None:
    """Find an empty bag slot by locating the largest cluster of near-identical cells."""
    excluded = excluded or set()
    indexes = [
        index
        for index in range(grid.count)
        if index not in excluded and (allowed is None or index in allowed)
    ]
    if not indexes:
        return None

    tiles = {
        index: np.asarray(
            crop_icon(frame, grid.point(index).x, grid.point(index).y, grid.icon_size),
            dtype=np.float32,
        )
        for index in indexes
    }
    candidates: list[tuple[int, float, float, int]] = []
    for index, tile in tiles.items():
        similarities = []
        for other_index, other in tiles.items():
            if other_index == index:
                continue
            similarities.append(1.0 - float(np.mean(np.abs(tile - other))) / 255.0)
        neighbours = sum(value >= 0.94 for value in similarities)
        cluster_score = sum(sorted(similarities, reverse=True)[: min(8, len(similarities))])
        brightness = float(tile.mean())
        candidates.append((neighbours, cluster_score, -brightness, index))

    neighbours, cluster_score, _darkness, index = max(candidates)
    # At least two matching neighbours avoids treating a repeated pair of items as empty.
    if neighbours < 2:
        return None
    selected = tiles[index]
    close_scores = [
        1.0 - float(np.mean(np.abs(selected - other))) / 255.0
        for other_index, other in tiles.items()
        if other_index != index
        and 1.0 - float(np.mean(np.abs(selected - other))) / 255.0 >= 0.94
    ]
    confidence = sum(close_scores) / len(close_scores)
    return Match(index=index, point=grid.point(index), score=confidence)


def looks_like_shop(frame: Image.Image, probe: Point) -> bool:
    """Шукаємо темну панель із золотим заголовком біля точки назви «Лавка»."""
    box = frame.crop((probe.x - 150, probe.y - 22, probe.x + 150, probe.y + 22)).convert("RGB")
    pixels = np.asarray(box, dtype=np.uint8)
    gold = (
        (pixels[:, :, 0] > 120)
        & (pixels[:, :, 1] > 85)
        & (pixels[:, :, 1] < 220)
        & (pixels[:, :, 2] < 100)
    )
    dark = pixels.mean(axis=2) < 75
    return int(gold.sum()) >= 20 and float(dark.mean()) > 0.22


def looks_like_bag(frame: Image.Image, probe: Point) -> bool:
    box = frame.crop((probe.x - 130, probe.y - 22, probe.x + 130, probe.y + 22)).convert("RGB")
    pixels = np.asarray(box, dtype=np.uint8)
    gold = (
        (pixels[:, :, 0] > 110)
        & (pixels[:, :, 1] > 80)
        & (pixels[:, :, 2] < 110)
    )
    dark = pixels.mean(axis=2) < 85
    return int(gold.sum()) >= 20 and float(dark.mean()) > 0.18


def image_changed(before: Image.Image, after: Image.Image, box: tuple[int, int, int, int]) -> float:
    diff = ImageChops.difference(before.crop(box).convert("RGB"), after.crop(box).convert("RGB"))
    return sum(ImageStat.Stat(diff).mean) / 3.0


def load_icon(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGB")
