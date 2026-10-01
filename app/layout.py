from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from app.models import Geometry, Point


TEMPLATES = Path(__file__).resolve().parent / "assets" / "templates"
DEFAULT_SHOP_ANCHOR = Point(x=147, y=274)  # centre of the "Продажа" template
DEFAULT_BAG_ANCHOR = Point(x=985, y=265)  # centre of the "Рюкзак" template
SUPPORTED_CLIENT_SIZES = {(1440, 1080), (1280, 720)}


@lru_cache(maxsize=8)
def _template(name: str) -> np.ndarray:
    path = TEMPLATES / name
    data = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if data is None:
        raise RuntimeError(f"Не вдалося завантажити шаблон інтерфейсу {name}")
    return data


def _find(
    frame: Image.Image,
    name: str,
    threshold: float,
    validator=None,
) -> tuple[Point | None, float]:
    template = _template(name)
    gray = cv2.cvtColor(np.asarray(frame.convert("RGB")), cv2.COLOR_RGB2GRAY)
    if gray.shape[0] < template.shape[0] or gray.shape[1] < template.shape[1]:
        return None, 0.0
    result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    height, width = template.shape[:2]
    # Do not blindly trust the global maximum. Animated world/UI text can
    # occasionally resemble a short label. Consider every strong candidate
    # and reject anchors whose full panel would lie outside the client.
    ys, xs = np.where(result >= threshold)
    if not len(xs):
        _, score, _, _ = cv2.minMaxLoc(result)
        return None, float(score)
    candidates = sorted(
        (
            (float(result[y, x]), Point(x=int(x + width // 2), y=int(y + height // 2)))
            for y, x in zip(ys, xs)
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    for score, point in candidates:
        if validator is None or validator(point, frame.width, frame.height):
            return point, score
    return None, candidates[0][0]


def _point_inside(point: Point, width: int, height: int, margin: int = 16) -> bool:
    return margin <= point.x < width - margin and margin <= point.y < height - margin


def _shop_anchor_fits(anchor: Point, width: int, height: int) -> bool:
    geometry = geometry_for_frame(Geometry(), shop_anchor=anchor)
    points = [
        geometry.sale_grid.point(0),
        geometry.sale_grid.point(geometry.sale_grid.count - 1),
        geometry.buy_grid.point(0),
        geometry.buy_grid.point(geometry.buy_grid.count - 1),
        geometry.shop_name,
        geometry.return_button,
        geometry.ok_button,
        geometry.cancel_button,
        geometry.offline_button,
    ]
    return all(_point_inside(point, width, height) for point in points)


def _bag_anchor_fits(anchor: Point, width: int, height: int) -> bool:
    geometry = geometry_for_frame(Geometry(), bag_anchor=anchor)
    return all(
        _point_inside(point, width, height)
        for point in (
            geometry.bag_grid.point(0),
            geometry.bag_grid.point(geometry.bag_grid.count - 1),
        )
    )


def locate_shop(frame: Image.Image) -> Point | None:
    # The title can be partly covered by the bag.  "Продажа" stays beside the
    # lot grid and is much less likely to be hidden.  At 0.85 chat text does not
    # produce false positives (the live template scores around 0.99).
    return _find(frame, "sell_panel.png", 0.85, _shop_anchor_fits)[0]


def locate_bag(frame: Image.Image) -> Point | None:
    return _find(frame, "bag_title.png", 0.80, _bag_anchor_fits)[0]


def _shift(point: Point, dx: int, dy: int) -> Point:
    return Point(x=point.x + dx, y=point.y + dy)


def geometry_for_client(base: Geometry, width: int, height: int) -> Geometry:
    """Adapt centred dialogs while keeping draggable panels anchor-relative."""
    if (width, height) not in SUPPORTED_CLIENT_SIZES:
        supported = ", ".join(f"{w}x{h}" for w, h in sorted(SUPPORTED_CLIENT_SIZES))
        raise RuntimeError(f"непідтримуваний розмір гри {width}x{height}; доступні {supported}")
    reference = Geometry()
    if (width, height) == (1280, 720):
        # Live ComebackPW 1280x720 dialogs sit higher than a simple centred
        # translation of the 1440x1080 layout. These points were measured from
        # the sale-quantity dialog on Ellnalise.
        dialog_points = {
            "dialog_price": Point(x=679, y=555),
            "dialog_quantity": Point(x=637, y=581),
            "dialog_maximum": Point(x=709, y=581),
            "dialog_accept": Point(x=622, y=609),
            "split_accept": Point(x=674, y=618),
        }
    else:
        dx = (width - reference.client_width) // 2
        dy = (height - reference.client_height) // 2
        dialog_points = {
            "dialog_price": _shift(reference.dialog_price, dx, dy),
            "dialog_quantity": _shift(reference.dialog_quantity, dx, dy),
            "dialog_maximum": _shift(reference.dialog_maximum, dx, dy),
            "dialog_accept": _shift(reference.dialog_accept, dx, dy),
            "split_accept": _shift(reference.split_accept, dx, dy),
        }
    return base.model_copy(
        deep=True,
        update={
            "client_width": width,
            "client_height": height,
            **dialog_points,
        },
    )


def geometry_for_frame(
    base: Geometry,
    *,
    shop_anchor: Point | None = None,
    bag_anchor: Point | None = None,
) -> Geometry:
    """Translate all panel-relative controls to their current screen positions."""
    shop_anchor = shop_anchor or DEFAULT_SHOP_ANCHOR
    bag_anchor = bag_anchor or DEFAULT_BAG_ANCHOR
    reference = Geometry()
    shop_dx = shop_anchor.x - DEFAULT_SHOP_ANCHOR.x
    shop_dy = shop_anchor.y - DEFAULT_SHOP_ANCHOR.y
    bag_dx = bag_anchor.x - DEFAULT_BAG_ANCHOR.x
    bag_dy = bag_anchor.y - DEFAULT_BAG_ANCHOR.y
    return base.model_copy(
        deep=True,
        update={
            "shop_probe": _shift(reference.shop_probe, shop_dx, shop_dy),
            "sale_grid": base.sale_grid.model_copy(
                update={"first": _shift(reference.sale_grid.first, shop_dx, shop_dy)}
            ),
            "buy_grid": base.buy_grid.model_copy(
                update={"first": _shift(reference.buy_grid.first, shop_dx, shop_dy)}
            ),
            "shop_name": _shift(reference.shop_name, shop_dx, shop_dy),
            "return_button": _shift(reference.return_button, shop_dx, shop_dy),
            "ok_button": _shift(reference.ok_button, shop_dx, shop_dy),
            "cancel_button": _shift(reference.cancel_button, shop_dx, shop_dy),
            "offline_button": _shift(reference.offline_button, shop_dx, shop_dy),
            "bag_probe": _shift(reference.bag_probe, bag_dx, bag_dy),
            "bag_grid": base.bag_grid.model_copy(
                update={"first": _shift(reference.bag_grid.first, bag_dx, bag_dy)}
            ),
        },
    )
