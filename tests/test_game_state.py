from PIL import Image

from app import game_state
from app.models import Point


def test_free_funds_prefers_precise_shop_field_over_noisy_bag(monkeypatch):
    frame = Image.new("RGB", (1440, 1080), "black")
    shop = Point(x=149, y=274)
    bag = Point(x=734, y=257)
    seen = []

    def fake_money(_frame, box):
        seen.append(box)
        return 2_936_500 if box[0] == shop.x + 59 else 32_936_500

    monkeypatch.setattr(game_state, "_money_from_crop", fake_money)

    assert game_state.detect_free_funds(frame, shop, bag) == 2_936_500
    assert seen == [(shop.x + 59, shop.y + 195, shop.x + 264, shop.y + 220)]


def test_bag_money_crop_excludes_coin_icon(monkeypatch):
    frame = Image.new("RGB", (1440, 1080), "black")
    bag = Point(x=734, y=257)
    seen = []

    def fake_money(_frame, box):
        seen.append(box)
        return 2_936_500

    monkeypatch.setattr(game_state, "_money_from_crop", fake_money)

    assert game_state.detect_free_funds(frame, bag_anchor=bag) == 2_936_500
    assert seen[0] == (bag.x + 58, bag.y + 410, bag.x + 150, bag.y + 440)
