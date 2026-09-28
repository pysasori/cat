from app.inventory import extract_count
import pytest

from app import shop as shop_module
from app.market import MarketQuote
from app.models import AppConfig, CatalogItem, Lot, LotSide, PriceMode, ProfileEntry
from app.shop import (
    ShopRunner,
    adjusted_price,
    apply_default_spread_strategy,
    ensure_profitable_spread,
    profile_quantities,
)
from app.storage import Repository


def test_profile_quantity_target_reserves_one_buy_sample():
    assert profile_quantities(36, 100, True, True) == (35, 64, 1)
    assert profile_quantities(100, 100, True, True) == (100, 0, 0)
    assert profile_quantities(120, 100, False, True) == (0, 0, 0)


def test_price_modes_are_calculated_from_each_side_market_price():
    assert adjusted_price(PriceMode.MARKET, 1, 20_000) == 20_000
    assert adjusted_price(PriceMode.MARKET_PERCENT, 1, 100, 5) == 105
    assert adjusted_price(PriceMode.MARKET_PERCENT, 1, 100, 5, sale=True) == 95
    assert adjusted_price(PriceMode.MARKET_AMOUNT, 1, 500, 100) == 600
    assert adjusted_price(PriceMode.MARKET_AMOUNT, 1, 500, 100, sale=True) == 400
    assert adjusted_price(PriceMode.MANUAL, 777, None, 999) == 777
    with pytest.raises(ValueError):
        adjusted_price(PriceMode.MARKET_AMOUNT, 1, 50, 100, sale=True)


def test_loss_making_two_sided_prices_are_rejected():
    ensure_profitable_spread("Loot", 1_001, 1_000)
    with pytest.raises(RuntimeError, match="скупка 1,000, продаж 1,000"):
        ensure_profitable_spread("Loot", 1_000, 1_000)
    with pytest.raises(RuntimeError, match="скупка 1,005, продаж 995"):
        ensure_profitable_spread("Loot", 995, 1_005)


def test_crossed_market_defaults_to_sale_plus_ten_percent_without_buy():
    assert apply_default_spread_strategy(995, 1_000) == (1_100, None, True)
    assert apply_default_spread_strategy(1_100, 1_000) == (1_100, 1_000, False)
    assert apply_default_spread_strategy(None, 1_000) == (None, 1_000, False)


def test_ocr_count_parser_defaults_to_singleton():
    assert extract_count("Soft Fur (36)") == 36
    assert extract_count("Soft Fur") == 1
    assert extract_count("Soft Fur (36)\nPrice 800 (28,800)") == 36


def test_legacy_lots_migrate_to_catalog_and_profile(tmp_path):
    repository = Repository(tmp_path)
    repository.save_config(AppConfig(shop_name="20k / 500", window_hwnd=123))
    repository.save_lots(
        [
            Lot(name="Loot", side=LotSide.SALE, price=20_000, quantity=35, icon_file="loot.png"),
            Lot(name="Loot", side=LotSide.BUY, price=500, quantity=64, icon_file="buy.png"),
        ]
    )
    catalog = repository.catalog()
    profiles = repository.profiles()
    assert len(catalog) == 1
    assert profiles[0].window_hwnd == 123
    assert profiles[0].entries[0].max_owned == 100
    assert profiles[0].entries[0].sale_price == 20_000
    assert profiles[0].entries[0].buy_price == 500
    characters = repository.characters()
    assert characters[0].character_name == "Ellnalise"
    assert characters[0].profile_id == profiles[0].id
    assert characters[0].window_hwnd == 123


def test_profile_run_refreshes_every_enabled_item_with_market_id(tmp_path, monkeypatch):
    repository = Repository(tmp_path)
    first = repository.add_catalog_item(
        CatalogItem(name="First", icon_file="first.png", market_item_id=11)
    )
    second = repository.add_catalog_item(
        CatalogItem(name="Second", icon_file="second.png", market_item_id=22)
    )
    local = repository.add_catalog_item(CatalogItem(name="Manual", icon_file="manual.png"))
    requested = []

    def fake_quote(item_id, **_):
        requested.append(item_id)
        return MarketQuote(item_id, f"Item {item_id}", "", [item_id * 10], [item_id * 5])

    monkeypatch.setattr(shop_module, "fetch_quote", fake_quote)
    runner = ShopRunner(repository)
    updated = runner._refresh_market_prices(
        [
            ProfileEntry(item_id=first.id),
            ProfileEntry(item_id=second.id),
            ProfileEntry(item_id=local.id),
        ],
        {item.id: item for item in repository.catalog()},
        repository.config(),
    )

    assert sorted(requested) == [11, 22]
    assert updated[first.id].market_sell == 110
    assert updated[first.id].market_buy == 55
    assert updated[second.id].market_sell == 220
    assert updated[second.id].market_buy == 110
    assert updated[local.id].market_updated_at is None
