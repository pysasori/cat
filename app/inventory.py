from __future__ import annotations

import io
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image, ImageOps

from app.capture import WindowCapture
from app.input import InputDriver
from app.models import Grid, Point
from app.vision import Match, crop_icon, icon_similarity, occupied_grid_indexes


TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


@dataclass(frozen=True)
class InventoryStack:
    match: Match
    quantity: int


def extract_count(text: str) -> int:
    values = re.findall(r"\((\d{1,6})\)", text)
    # The first pair is the stack count; a later pair can be the total price.
    return int(values[0]) if values else 1


def tooltip_count(capture: WindowCapture, match: Match) -> int:
    """Read `(quantity)` from the first tooltip line; no suffix means a singleton."""
    if not TESSERACT.exists():
        raise RuntimeError("Tesseract OCR не знайдено в C:\\Program Files\\Tesseract-OCR")
    x, y = match.point.x, match.point.y
    for attempt in range(3):
        frame = capture.grab_screen()
        right = min(frame.width, x + 400)
        bottom = min(frame.height, y + 70)
        # Different PW items shift the tooltip title by a few pixels. The narrow
        # strip is cleanest; wider/raised strips recover titles that moved up.
        for top_offset in (20, 10, -15):
            top = max(0, y + top_offset)
            crop = frame.crop((x, top, right, bottom)).resize(
                (max(1, right - x) * 4, max(1, bottom - top) * 4),
                # Pixel UI font OCR is markedly clearer without antialiasing.
                Image.Resampling.NEAREST,
            )
            buffer = io.BytesIO()
            crop.save(buffer, format="PNG")
            result = subprocess.run(
                [str(TESSERACT), "stdin", "stdout", "--psm", "6", "-l", "eng+rus"],
                input=buffer.getvalue(),
                capture_output=True,
                timeout=8,
                check=False,
            )
            text = result.stdout.decode("utf-8", errors="ignore")
            values = re.findall(r"\((\d{1,6})\)", text)
            if values:
                return int(values[0])
        if attempt < 2:
            time.sleep(0.22)
    return 1


def dialog_quantity_count(frame: Image.Image, point: Point) -> int:
    """OCR the value shown in PW's quantity edit after pressing Maximum."""
    if not TESSERACT.exists():
        raise RuntimeError("Tesseract OCR не знайдено в C:\\Program Files\\Tesseract-OCR")
    # Keep only the dark edit interior. Including its beveled border makes
    # Tesseract drop the last digit of PW's tiny bitmap font.
    crop = frame.crop((point.x - 25, point.y - 10, point.x + 25, point.y + 9)).convert("L")
    # Trim the empty right side of the edit. Tesseract otherwise ignores a
    # lone PW bitmap glyph (for example Maximum=9) as insignificant noise.
    glyph_box = crop.point(lambda value: 255 if value > 90 else 0).getbbox()
    if glyph_box is None:
        raise RuntimeError("поле кількості після «Максимум» порожнє")
    left, top, right, bottom = glyph_box
    crop = crop.crop(
        (
            max(0, left - 2),
            max(0, top - 2),
            min(crop.width, right + 2),
            min(crop.height, bottom + 2),
        )
    )
    crop = crop.resize((crop.width * 15, crop.height * 15), Image.Resampling.LANCZOS)
    crop = ImageOps.expand(crop, border=50, fill=0)
    payload = io.BytesIO()
    crop.save(payload, format="PNG")
    values: list[int] = []
    for psm in (6, 7, 8, 10, 13):
        result = subprocess.run(
            [
                str(TESSERACT),
                "stdin",
                "stdout",
                "--psm",
                str(psm),
                "-l",
                "eng",
                "-c",
                "tessedit_char_whitelist=0123456789",
            ],
            input=payload.getvalue(),
            capture_output=True,
            timeout=8,
            check=False,
        )
        digits = re.sub(r"\D", "", result.stdout.decode("utf-8", errors="ignore"))
        if digits:
            values.append(int(digits))
    if not values:
        raise RuntimeError("не вдалося прочитати кількість після натискання «Максимум»")
    # Require two OCR layouts to agree; a single hallucinated digit must never
    # decide how much stock is sold.
    quantity = max(set(values), key=values.count)
    if values.count(quantity) < 2 or quantity < 1 or quantity > 999_999:
        raise RuntimeError(f"неоднозначна кількість після «Максимум»: {values}")
    return quantity


def dialog_quantity_signature(frame: Image.Image, point: Point) -> tuple[int, int, bytes]:
    """Return a caret-insensitive bitmap signature of the quantity field."""
    crop = frame.crop((point.x - 25, point.y - 10, point.x + 25, point.y + 9)).convert("L")
    mask = (np.asarray(crop, dtype=np.uint8) > 90).astype(np.uint8)
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = np.zeros_like(mask)
    for label in range(1, count):
        _x, _y, width, height, area = stats[label]
        # The focused PW edit draws a narrow blinking caret after the digits.
        if width <= 2 and height >= 7:
            continue
        if area >= 2:
            cleaned[labels == label] = 1
    ys, xs = np.where(cleaned)
    if not len(xs):
        return (0, 0, b"")
    glyphs = cleaned[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    return glyphs.shape[1], glyphs.shape[0], glyphs.tobytes()


PW_DIGITS = {
    "0": (".####.", ".#..#.", "##..#.", "##..##", "##..##", "##..##", "##..#.", ".#..#.", ".####."),
    "1": ("..#", ".##", "#.#", "..#", "..#", "..#", "..#", "..#", "..#"),
    "2": (".####.", "##..#.", "....#.", "....#.", "...##.", "..##..", ".##...", ".#....", "######"),
    "3": (".####.", "##..#.", "....#.", "...##.", "..###.", "....#.", "....##", "##..#.", ".####."),
    "4": ("...##", "..###", "..###", ".####", ".#.##", "##.##", "#####", "...##", "...##"),
    "5": (".####.", ".#....", ".#....", ".####.", ".#..#.", "....##", "....##", "##..#.", ".####."),
    "6": (".####.", ".#..#.", "##....", "#####.", "##..#.", "##..##", "##..##", ".#..#.", ".####."),
    "7": ("######", "....#.", "...#..", "...#..", "..##..", "..#...", "..#...", ".##...", ".##..."),
    "8": (".####.", ".#..#.", ".#..#.", ".####.", ".####.", "##..#.", "##..##", "##..#.", ".####."),
    "9": (".####.", "##..#.", "##..##", "##..##", ".#.###", ".#####", "....#.", "##..#.", ".###.."),
}


def dialog_quantity_pixels(frame: Image.Image, point: Point) -> int:
    """Read PW's fixed bitmap digits without Tesseract or language data."""
    width, height, payload = dialog_quantity_signature(frame, point)
    if height != 9 or width < 1:
        raise RuntimeError("не вдалося виділити піксельні цифри кількості")
    mask = np.frombuffer(payload, dtype=np.uint8).reshape(height, width)
    digits: list[str] = []
    right = width
    while right > 0:
        left = max(0, right - 6)
        glyph = mask[:, left:right]
        used = np.where(glyph.any(axis=0))[0]
        if not len(used):
            right = left
            continue
        glyph = glyph[:, used.min() : used.max() + 1]
        rows = tuple("".join("#" if value else "." for value in row) for row in glyph)
        digit = next((value for value, template in PW_DIGITS.items() if template == rows), None)
        if digit is None:
            raise RuntimeError(f"невідома піксельна цифра {glyph.shape[1]}x{glyph.shape[0]}")
        digits.append(digit)
        right = left
    if not digits:
        raise RuntimeError("поле кількості порожнє")
    return int("".join(reversed(digits)))


def scan_templates(
    capture: WindowCapture,
    driver: InputDriver,
    templates: dict[str, Image.Image],
    grid: Grid,
    threshold: float,
    wait: Callable[[float], None] = time.sleep,
    tooltip_driver: InputDriver | None = None,
    skip_quantity_for: set[str] | None = None,
) -> dict[str, list[InventoryStack]]:
    """Assign each occupied bag cell to its closest profile icon, then OCR its stack."""
    # A tooltip left over from a previous scan can cover lower bag cells in the
    # very first frame. Park the cursor over the world before finding icons.
    driver.move(max(10, grid.first.x - 250), grid.first.y)
    wait(0.45)
    frame = capture.grab()
    result = {item_id: [] for item_id in templates}
    candidates: list[tuple[str, Match]] = []
    retry_singletons: list[tuple[str, Match]] = []
    for index in occupied_grid_indexes(frame, grid):
        point = grid.point(index)
        cell = crop_icon(frame, point.x, point.y, grid.icon_size)
        scores = sorted(
            (
                (icon_similarity(template, cell, True), item_id)
                for item_id, template in templates.items()
            ),
            reverse=True,
        )
        runner_up = scores[1][0] if len(scores) > 1 else 0.0
        # Unknown inventory items must not be forced into the closest profile
        # template. Real copies score close to 1.0 after frame/count masking;
        # the old 0.60 floor classified unrelated loot as configured goods.
        minimum = max(0.88, threshold)
        if scores and scores[0][0] >= minimum and scores[0][0] - runner_up >= 0.02:
            candidates.append((scores[0][1], Match(index=index, point=point, score=scores[0][0])))
    hover_driver = tooltip_driver or driver
    skip_quantity_for = skip_quantity_for or set()
    for item_id, match in candidates:
        if match.index >= grid.count // 2:
            # The lower 16 cells are a protected sample bank. Samples are
            # created as singletons and are never counted as sale stock.
            result[item_id].append(InventoryStack(match=match, quantity=1))
            continue
        if item_id in skip_quantity_for:
            # Two-sided items are measured from the sale dialog's exact
            # Maximum value. Tooltips are too slow and occasionally stale.
            result[item_id].append(InventoryStack(match=match, quantity=1))
            continue
        # Leave the bag first. Moving directly between adjacent identical icons
        # can leave PW's old tooltip cached even after a long delay.
        hover_driver.move(max(10, grid.first.x - 250), grid.first.y)
        wait(0.50)
        hover_driver.move(match.point.x, match.point.y)
        # This PW client uses a long tooltip delay. Reading sooner can silently
        # reuse the previous item's tooltip or turn a large stack into 1.
        wait(5.00)
        quantity = tooltip_count(capture, match)
        if quantity == 1:
            retry_singletons.append((item_id, match))
        result[item_id].append(InventoryStack(match=match, quantity=quantity))
    # PW can refuse to replace one tooltip during the first sweep. A separate
    # second sweep reliably refreshes those suspicious top-row singletons.
    for item_id, match in retry_singletons:
        hover_driver.move(max(10, grid.first.x - 250), grid.first.y)
        wait(0.50)
        hover_driver.move(match.point.x, match.point.y)
        wait(5.00)
        quantity = tooltip_count(capture, match)
        if quantity > 1:
            result[item_id] = [
                InventoryStack(match=stack.match, quantity=quantity)
                if stack.match.index == match.index
                else stack
                for stack in result[item_id]
            ]
    return result
