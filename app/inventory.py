from __future__ import annotations

import io
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image

from app.capture import WindowCapture
from app.input import InputDriver
from app.models import Grid
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


def scan_templates(
    capture: WindowCapture,
    driver: InputDriver,
    templates: dict[str, Image.Image],
    grid: Grid,
    threshold: float,
    wait: Callable[[float], None] = time.sleep,
    tooltip_driver: InputDriver | None = None,
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
            ((icon_similarity(template, cell), item_id) for item_id, template in templates.items()),
            reverse=True,
        )
        runner_up = scores[1][0] if len(scores) > 1 else 0.0
        minimum = max(0.60, threshold - 0.10)
        if scores and scores[0][0] >= minimum and scores[0][0] - runner_up >= 0.02:
            candidates.append((scores[0][1], Match(index=index, point=point, score=scores[0][0])))
    hover_driver = tooltip_driver or driver
    for item_id, match in candidates:
        if match.index >= grid.count // 2:
            # The lower 16 cells are a protected sample bank. Samples are
            # created as singletons and are never counted as sale stock.
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
