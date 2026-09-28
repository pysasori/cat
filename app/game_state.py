from __future__ import annotations

import io
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

from PIL import Image, ImageEnhance, ImageOps

from app.inventory import TESSERACT
from app.models import Point


@dataclass(frozen=True)
class DetectedGameState:
    character_name: str | None
    free_funds: int | None
    detected_at: str

    def as_dict(self) -> dict:
        return {
            "character_name": self.character_name,
            "free_funds": self.free_funds,
            "detected_at": self.detected_at,
        }


def _ocr(image: Image.Image, psm: int = 7, whitelist: str | None = None) -> str:
    if not TESSERACT.exists():
        raise RuntimeError("Tesseract OCR не знайдено")
    prepared = ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(2.2)
    prepared = prepared.resize((prepared.width * 5, prepared.height * 5), Image.Resampling.LANCZOS)
    payload = io.BytesIO()
    prepared.save(payload, format="PNG")
    command = [str(TESSERACT), "stdin", "stdout", "--psm", str(psm), "-l", "eng"]
    if whitelist:
        command.extend(["-c", f"tessedit_char_whitelist={whitelist}"])
    result = subprocess.run(command, input=payload.getvalue(), capture_output=True, timeout=8, check=False)
    return result.stdout.decode("utf-8", errors="ignore")


def detect_character_name(frame: Image.Image) -> str | None:
    # Name below the top-left HP/MP portrait in the 1440x1080 client layout.
    text = _ocr(frame.crop((95, 48, 260, 98)), psm=6)
    candidates = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,31}", text)
    ignored = {"exp", "hp", "mp"}
    candidates = [value for value in candidates if value.lower() not in ignored]
    return max(candidates, key=len, default=None)


def _money_from_crop(frame: Image.Image, box: tuple[int, int, int, int]) -> int | None:
    text = _ocr(frame.crop(box), psm=7, whitelist="0123456789,. ")
    values = []
    for token in re.findall(r"[\d][\d,. ]{1,20}", text):
        digits = re.sub(r"\D", "", token)
        if digits:
            values.append(int(digits))
    return max(values, default=None)


def detect_free_funds(
    frame: Image.Image,
    shop_anchor: Point | None = None,
    bag_anchor: Point | None = None,
) -> int | None:
    # Prefer panel-relative money fields. The panels are draggable and a stack
    # tooltip can cover the backpack footer after inventory scanning.
    if shop_anchor is not None:
        value = _money_from_crop(
            frame,
            (
                shop_anchor.x + 59,
                shop_anchor.y + 195,
                shop_anchor.x + 264,
                shop_anchor.y + 220,
            ),
        )
        if value is not None:
            return value
    if bag_anchor is not None:
        value = _money_from_crop(
            frame,
            (
                # +55 included the edge of the coin icon in some layouts;
                # Tesseract could hallucinate it as a leading digit.
                bag_anchor.x + 58,
                bag_anchor.y + 410,
                bag_anchor.x + 150,
                bag_anchor.y + 440,
            ),
        )
        if value is not None:
            return value
    # Legacy crops are only fallbacks for callers without detected anchors.
    # Mixing them with precise fields and taking max() can turn OCR noise into
    # a believable but much larger balance.
    boxes = ((15, 420, 430, 510), (835, 665, 1140, 720))
    values = [_money_from_crop(frame, box) for box in boxes]
    return max((value for value in values if value is not None), default=None)


def detect_game_state(frame: Image.Image) -> DetectedGameState:
    return DetectedGameState(
        character_name=detect_character_name(frame),
        free_funds=detect_free_funds(frame),
        detected_at=datetime.now(timezone.utc).isoformat(),
    )
