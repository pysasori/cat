from PIL import Image, ImageDraw
import pytest

from app.inventory import InventoryStack
from app.models import AppConfig, CatalogItem, Character, Lot, LotSide, Point, Profile, ProfileEntry
from app import shop
from app.shop import BuyReservation, ShopRunner, layout_quantities, sale_stack_plan
from app.storage import Repository
from app.vision import Match, crop_icon
from app.windows import GameWindow


def make_item(color):
    item = Image.new("RGB", (26, 26), (8, 10, 12))
    draw = ImageDraw.Draw(item)
    draw.rectangle((4, 4, 21, 21), fill=color)
    draw.line((4, 21, 21, 4), fill="white", width=2)
    return item


def test_plan_separates_sale_and_buy_slots(tmp_path):
    repository = Repository(tmp_path)
    config = AppConfig()
    repository.save_config(config)
    frame = Image.new("RGB", (1440, 1080), (5, 8, 10))
    sale_icon, buy_icon = make_item((210, 70, 40)), make_item((50, 100, 230))
    first, second = config.geometry.bag_grid.point(0), config.geometry.bag_grid.point(3)
    frame.paste(sale_icon, (first.x - 13, first.y - 13))
    frame.paste(buy_icon, (second.x - 13, second.y - 13))
    sale_icon.save(repository.icons / "sale.png")
    buy_icon.save(repository.icons / "buy.png")
    lots = [
        Lot(name="Продаж", side=LotSide.SALE, icon_file="sale.png"),
        Lot(name="Скупка", side=LotSide.BUY, icon_file="buy.png"),
    ]
    plan = ShopRunner(repository)._plan(frame, lots, config)
    assert len(plan) == 2
    assert (plan[0].destination_x, plan[0].destination_y) == (
        config.geometry.sale_grid.first.x,
        config.geometry.sale_grid.first.y,
    )
    assert (plan[1].destination_x, plan[1].destination_y) == (
        config.geometry.buy_grid.first.x,
        config.geometry.buy_grid.first.y,
    )
    assert plan[1].as_dict()["modifier"] == "alt-split-1"


def inventory_stack(config: AppConfig, index: int, quantity: int) -> InventoryStack:
    return InventoryStack(
        match=Match(index=index, point=config.geometry.bag_grid.point(index), score=0.99),
        quantity=quantity,
    )


def test_sale_stack_plan_keeps_top_anchor_and_reserves_bottom_sample():
    config = AppConfig()
    stacks = [inventory_stack(config, 0, 100), inventory_stack(config, 17, 36)]

    planned = sale_stack_plan(stacks, BuyReservation(source_index=17, split=False))

    assert [quantity for _, quantity in planned] == [99]
    assert sum(quantity for _, quantity in planned) == 99


def test_layout_quantities_never_sell_bottom_16_and_keep_one_on_top():
    config = AppConfig()
    stacks = [inventory_stack(config, 0, 99), inventory_stack(config, 24, 31)]

    sale, buy, needs_sample = layout_quantities(stacks, 200, True, True)

    assert (sale, buy, needs_sample) == (98, 70, False)


def test_layout_quantities_caps_sale_at_one_stack():
    config = AppConfig()
    stacks = [inventory_stack(config, 0, 99), inventory_stack(config, 1, 31)]

    sale, buy, needs_sample = layout_quantities(
        stacks, 50, True, False, stack_limit=100
    )

    assert (sale, buy, needs_sample) == (100, 0, False)


def test_multiple_inventory_stacks_are_merged_before_single_sale_lot(tmp_path):
    repository = Repository(tmp_path)
    runner = ShopRunner(repository)
    config = AppConfig(click_ok=False, shop_name="", action_delay=0.05)
    stacks = [inventory_stack(config, 0, 100), inventory_stack(config, 1, 36)]
    lot = Lot(name="Loot", side=LotSide.SALE, quantity=135, price=20_000, icon_file="loot.png")
    make_item((80, 160, 70)).save(repository.icons / lot.icon_file)
    drags = []
    dialogs = []

    class Driver:
        def drag(self, x1, y1, x2, y2, *, modifier=None):
            drags.append((x1, y1, x2, y2, modifier))

        def click(self, *_):
            pass

        def hotkey(self, *_):
            pass

        def clear_text(self, *_):
            pass

        def type_text(self, *_):
            pass

    runner._sleep = lambda _: None
    runner._wait_for_panels = lambda *_: Image.new("RGB", (1440, 1080), (5, 8, 10))
    runner._require_item_at = lambda *_: None
    runner._require_slot_state = lambda *_: None
    runner._require_dialog = lambda *_: None
    runner._fill_dialog = lambda capture, driver, current, current_config, quantity=None, **_: dialogs.append(quantity)
    runner._fill_lots(
        capture=object(),
        input_driver=Driver(),
        lots=[lot],
        config=config,
        inventory_stacks={"loot.png": stacks},
    )

    sale = config.geometry.sale_grid.point(0)
    assert len(drags) == 2
    primary = config.geometry.bag_grid.point(0)
    assert (drags[0][2], drags[0][3]) == (primary.x, primary.y)
    assert drags[0][4] == "alt"
    assert (drags[1][2], drags[1][3]) == (sale.x, sale.y)
    assert dialogs == [135]


def test_offline_trade_confirms_shop_before_starting_offline_mode(tmp_path):
    runner = ShopRunner(Repository(tmp_path))
    config = AppConfig(click_ok=True, offline_trade=True, shop_name="")
    clicks = []

    class Capture:
        def grab(self):
            # A closed HWND is the success signal after offline trade starts.
            raise RuntimeError("window closed")

    class Driver:
        def click(self, x, y):
            clicks.append((x, y))

    runner._sleep = lambda _: None
    runner._guard_driver = lambda capture, driver, config, **_: driver
    runner._fill_lots(Capture(), Driver(), [], config)

    ok = config.geometry.ok_button
    offline = config.geometry.offline_button
    assert clicks == [(ok.x, ok.y), (offline.x, offline.y)]


def test_shop_name_is_cleared_without_ctrl_a(tmp_path):
    runner = ShopRunner(Repository(tmp_path))
    config = AppConfig(click_ok=False, offline_trade=False, shop_name="Міражі")
    events = []

    class Driver:
        def click(self, x, y):
            events.append(("click", x, y))

        def clear_text(self, length=16):
            events.append(("clear", length))

        def type_text(self, value):
            events.append(("type", value))

        def hotkey(self, *_):
            raise AssertionError("Ctrl+A must not be used for the PW shop-name field")

    runner._fill_lots(object(), Driver(), [], config)

    assert events == [
        ("click", config.geometry.shop_name.x, config.geometry.shop_name.y),
        ("clear", 100),
        ("type", "Міражі"),
    ]


def test_missing_buy_sample_is_saved_as_character_error(tmp_path, monkeypatch):
    repository = Repository(tmp_path)
    icon = "loot.png"
    make_item((80, 160, 70)).save(repository.icons / icon)
    item = repository.add_catalog_item(CatalogItem(name="Loot", icon_file=icon))
    profile = repository.add_profile(
        Profile(
            name="Buyer",
            entries=[ProfileEntry(item_id=item.id, buy_enabled=True, sale_enabled=False, max_owned=100)],
        )
    )
    character = repository.add_character(Character(character_name="Cat", profile_id=profile.id))
    runner = ShopRunner(repository)

    class Capture:
        def __init__(self, _):
            pass

        def grab(self):
            return Image.new("RGB", (1440, 1080), (5, 8, 10))

    monkeypatch.setattr(shop, "resolve_window", lambda *_: GameWindow(1, "PW", 1440, 1080, False, 1))
    monkeypatch.setattr(shop, "WindowCapture", Capture)
    monkeypatch.setattr(shop, "make_input", lambda *_: object())
    monkeypatch.setattr(shop, "locate_bag", lambda *_: Point(x=985, y=265))
    monkeypatch.setattr(shop, "scan_templates", lambda *_, **__: {item.id: []})
    monkeypatch.setattr(shop, "detect_free_funds", lambda *_: 10_000)
    monkeypatch.setattr(runner, "_validate_window", lambda *_: None)
    monkeypatch.setattr(runner, "_refresh_market_prices", lambda entries, catalog, config: catalog)

    runner._run_guarded(True, None, character.id)

    saved = next(item for item in repository.characters() if item.id == character.id)
    assert runner.snapshot().stage == "error"
    assert saved.last_run_status == "error"
    assert saved.last_run_message == "Немає предмета-зразка для скупки: Loot"


def test_profile_stats_are_captured_before_offline_window_closes(tmp_path, monkeypatch):
    repository = Repository(tmp_path)
    icon = "loot.png"
    make_item((80, 160, 70)).save(repository.icons / icon)
    item = repository.add_catalog_item(CatalogItem(name="Loot", icon_file=icon))
    profile = repository.add_profile(
        Profile(
            name="Seller",
            entries=[
                ProfileEntry(
                    item_id=item.id,
                    sale_enabled=True,
                    buy_enabled=False,
                    sale_price=777,
                )
            ],
        )
    )
    character = repository.add_character(Character(character_name="Cat", profile_id=profile.id))
    runner = ShopRunner(repository)
    config = repository.config()

    class Capture:
        def __init__(self, _):
            pass

        def grab(self):
            return Image.new("RGB", (1440, 1080), (5, 8, 10))

    monkeypatch.setattr(shop, "resolve_window", lambda *_: GameWindow(1, "PW", 1440, 1080, False, 1))
    monkeypatch.setattr(shop, "WindowCapture", Capture)
    monkeypatch.setattr(shop, "make_input", lambda *_: object())
    monkeypatch.setattr(shop, "scan_templates", lambda *_, **__: {item.id: [inventory_stack(config, 0, 2)]})
    monkeypatch.setattr(shop, "detect_free_funds", lambda *_: 123)
    monkeypatch.setattr(runner, "_validate_window", lambda *_: None)
    monkeypatch.setattr(runner, "_open_windows", lambda capture, driver, current: current)
    monkeypatch.setattr(runner, "_wait_for_panels", lambda capture, *_: capture.grab())
    monkeypatch.setattr(runner, "_return_existing_lots", lambda *_: None)
    monkeypatch.setattr(runner, "_fill_lots", lambda *_: None)
    monkeypatch.setattr(
        runner,
        "_plan",
        lambda frame, lots, current: [
            shop.PlannedLot(
                lot=lot,
                source=Match(index=0, point=current.geometry.bag_grid.point(0), score=1.0),
                destination_x=current.geometry.sale_grid.point(index).x,
                destination_y=current.geometry.sale_grid.point(index).y,
            )
            for index, lot in enumerate(lots)
        ],
    )

    runner._run_profile(False, profile.id, character.id)

    saved = next(value for value in repository.characters() if value.id == character.id)
    assert saved.free_funds == 123
    assert saved.sale_value == 777


def test_open_shop_retries_f1_until_window_appears(tmp_path, monkeypatch):
    runner = ShopRunner(Repository(tmp_path))
    config = AppConfig()
    frames = iter([0, 1, 2, 3, 3])

    class Capture:
        def grab(self):
            return next(frames)

    class Driver:
        def __init__(self):
            self.presses = []

        def press(self, key):
            self.presses.append(key)

    driver = Driver()
    monkeypatch.setattr(shop, "locate_shop", lambda frame: config.geometry.shop_probe if frame >= 3 else None)
    monkeypatch.setattr(shop, "locate_bag", lambda *_: config.geometry.bag_probe)
    monkeypatch.setattr(runner, "_sleep", lambda *_: None)

    runner._open_windows(Capture(), driver, config)

    assert driver.presses == ["f1", "f1"]


def test_guarded_input_never_calls_driver_after_stop(tmp_path):
    runner = ShopRunner(Repository(tmp_path))
    calls = []

    class Driver:
        def click(self, x, y):
            calls.append((x, y))

    runner.stop()
    guarded = runner._guard_driver(object(), Driver(), AppConfig())

    with pytest.raises(InterruptedError):
        guarded.click(100, 200)

    assert calls == []


def test_panel_guard_stops_without_input_when_layout_moved(tmp_path, monkeypatch):
    runner = ShopRunner(Repository(tmp_path))
    config = AppConfig()

    class Capture:
        def grab(self):
            return Image.new("RGB", (1440, 1080), (5, 8, 10))

    monkeypatch.setattr(shop, "locate_shop", lambda *_: Point(x=347, y=374))
    monkeypatch.setattr(shop, "locate_bag", lambda *_: Point(x=985, y=265))

    with pytest.raises(RuntimeError, match="Безпечна зупинка"):
        runner._wait_for_panels(Capture(), config, timeout=0)
