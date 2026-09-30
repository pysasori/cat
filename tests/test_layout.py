from PIL import Image

from app.layout import TEMPLATES, geometry_for_client, geometry_for_frame, locate_bag, locate_shop
from app.models import Geometry, Point


def test_panel_templates_are_found_anywhere_on_screen():
    frame = Image.new("RGB", (1100, 900), (23, 31, 37))
    bag = Image.open(TEMPLATES / "bag_title.png").convert("RGB")
    shop = Image.open(TEMPLATES / "sell_panel.png").convert("RGB")
    frame.paste(bag, (611, 73))
    frame.paste(shop, (129, 307))

    assert locate_bag(frame) == Point(x=611 + bag.width // 2, y=73 + bag.height // 2)
    assert locate_shop(frame) == Point(x=129 + shop.width // 2, y=307 + shop.height // 2)


def test_shop_match_is_rejected_when_controls_would_be_off_screen():
    frame = Image.new("RGB", (1440, 1080), (23, 31, 37))
    shop = Image.open(TEMPLATES / "sell_panel.png").convert("RGB")
    frame.paste(shop, (97, 1008))

    assert locate_shop(frame) is None


def test_geometry_follows_shop_and_bag_independently():
    base = Geometry()
    moved = geometry_for_frame(
        base,
        shop_anchor=Point(x=347, y=374),
        bag_anchor=Point(x=885, y=465),
    )

    assert moved.sale_grid.first == Point(x=335, y=402)
    assert moved.ok_button == Point(x=586, y=603)
    assert moved.bag_grid.first == Point(x=763, y=749)
    # Modal dialogs are centred by PW and do not move with either panel.
    assert moved.dialog_accept == base.dialog_accept


def test_geometry_supports_1280_by_720_client():
    base = Geometry()
    resized = geometry_for_client(base, 1280, 720)

    assert (resized.client_width, resized.client_height) == (1280, 720)
    assert resized.sale_grid == base.sale_grid
    assert resized.bag_grid == base.bag_grid
    assert resized.dialog_price == Point(x=715, y=631)
    assert resized.dialog_quantity == Point(x=640, y=657)
    assert resized.dialog_accept == Point(x=611, y=686)
    assert resized.split_accept == Point(x=663, y=695)
