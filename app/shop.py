from __future__ import annotations

import threading
import time
from math import ceil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from app.capture import WindowCapture
from app.game_state import detect_free_funds
from app.input import InputDriver, make_input
from app.inventory import InventoryStack, scan_templates
from app.layout import geometry_for_frame, locate_bag, locate_shop
from app.market import fetch_quote
from app.models import AppConfig, CatalogItem, InputMode, JobState, Lot, LotSide, Point, PriceMode, ProfileEntry
from app.storage import Repository
from app.vision import Match, crop_icon, find_empty_cell, icon_similarity, image_changed, load_icon, occupied_grid_indexes, search_grid
from app.windows import GameWindow, resolve_window


@dataclass(frozen=True)
class PlannedLot:
    lot: Lot
    source: Match
    destination_x: int
    destination_y: int

    def as_dict(self) -> dict:
        return {
            "lot_id": self.lot.id,
            "name": self.lot.name,
            "side": self.lot.side.value,
            "source": {"x": self.source.point.x, "y": self.source.point.y},
            "destination": {"x": self.destination_x, "y": self.destination_y},
            "score": round(self.source.score, 3),
            "price": self.lot.price,
            "quantity": self.lot.quantity,
            "modifier": "alt-split-1" if self.lot.side is LotSide.BUY else None,
        }


@dataclass(frozen=True)
class BuyReservation:
    source_index: int
    split: bool


class GuardedInput:
    """Serialize stop requests with inputs and revalidate the UI first."""

    def __init__(self, delegate: InputDriver, lock: threading.RLock, before_input) -> None:
        self.delegate = delegate
        self.lock = lock
        self.before_input = before_input

    def _run(self, method: str, *args, **kwargs):
        # Holding the same lock used by stop() means that once the stop API
        # returns no later input can slip through a check/action race.
        with self.lock:
            self.before_input()
            return getattr(self.delegate, method)(*args, **kwargs)

    def press(self, key: str) -> None:
        self._run("press", key)

    def hotkey(self, *keys: str) -> None:
        self._run("hotkey", *keys)

    def type_text(self, text: str) -> None:
        self._run("type_text", text)

    def clear_text(self, length: int = 16) -> None:
        self._run("clear_text", length)

    def move(self, x: int, y: int) -> None:
        self._run("move", x, y)

    def click(self, x: int, y: int) -> None:
        self._run("click", x, y)

    def drag(self, x1: int, y1: int, x2: int, y2: int, *, modifier: str | None = None) -> None:
        self._run("drag", x1, y1, x2, y2, modifier=modifier)


def sale_stack_plan(
    stacks: list[InventoryStack],
    reservation: BuyReservation | None = None,
    sale_slot_count: int = 16,
) -> list[tuple[InventoryStack, int]]:
    """Sell only top-half stacks, keeping one top anchor and all bottom samples."""
    result = []
    for stack in stacks:
        if stack.match.index >= sale_slot_count:
            continue
        quantity = stack.quantity
        if (
            reservation is not None
            and reservation.split
            and stack.match.index == reservation.source_index
        ):
            quantity -= 1
        if quantity > 0:
            result.append((stack, quantity))
    if result:
        anchor = max(range(len(result)), key=lambda index: result[index][1])
        stack, quantity = result[anchor]
        result[anchor] = (stack, quantity - 1)
        result = [item for item in result if item[1] > 0]
    return sorted(result, key=lambda item: item[1], reverse=True)


def layout_quantities(
    stacks: list[InventoryStack],
    maximum: int,
    sale_enabled: bool,
    buy_enabled: bool,
    sale_slot_count: int = 16,
    stack_limit: int = 999_999,
) -> tuple[int, int, bool]:
    """Quantities for the fixed 16 sale slots / 16 protected buy-sample slots."""
    owned = sum(stack.quantity for stack in stacks)
    top_owned = sum(stack.quantity for stack in stacks if stack.match.index < sale_slot_count)
    has_bottom_sample = any(stack.match.index >= sale_slot_count for stack in stacks)
    buy_quantity = max(0, maximum - owned) if buy_enabled else 0
    needs_sample = bool(buy_quantity and owned and not has_bottom_sample)
    reserved_top = (1 if top_owned else 0) + (1 if needs_sample else 0)
    available_for_sale = max(0, top_owned - reserved_top)
    # One catalog item occupies one sale slot. If the total exceeds the game's
    # stack limit, sell one full stack and leave the overflow in the bag.
    sale_quantity = min(available_for_sale, stack_limit) if sale_enabled else 0
    return sale_quantity, buy_quantity, needs_sample


def profile_quantities(owned: int, maximum: int, sale_enabled: bool, buy_enabled: bool) -> tuple[int, int, int]:
    """Return sale quantity, purchase quantity and the sample reserved for purchase."""
    buy_quantity = max(0, maximum - owned) if buy_enabled else 0
    reserve = 1 if buy_quantity > 0 and owned > 0 else 0
    sale_quantity = max(0, owned - reserve) if sale_enabled else 0
    return sale_quantity, buy_quantity, reserve


def adjusted_price(
    mode: PriceMode,
    manual: int,
    market: int | None,
    adjustment: float = 0,
    *,
    sale: bool = False,
) -> int:
    if mode is PriceMode.MANUAL:
        return manual
    if market is None:
        raise ValueError("ринкова ціна відсутня")
    direction = -1 if sale else 1
    if mode is PriceMode.MARKET:
        result = market
    elif mode is PriceMode.MARKET_PERCENT:
        result = round(market * (1 + direction * adjustment / 100))
    else:
        result = round(market + direction * adjustment)
    if result < 1 or result > 2_000_000_000:
        raise ValueError(f"розрахована ціна {result} поза допустимим діапазоном")
    return result


def ensure_profitable_spread(name: str, sale_price: int | None, buy_price: int | None) -> None:
    """Reject a two-sided shop that would buy at or above its selling price."""
    if sale_price is not None and buy_price is not None and buy_price >= sale_price:
        raise RuntimeError(
            f"{name}: збиткова пара цін — скупка {buy_price:,}, продаж {sale_price:,}. "
            "Скупка має бути нижчою за продаж."
        )


def apply_default_spread_strategy(
    sale_price: int | None,
    buy_price: int | None,
) -> tuple[int | None, int | None, bool]:
    """Disable buying on a crossed market and sell 10% above the best buy."""
    if sale_price is not None and buy_price is not None and buy_price >= sale_price:
        return ceil(buy_price * 1.10), None, True
    return sale_price, buy_price, False


class ShopRunner:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self.state = JobState()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None

    def snapshot(self) -> JobState:
        with self._lock:
            return self.state.model_copy(deep=True)

    def _set(self, **updates) -> None:
        with self._lock:
            self.state = self.state.model_copy(update=updates)

    def _log(self, message: str) -> None:
        with self._lock:
            log = [*self.state.log, message][-100:]
            self.state = self.state.model_copy(update={"message": message, "log": log})

    def start(
        self,
        dry_run: bool,
        profile_id: str | None = None,
        character_id: str | None = None,
    ) -> JobState:
        with self._lock:
            if self.state.running:
                raise RuntimeError("підготовка лавки вже виконується")
            self.state = JobState(running=True, stage="starting", message="старт")
            self._record_character_status(character_id, "running", "Запуск стратегії")
            self._thread = threading.Thread(
                target=self._run_guarded,
                args=(dry_run, profile_id, character_id),
                daemon=True,
            )
            self._thread.start()
            return self.state.model_copy(deep=True)

    def stop(self) -> JobState:
        with self._lock:
            self.state = self.state.model_copy(update={"stop_requested": True, "message": "зупиняю"})
            return self.state.model_copy(deep=True)

    def _run_guarded(
        self,
        dry_run: bool,
        profile_id: str | None,
        character_id: str | None,
    ) -> None:
        try:
            warnings = self._run(dry_run, profile_id, character_id)
            self._check_stop()
            warning_message = "; ".join(warnings)
            self._set(
                running=False,
                stage="done",
                message=(f"готово з попередженням: {warning_message}" if warnings else "готово"),
            )
            self._record_character_status(
                character_id,
                "warning" if warnings else ("checked" if dry_run else "done"),
                (
                    f"Лавку виставлено частково: {warning_message}"
                    if warnings and not dry_run
                    else f"Перевірку завершено з попередженням: {warning_message}"
                    if warnings
                    else "Перевірку завершено"
                    if dry_run
                    else "Лавку успішно виставлено"
                ),
            )
        except InterruptedError:
            self._set(running=False, stage="stopped", message="зупинено")
            self._record_character_status(character_id, "stopped", "Запуск зупинено")
        except Exception as error:
            self._set(running=False, stage="error", message=str(error), error=str(error))
            self._record_character_status(character_id, "error", str(error))

    def _record_character_status(
        self,
        character_id: str | None,
        status: str,
        message: str,
    ) -> None:
        if not character_id:
            return
        character = next(
            (item for item in self.repository.characters() if item.id == character_id),
            None,
        )
        if character is None:
            return
        self.repository.replace_character(
            character.model_copy(
                update={
                    "last_run_status": status,
                    "last_run_message": message[:1000],
                    "last_run_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        )

    def _check_stop(self) -> None:
        if self.snapshot().stop_requested:
            raise InterruptedError

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while True:
            self._check_stop()
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.08, remaining))

    def _wait_for_panels(
        self,
        capture: WindowCapture,
        config: AppConfig,
        timeout: float = 4.0,
        *,
        require_shop: bool = True,
        require_bag: bool = True,
    ) -> Image.Image:
        """Wait without input until the exact shop and bag layout is visible."""
        deadline = time.monotonic() + timeout
        last_reason = "лавку або інвентар не знайдено"
        while True:
            self._check_stop()
            frame = capture.grab()
            shop_anchor = locate_shop(frame)
            bag_anchor = locate_bag(frame)
            shop_missing = require_shop and shop_anchor is None
            bag_missing = require_bag and bag_anchor is None
            if shop_missing or bag_missing:
                missing = []
                if shop_missing:
                    missing.append("лавку")
                if bag_missing:
                    missing.append("інвентар")
                last_reason = "не бачу " + " та ".join(missing)
            else:
                actual = geometry_for_frame(
                    config.geometry,
                    shop_anchor=shop_anchor,
                    bag_anchor=bag_anchor,
                )
                shop_shift = max(
                    abs(actual.sale_grid.first.x - config.geometry.sale_grid.first.x),
                    abs(actual.sale_grid.first.y - config.geometry.sale_grid.first.y),
                )
                bag_shift = max(
                    abs(actual.bag_grid.first.x - config.geometry.bag_grid.first.x),
                    abs(actual.bag_grid.first.y - config.geometry.bag_grid.first.y),
                )
                shop_ok = not require_shop or shop_shift <= 4
                bag_ok = not require_bag or bag_shift <= 4
                if shop_ok and bag_ok:
                    self._check_stop()
                    return frame
                last_reason = (
                    "лавка або інвентар змістилися після розпізнавання "
                    f"(лавка {shop_shift}px, інвентар {bag_shift}px)"
                )
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Безпечна зупинка: {last_reason}; кліків не виконано")
            self._sleep(0.15)

    def _guard_driver(
        self,
        capture: WindowCapture,
        driver: InputDriver,
        config: AppConfig,
        *,
        require_bag: bool = True,
    ) -> GuardedInput:
        if isinstance(driver, GuardedInput):
            driver = driver.delegate
        return GuardedInput(
            driver,
            self._lock,
            lambda: self._wait_for_panels(
                capture,
                config,
                require_bag=require_bag,
            ),
        )

    @staticmethod
    def _dialog_box(frame: Image.Image, point: Point) -> tuple[int, int, int, int]:
        return (
            max(0, point.x - 150),
            max(0, point.y - 110),
            min(frame.width, point.x + 180),
            min(frame.height, point.y + 35),
        )

    def _require_dialog(
        self,
        capture: WindowCapture,
        config: AppConfig,
        point: Point,
        before: Image.Image | None = None,
    ) -> Image.Image:
        frame = self._wait_for_panels(capture, config)
        box = self._dialog_box(frame, point)
        pixels = np.asarray(frame.crop(box).convert("RGB"), dtype=np.uint8)
        luminance = pixels.mean(axis=2)
        dark_ratio = float((luminance < 55).mean())
        gold_ratio = float(
            (
                (pixels[:, :, 0] > 90)
                & (pixels[:, :, 1] > 70)
                & (pixels[:, :, 2] < 90)
            ).mean()
        )
        changed = image_changed(before, frame, box) if before is not None else 99.0
        if dark_ratio < 0.04 or gold_ratio < 0.003 or changed < 2.0:
            raise RuntimeError("Безпечна зупинка: очікуваний діалог ціни/кількості не з'явився")
        return frame

    def _require_item_at(
        self,
        frame: Image.Image,
        template: Image.Image,
        point: Point,
        threshold: float,
        label: str,
    ) -> None:
        candidate = crop_icon(frame, point.x, point.y)
        score = icon_similarity(template, candidate)
        if score < max(0.55, threshold - 0.10):
            raise RuntimeError(
                f"Безпечна зупинка: {label} відсутній у очікуваній клітинці "
                f"(збіг {score:.2f})"
            )

    @staticmethod
    def _require_slot_state(frame: Image.Image, grid, index: int, occupied: bool, label: str) -> None:
        is_occupied = index in occupied_grid_indexes(frame, grid)
        if is_occupied != occupied:
            state = "зайнятий" if is_occupied else "порожній"
            expected = "зайнятий" if occupied else "порожній"
            raise RuntimeError(
                f"Безпечна зупинка: {label} зараз {state}, очікувався {expected}"
            )

    def _run(
        self,
        dry_run: bool,
        profile_id: str | None = None,
        character_id: str | None = None,
    ) -> list[str]:
        if profile_id or character_id:
            return self._run_profile(dry_run, profile_id, character_id)
        self._run_legacy(dry_run)
        return []

    def _run_legacy(self, dry_run: bool) -> None:
        config = self.repository.config()
        lots = [lot for lot in self.repository.lots() if lot.enabled]
        if not lots:
            raise RuntimeError("у базі немає увімкнених лотів")
        window = resolve_window(config.window_hwnd, config.window_index)
        self._validate_window(window, config)
        capture = WindowCapture(window.hwnd)
        frame = capture.grab()
        plan = self._plan(frame, lots, config)
        self._set(stage="planned", preview=[item.as_dict() for item in plan])
        self._log(f"знайдено {len(plan)} предметів із {len(lots)}")
        if dry_run:
            missing = len(lots) - len(plan)
            if missing:
                raise RuntimeError(f"dry-run: не знайдено {missing} предметів у рюкзаку")
            self._log("dry-run завершено: кліків не було")
            return

        input_driver = make_input(config.input_mode, window.hwnd, config.drag_duration)
        config = self._open_windows(capture, input_driver, config)
        input_driver = self._guard_driver(capture, input_driver, config)
        self._fill_lots(capture, input_driver, lots, config)

    def _fill_lots(
        self,
        capture: WindowCapture,
        input_driver: InputDriver,
        lots: list[Lot],
        config: AppConfig,
        inventory_stacks: dict[str, list[InventoryStack]] | None = None,
    ) -> None:
        buy_samples, sample_indexes, buy_originals, reservations = self._prepare_buy_samples(
            capture, input_driver, lots, config, inventory_stacks
        )
        self._set(stage="filling")
        sale_index = buy_index = 0
        for position, lot in enumerate(lots, start=1):
            self._check_stop()
            target_grid = config.geometry.sale_grid if lot.side is LotSide.SALE else config.geometry.buy_grid
            target_index = sale_index if lot.side is LotSide.SALE else buy_index
            if target_index >= target_grid.count:
                raise RuntimeError(f"у секції {lot.side.value} більше немає вільних лотів")
            target = target_grid.point(target_index)
            template = load_icon(self.repository.icons / lot.icon_file)
            self._log(f"{position}/{len(lots)} · {lot.name}: перетягую")
            if lot.side is LotSide.BUY:
                sample = buy_samples[lot.id]
                before_dialog = self._wait_for_panels(capture, config)
                self._require_item_at(
                    before_dialog,
                    template,
                    sample,
                    config.match_threshold,
                    f"зразок {lot.name}",
                )
                self._require_slot_state(
                    before_dialog,
                    target_grid,
                    target_index,
                    False,
                    f"лот скупки {lot.name}",
                )
                input_driver.drag(sample.x, sample.y, target.x, target.y)
            else:
                known_stacks = (inventory_stacks or {}).get(lot.icon_file)
                if known_stacks is not None:
                    sources = sale_stack_plan(
                        known_stacks,
                        reservations.get(lot.icon_file),
                        config.geometry.bag_grid.count // 2,
                    )
                    available = sum(quantity for _, quantity in sources)
                    if not sources or available < lot.quantity:
                        raise RuntimeError(
                            f"{lot.name}: після резервування доступно {available}, "
                            f"для продажу очікується {lot.quantity}"
                        )
                    sale_slot_count = config.geometry.bag_grid.count // 2
                    top_stacks = [
                        stack for stack in known_stacks if stack.match.index < sale_slot_count
                    ]
                    primary = max(top_stacks, key=lambda stack: stack.quantity)
                    reservation = reservations.get(lot.icon_file)
                    primary_quantity = primary.quantity
                    if (
                        reservation is not None
                        and reservation.split
                        and reservation.source_index == primary.match.index
                    ):
                        primary_quantity -= 1
                    needed = max(0, lot.quantity - primary_quantity)
                    for extra in (stack for stack in top_stacks if stack is not primary):
                        if needed <= 0:
                            break
                        move_quantity = min(extra.quantity, needed)
                        self._log(
                            f"{lot.name}: додаю {move_quantity} до основного стека "
                            f"у рюкзаку (ліміт одного лота {lot.quantity})"
                        )
                        before_merge = self._wait_for_panels(capture, config)
                        self._require_item_at(
                            before_merge,
                            template,
                            primary.match.point,
                            config.match_threshold,
                            f"основний стек {lot.name}",
                        )
                        self._require_item_at(
                            before_merge,
                            template,
                            extra.match.point,
                            config.match_threshold,
                            f"додатковий стек {lot.name}",
                        )
                        split = move_quantity < extra.quantity
                        input_driver.drag(
                            extra.match.point.x,
                            extra.match.point.y,
                            primary.match.point.x,
                            primary.match.point.y,
                            modifier="alt" if split else None,
                        )
                        if split:
                            self._sleep(config.dialogs.open_delay)
                            accept = config.geometry.split_accept
                            self._require_dialog(capture, config, accept, before_merge)
                            quantity_point = config.geometry.dialog_quantity
                            input_driver.click(quantity_point.x, quantity_point.y)
                            input_driver.clear_text(8)
                            input_driver.type_text(str(move_quantity))
                            self._sleep(config.dialogs.field_delay)
                            self._require_dialog(capture, config, accept, before_merge)
                            input_driver.click(accept.x, accept.y)
                        self._sleep(config.action_delay)
                        after_merge = self._wait_for_panels(capture, config)
                        if split:
                            self._require_item_at(
                                after_merge,
                                template,
                                extra.match.point,
                                config.match_threshold,
                                f"залишок стека {lot.name}",
                            )
                        else:
                            self._require_slot_state(
                                after_merge,
                                config.geometry.bag_grid,
                                extra.match.index,
                                False,
                                f"об'єднаний стек {lot.name}",
                            )
                        self._require_item_at(
                            after_merge,
                            template,
                            primary.match.point,
                            config.match_threshold,
                            f"об'єднаний стек {lot.name}",
                        )
                        primary_quantity += move_quantity
                        needed -= move_quantity
                    if needed > 0:
                        raise RuntimeError(
                            f"{lot.name}: не вдалося зібрати один стек на {lot.quantity} шт."
                        )
                    before_dialog = self._wait_for_panels(capture, config)
                    self._require_item_at(
                        before_dialog,
                        template,
                        primary.match.point,
                        config.match_threshold,
                        lot.name,
                    )
                    self._require_slot_state(
                        before_dialog,
                        target_grid,
                        target_index,
                        False,
                        f"лот продажу {lot.name}",
                    )
                    input_driver.drag(
                        primary.match.point.x,
                        primary.match.point.y,
                        target.x,
                        target.y,
                    )
                    self._sleep(config.dialogs.open_delay)
                    self._fill_dialog(
                        capture,
                        input_driver,
                        lot,
                        config,
                        quantity=lot.quantity,
                        before=before_dialog,
                    )
                    after = self._wait_for_panels(capture, config)
                    self._require_item_at(
                        after,
                        template,
                        target,
                        config.match_threshold,
                        f"створений лот {lot.name}",
                    )
                    sale_index += 1
                    self._sleep(config.action_delay)
                    continue
                else:
                    fresh = self._wait_for_panels(capture, config)
                    paired = max(
                        buy_originals,
                        key=lambda item: icon_similarity(template, item[0]),
                        default=None,
                    )
                    if paired is not None and icon_similarity(template, paired[0]) >= 0.9:
                        best = paired[1]
                    else:
                        matches = search_grid(fresh, template, config.geometry.bag_grid)
                        best = next(
                            (match for match in matches if match.index not in sample_indexes),
                            matches[0],
                        )
                if best.score < config.match_threshold:
                    raise RuntimeError(
                        f"{lot.name}: предмет не знайдено (збіг {best.score:.2f}, потрібно {config.match_threshold:.2f})"
                    )
                before_dialog = self._wait_for_panels(capture, config)
                self._require_item_at(
                    before_dialog,
                    template,
                    best.point,
                    config.match_threshold,
                    lot.name,
                )
                self._require_slot_state(
                    before_dialog,
                    target_grid,
                    target_index,
                    False,
                    f"лот продажу {lot.name}",
                )
                input_driver.drag(best.point.x, best.point.y, target.x, target.y)
            self._sleep(config.dialogs.open_delay)
            self._fill_dialog(capture, input_driver, lot, config, before=before_dialog)
            after = self._wait_for_panels(capture, config)
            self._require_item_at(
                after,
                template,
                target,
                config.match_threshold,
                f"створений лот {lot.name}",
            )
            if lot.side is LotSide.SALE:
                sale_index += 1
            else:
                buy_index += 1
            self._sleep(config.action_delay)

        self._set(stage="finishing")
        if config.shop_name:
            point = config.geometry.shop_name
            input_driver.click(point.x, point.y)
            # PW's custom edit control can ignore Ctrl while still accepting
            # the A key. With a Cyrillic layout that leaves a leading "ф" in
            # the shop name. The caret is placed at the end by the click, so
            # repeated Backspace is slower but deterministic in background mode.
            input_driver.clear_text(100)
            input_driver.type_text(config.shop_name)
        # The shop contents must be confirmed before the client can switch the
        # character to offline trade.  Offline mode always implies this click,
        # even if click_ok was disabled in an old saved config.
        if config.click_ok or config.offline_trade:
            point = config.geometry.ok_button
            input_driver.click(point.x, point.y)
            self._sleep(max(0.8, config.action_delay))
        if config.offline_trade:
            # Confirming the shop normally closes the backpack. From this point
            # only the still-visible shop is required for the offline button.
            offline_driver = self._guard_driver(
                capture,
                input_driver,
                config,
                require_bag=False,
            )
            point = config.geometry.offline_button
            offline_driver.click(point.x, point.y)
            self._sleep(0.40)
            try:
                after_first = capture.grab()
            except Exception:
                return
            if locate_shop(after_first) is None:
                # The first click was accepted and PW is transitioning to the
                # offline client. Never click the now-missing button again.
                deadline = time.monotonic() + 10.0
                while True:
                    try:
                        capture.grab()
                    except Exception:
                        return
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            "офлайн-лавка почала запуск, але ігрове вікно не закрилося"
                        )
                    self._sleep(0.25)
            # ComebackPW's offline-trade control behaves like a double-click
            # button; a single click only focuses it and shows its tooltip.
            offline_driver.click(point.x, point.y)
            self._sleep(max(2.0, config.action_delay))
            deadline = time.monotonic() + 10.0
            while True:
                try:
                    capture.grab()
                except Exception:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("офлайн-лавка не запустилася: ігрове вікно не закрилося")
                self._sleep(0.50)
        else:
            self._log("офлайн-лавку не натискаю (тестовий режим)")

    def _run_profile(
        self,
        dry_run: bool,
        profile_id: str | None,
        character_id: str | None = None,
    ) -> list[str]:
        character = None
        if character_id:
            character = next(
                (item for item in self.repository.characters() if item.id == character_id),
                None,
            )
            if character is None:
                raise RuntimeError("Персонажа не знайдено")
            profile_id = character.profile_id
        if profile_id is None:
            raise RuntimeError("Для персонажа не вибрано сценарій")
        profile = next((item for item in self.repository.profiles() if item.id == profile_id), None)
        if profile is None:
            raise RuntimeError("Сценарій не знайдено")
        catalog = {item.id: item for item in self.repository.catalog()}
        entries = [entry for entry in profile.entries if entry.enabled and entry.item_id in catalog]
        if not entries:
            raise RuntimeError("У профілі немає увімкнених предметів")

        base = self.repository.config()
        catalog = self._refresh_market_prices(entries, catalog, base)
        entry_prices: dict[str, tuple[int | None, int | None]] = {}
        for entry in entries:
            item = catalog[entry.item_id]
            sale_price = (
                self._profile_price(item.name, entry, item.market_sell, True)
                if entry.sale_enabled
                else None
            )
            buy_price = (
                self._profile_price(item.name, entry, item.market_buy, False)
                if entry.buy_enabled
                else None
            )
            sale_price, buy_price, used_default = apply_default_spread_strategy(
                sale_price,
                buy_price,
            )
            ensure_profitable_spread(item.name, sale_price, buy_price)
            if used_default:
                self._log(
                    f"{item.name}: ринок перехрещений — скупку пропускаю, "
                    f"продаж ставлю {sale_price:,} (+10% від топ-скупки)"
                )
            entry_prices[entry.item_id] = (sale_price, buy_price)
        window_hwnd = character.window_hwnd if character else profile.window_hwnd
        window_index = character.window_index if character else profile.window_index
        shop_name = character.shop_name if character else profile.shop_name
        config = base.model_copy(
            deep=True,
            update={
                "window_hwnd": window_hwnd if window_hwnd is not None else base.window_hwnd,
                "window_index": window_index,
                "shop_name": shop_name,
                "offline_trade": character.offline_trade if character else base.offline_trade,
            },
        )
        if character:
            self._log(f"{character.character_name}: сценарій «{profile.name}»")
        window = resolve_window(
            config.window_hwnd,
            config.window_index,
            character.window_title if character else None,
        )
        if character and character.window_hwnd != window.hwnd:
            character = character.model_copy(update={"window_hwnd": window.hwnd})
            self.repository.replace_character(character)
        self._validate_window(window, config)
        capture = WindowCapture(window.hwnd)
        raw_driver = make_input(config.input_mode, window.hwnd, config.drag_duration)

        if dry_run:
            frame = capture.grab()
            bag_anchor = locate_bag(frame)
            if bag_anchor is None:
                raise RuntimeError("Для перевірки відкрийте рюкзак у грі — dry-run не натискає клавіші")
            config = config.model_copy(
                deep=True,
                update={
                    "geometry": geometry_for_frame(
                        config.geometry,
                        bag_anchor=bag_anchor,
                    )
                },
            )
        else:
            config = self._open_windows(capture, raw_driver, config)
            driver = self._guard_driver(capture, raw_driver, config)
            self._set(stage="returning")
            self._log("Повертаю предмети з поточної лавки для перевиставлення")
            self._return_existing_lots(capture, driver, config)
            config = self._open_windows(capture, raw_driver, config)
            driver = self._guard_driver(capture, raw_driver, config)

        if dry_run:
            driver = self._guard_driver(capture, raw_driver, config)

        self._set(stage="inventory")
        self._log("Рахую предмети у рюкзаку через підказки PW")
        templates = {
            entry.item_id: load_icon(self.repository.icons / catalog[entry.item_id].icon_file)
            for entry in entries
        }
        tooltip_driver = None
        previous_foreground = None
        previous_cursor = None
        if config.input_mode is InputMode.BACKGROUND:
            # PW does not render stack tooltips from window messages.  Borrow
            # the physical cursor only while reading quantities, then return
            # every click and drag to the background driver.
            foreground_driver = make_input(InputMode.FOREGROUND, window.hwnd, config.drag_duration)
            tooltip_driver = self._guard_driver(capture, foreground_driver, config)
            import win32api
            import win32gui

            previous_foreground = win32gui.GetForegroundWindow()
            previous_cursor = win32api.GetCursorPos()
            self._log(
                "Фонове керування: нижні зразки читаю без фізичної мишки; "
                "курсор використовую лише для чисел у верхніх стеках"
            )
        try:
            stacks = scan_templates(
                capture,
                driver,
                templates,
                config.geometry.bag_grid,
                config.match_threshold,
                wait=self._sleep,
                tooltip_driver=tooltip_driver,
            )
        finally:
            if previous_foreground is not None and previous_cursor is not None:
                import win32api
                import win32gui

                # Do not override a window the user selected while scanning.
                # Restore only when our PW client is still the foreground owner.
                if win32gui.GetForegroundWindow() == window.hwnd:
                    win32api.SetCursorPos(previous_cursor)
                    if win32gui.IsWindow(previous_foreground):
                        try:
                            win32gui.SetForegroundWindow(previous_foreground)
                        except win32gui.error:
                            win32gui.BringWindowToTop(previous_foreground)
        funds_frame = capture.grab() if dry_run else self._wait_for_panels(capture, config)
        free_funds = detect_free_funds(
            funds_frame,
            locate_shop(funds_frame),
            locate_bag(funds_frame),
        )
        needs_budget = any(
            entry.buy_enabled and entry_prices[entry.item_id][1] is not None
            for entry in entries
        )
        if needs_budget and free_funds is None:
            raise RuntimeError(
                "Безпечна зупинка: не вдалося прочитати вільні кошти для скупки"
            )
        remaining_buy_funds = free_funds
        lots: list[Lot] = []
        quantities: dict[str, dict[str, int]] = {}
        missing_buy_samples: list[str] = []
        for entry in entries:
            item = catalog[entry.item_id]
            item_stacks = stacks.get(entry.item_id, [])
            owned = sum(stack.quantity for stack in item_stacks)
            sale_slot_count = config.geometry.bag_grid.count // 2
            top_owned = sum(
                stack.quantity for stack in item_stacks if stack.match.index < sale_slot_count
            )
            protected_owned = owned - top_owned
            sale_quantity, buy_quantity, _needs_sample = layout_quantities(
                item_stacks,
                entry.max_owned,
                entry.sale_enabled,
                entry.buy_enabled and entry_prices[entry.item_id][1] is not None,
                config.geometry.bag_grid.count // 2,
                item.stack_limit,
            )
            buy_price = entry_prices[entry.item_id][1]
            if buy_quantity and owned < 1:
                missing_buy_samples.append(item.name)
                buy_quantity = 0
            if buy_quantity and remaining_buy_funds is not None and buy_price is not None:
                requested = buy_quantity
                buy_quantity = min(buy_quantity, remaining_buy_funds // buy_price)
                remaining_buy_funds -= buy_quantity * buy_price
                if buy_quantity < requested:
                    self._log(
                        f"{item.name}: скупку зменшено з {requested} до {buy_quantity}, "
                        "щоб не перевищити вільні кошти"
                    )
            quantities[entry.item_id] = {
                "owned": owned,
                "top_owned": top_owned,
                "protected_owned": protected_owned,
                "maximum": entry.max_owned,
                "sale": sale_quantity,
                "buy": buy_quantity,
            }
            self._log(
                f"{item.name}: верх {top_owned}, низ-зразок {protected_owned}, "
                f"усього {owned}, максимум {entry.max_owned}, "
                f"продаж {sale_quantity}, скупка {buy_quantity}"
            )
            if sale_quantity:
                lots.append(
                    Lot(
                        name=item.name,
                        side=LotSide.SALE,
                        price=entry_prices[entry.item_id][0],
                        quantity=sale_quantity,
                        icon_file=item.icon_file,
                    )
                )
            if buy_quantity:
                lots.append(
                    Lot(
                        name=item.name,
                        side=LotSide.BUY,
                        price=entry_prices[entry.item_id][1],
                        quantity=buy_quantity,
                        icon_file=item.icon_file,
                    )
                )

        warnings: list[str] = []
        if missing_buy_samples:
            warning = "Немає предмета-зразка для скупки: " + ", ".join(missing_buy_samples)
            if not lots:
                raise RuntimeError(warning)
            warnings.append(warning)
            self._log(f"Попередження: {warning}; ці заявки на скупку пропускаю")
        if not lots:
            raise RuntimeError("За поточним інвентарем профіль не створює жодного лота")
        frame = capture.grab() if dry_run else self._wait_for_panels(capture, config)
        # Offline trade closes the client window during finalization, so any
        # statistics that require a screenshot were read before filling.
        plan = self._plan(frame, lots, config)
        preview = []
        for planned in plan:
            row = planned.as_dict()
            item_id = next(
                (entry.item_id for entry in entries if catalog[entry.item_id].icon_file == planned.lot.icon_file),
                "",
            )
            row.update(quantities.get(item_id, {}))
            preview.append(row)
        self._set(stage="planned", preview=preview)
        if len(plan) != len(lots):
            raise RuntimeError(f"Не знайдено {len(lots) - len(plan)} предметів профілю у рюкзаку")
        if dry_run:
            self._log("Dry-run завершено: кліків не було, показано розраховані кількості")
            return warnings
        inventory_stacks = {
            catalog[item_id].icon_file: item_stacks
            for item_id, item_stacks in stacks.items()
        }
        self._fill_lots(capture, driver, lots, config, inventory_stacks)
        if character:
            sale_value = sum(
                lot.price * lot.quantity for lot in lots if lot.side is LotSide.SALE
            )
            updates = {
                "sale_value": sale_value,
                "stats_updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if config.offline_trade:
                updates["window_hwnd"] = None
            if free_funds is not None:
                updates["free_funds"] = free_funds
            self.repository.replace_character(character.model_copy(update=updates))
            total = sale_value + (free_funds or character.free_funds or 0)
            self._log(f"Оцінка лавки: {total:,} монет")
        return warnings

    def _refresh_market_prices(
        self,
        entries: list[ProfileEntry],
        catalog: dict[str, CatalogItem],
        config: AppConfig,
    ) -> dict[str, CatalogItem]:
        wanted = [
            catalog[entry.item_id]
            for entry in entries
            if catalog[entry.item_id].market_item_id is not None
        ]
        if not wanted:
            self._log("У стратегії немає товарів з item ID — ціни не оновлюю")
            return catalog

        self._set(stage="prices")
        self._log(f"Оновлюю ціни перед запуском: {len(wanted)} товарів")
        quotes = {}
        errors = []

        def fetch(item: CatalogItem):
            return fetch_quote(
                item.market_item_id,
                url_template=config.market_url_template,
                max_pages=config.market_max_pages,
            )

        with ThreadPoolExecutor(max_workers=min(6, len(wanted))) as pool:
            pending = {pool.submit(fetch, item): item for item in wanted}
            for future in as_completed(pending):
                item = pending[future]
                try:
                    quotes[item.id] = future.result()
                except Exception as error:
                    errors.append(f"{item.name}: {error}")

        if errors:
            raise RuntimeError("Не вдалося оновити ринкові ціни: " + "; ".join(errors))

        timestamp = datetime.now(timezone.utc).isoformat()
        next_catalog = []
        for item in self.repository.catalog():
            quote = quotes.get(item.id)
            if quote is None:
                next_catalog.append(item)
                continue
            next_catalog.append(
                item.model_copy(
                    update={
                        "name": quote.name or item.name,
                        "market_sell": quote.recommended_sell,
                        "market_buy": quote.recommended_buy,
                        "market_updated_at": timestamp,
                    }
                )
            )
        self.repository.save_catalog(next_catalog)
        self._log(f"Ціни оновлено: {len(quotes)} товарів")
        return {item.id: item for item in next_catalog}

    @staticmethod
    def _profile_price(
        name: str,
        entry: ProfileEntry,
        market_price: int | None,
        sale: bool,
    ) -> int:
        mode = entry.sale_price_mode if sale else entry.buy_price_mode
        manual = entry.sale_price if sale else entry.buy_price
        adjustment = entry.sale_adjustment if sale else entry.buy_adjustment
        try:
            return adjusted_price(mode, manual, market_price, adjustment, sale=sale)
        except ValueError as error:
            side = "продажу" if sale else "скупки"
            raise RuntimeError(f"{name}: помилка ціни {side}: {error}") from error

    def _prepare_buy_samples(
        self,
        capture: WindowCapture,
        driver: InputDriver,
        lots: list[Lot],
        config: AppConfig,
        inventory_stacks: dict[str, list[InventoryStack]] | None = None,
    ) -> tuple[
        dict[str, Point],
        set[int],
        list[tuple[Image.Image, Match]],
        dict[str, BuyReservation],
    ]:
        samples = {}
        sample_indexes: set[int] = set()
        originals: list[tuple[Image.Image, Match]] = []
        reservations: dict[str, BuyReservation] = {}
        for lot in (item for item in lots if item.side is LotSide.BUY):
            self._check_stop()
            fresh = self._wait_for_panels(capture, config)
            template = load_icon(self.repository.icons / lot.icon_file)
            known = (inventory_stacks or {}).get(lot.icon_file, [])
            sale_slot_count = config.geometry.bag_grid.count // 2
            bottom_samples = [stack for stack in known if stack.match.index >= sale_slot_count]
            if bottom_samples:
                sample = max(bottom_samples, key=lambda stack: stack.quantity)
                best = sample.match
                self._log(f"{lot.name}: використовую недоторканний зразок з нижніх 16 комірок")
                samples[lot.id] = best.point
                sample_indexes.add(best.index)
                originals.append((template, best))
                reservations[lot.icon_file] = BuyReservation(best.index, split=False)
                continue
            top_stacks = [stack for stack in known if stack.match.index < sale_slot_count]
            recognized_stack = bool(top_stacks)
            source = max(top_stacks, key=lambda stack: stack.quantity) if top_stacks else None
            best = source.match if source else search_grid(fresh, template, config.geometry.bag_grid)[0]
            if not recognized_stack and best.score < config.match_threshold:
                raise RuntimeError(
                    f"{lot.name}: предмет для скупки не знайдено "
                    f"(збіг {best.score:.2f}, потрібно {config.match_threshold:.2f})"
                )
            bottom_indexes = set(range(sale_slot_count, config.geometry.bag_grid.count))
            empty = find_empty_cell(
                fresh,
                config.geometry.bag_grid,
                {best.index},
                allowed=bottom_indexes,
            )
            if empty is None:
                raise RuntimeError(f"{lot.name}: у нижніх 16 комірках немає місця для зразка")
            self._require_item_at(
                fresh,
                template,
                best.point,
                config.match_threshold,
                lot.name,
            )
            self._require_slot_state(
                fresh,
                config.geometry.bag_grid,
                empty.index,
                False,
                f"місце зразка {lot.name}",
            )
            if source is not None and source.quantity == 1:
                self._log(f"{lot.name}: переношу єдину 1 шт. у нижні 16 як зразок")
                driver.drag(best.point.x, best.point.y, empty.point.x, empty.point.y)
            else:
                self._log(f"{lot.name}: відділяю 1 шт. через Alt у нижні 16 для скупки")
                driver.drag(best.point.x, best.point.y, empty.point.x, empty.point.y, modifier="alt")
                self._sleep(config.dialogs.open_delay)
                accept = config.geometry.split_accept
                self._require_dialog(capture, config, accept, fresh)
                driver.click(accept.x, accept.y)
            self._sleep(config.action_delay)
            after = self._wait_for_panels(capture, config)
            self._require_item_at(
                after,
                template,
                empty.point,
                config.match_threshold,
                f"зразок {lot.name}",
            )
            samples[lot.id] = empty.point
            sample_indexes.add(empty.index)
            originals.append((template, best))
            reservations[lot.icon_file] = BuyReservation(best.index, split=True)
        return samples, sample_indexes, originals, reservations

    def _plan(self, frame: Image.Image, lots: list[Lot], config: AppConfig) -> list[PlannedLot]:
        result: list[PlannedLot] = []
        sale_index = buy_index = 0
        for lot in lots:
            path = self.repository.icons / lot.icon_file
            if not path.exists():
                continue
            matches = search_grid(frame, load_icon(path), config.geometry.bag_grid)
            sale_slot_count = config.geometry.bag_grid.count // 2
            if lot.side is LotSide.SALE:
                eligible = [item for item in matches if item.index < sale_slot_count]
            else:
                bottom = [item for item in matches if item.index >= sale_slot_count]
                eligible = bottom if bottom and bottom[0].score >= config.match_threshold else matches
            match = eligible[0]
            if match.score < config.match_threshold:
                continue
            grid = config.geometry.sale_grid if lot.side is LotSide.SALE else config.geometry.buy_grid
            index = sale_index if lot.side is LotSide.SALE else buy_index
            if index >= grid.count:
                continue
            destination = grid.point(index)
            result.append(PlannedLot(lot, match, destination.x, destination.y))
            if lot.side is LotSide.SALE:
                sale_index += 1
            else:
                buy_index += 1
        return result

    @staticmethod
    def _validate_window(window: GameWindow, config: AppConfig) -> None:
        geometry = config.geometry
        if window.iconic:
            raise RuntimeError("вікно гри згорнуте")
        if (window.width, window.height) != (geometry.client_width, geometry.client_height):
            raise RuntimeError(
                f"розмір гри {window.width}x{window.height}; профіль очікує "
                f"{geometry.client_width}x{geometry.client_height}"
            )

    def _open_windows(
        self,
        capture: WindowCapture,
        driver: InputDriver,
        config: AppConfig,
    ) -> AppConfig:
        frame = capture.grab()
        shop_anchor = locate_shop(frame)
        if shop_anchor is None:
            if not config.open_shop:
                raise RuntimeError("лавка не відкрита")
            self._set(stage="opening_shop")
            deadline = time.monotonic() + 20.0
            attempt = 0
            foreground_key_driver = None
            while time.monotonic() < deadline:
                self._check_stop()
                frame = capture.grab()
                shop_anchor = locate_shop(frame)
                if shop_anchor is not None:
                    break
                attempt += 1
                self._log(
                    f"відкриваю лавку клавішею {config.shop_key.upper()} "
                    f"(спроба {attempt})"
                )
                key_driver = driver
                if config.input_mode is InputMode.BACKGROUND and attempt >= 3:
                    if foreground_key_driver is None:
                        foreground_key_driver = make_input(
                            InputMode.FOREGROUND,
                            capture.hwnd,
                            config.drag_duration,
                        )
                    key_driver = foreground_key_driver
                    if attempt == 3:
                        self._log("PW не прийняв фоновий F1 — натискаю F1 фізично один раз")
                key_driver.press(config.shop_key)
                self._sleep(max(2.0, config.action_delay))
            frame = capture.grab()
            shop_anchor = locate_shop(frame)
        if shop_anchor is None:
            raise RuntimeError("не бачу вікно «Лавка» після повторних спроб F1 протягом 20 с")
        bag_anchor = locate_bag(frame)
        if bag_anchor is None:
            self._log("відкриваю рюкзак")
            driver.press(config.bag_key)
            self._sleep(config.action_delay)
            frame = capture.grab()
            bag_anchor = locate_bag(frame)
        if bag_anchor is None:
            raise RuntimeError("не бачу вікно «Рюкзак»")
        geometry = geometry_for_frame(
            config.geometry,
            shop_anchor=shop_anchor,
            bag_anchor=bag_anchor,
        )
        self._log(
            f"Панелі знайдено: лавка ({shop_anchor.x}, {shop_anchor.y}), "
            f"рюкзак ({bag_anchor.x}, {bag_anchor.y})"
        )
        return config.model_copy(deep=True, update={"geometry": geometry})

    def _return_existing_lots(
        self,
        capture: WindowCapture,
        driver: InputDriver,
        config: AppConfig,
    ) -> None:
        grids = (config.geometry.sale_grid, config.geometry.buy_grid)
        maximum_attempts = sum(grid.count for grid in grids) + 4
        returned = 0
        for _ in range(maximum_attempts):
            frame = self._wait_for_panels(capture, config)
            occupied = [
                (grid, index)
                for grid in grids
                for index in occupied_grid_indexes(frame, grid)
            ]
            if not occupied:
                if returned:
                    self._log(f"Старі лоти повернуто в рюкзак: {returned}")
                else:
                    self._log("Старих лотів немає")
                return
            grid, index = occupied[0]
            slot = grid.point(index)
            self._require_slot_state(frame, grid, index, True, "лот для повернення")
            driver.click(slot.x, slot.y)
            self._sleep(0.2)
            selected = self._wait_for_panels(capture, config)
            slot_box = (
                max(0, slot.x - 16),
                max(0, slot.y - 16),
                min(selected.width, slot.x + 16),
                min(selected.height, slot.y + 16),
            )
            if image_changed(frame, selected, slot_box) < 1.0:
                raise RuntimeError(
                    "Безпечна зупинка: клік по лоту не змінив його підсвітку; "
                    "кнопку повернення не натискаю"
                )
            button = config.geometry.return_button
            driver.click(button.x, button.y)
            self._sleep(max(0.8, config.action_delay))
            after = self._wait_for_panels(capture, config)
            self._require_slot_state(after, grid, index, False, "повернений лот")
            returned += 1
        raise RuntimeError("не вдалося повернути всі старі лоти в рюкзак")

    def _fill_dialog(
        self,
        capture: WindowCapture,
        driver: InputDriver,
        lot: Lot,
        config: AppConfig,
        quantity: int | None = None,
        before: Image.Image | None = None,
    ) -> None:
        self._require_dialog(capture, config, config.geometry.dialog_accept, before)
        self._replace_dialog_value(
            capture,
            driver,
            config.geometry.dialog_price,
            str(lot.price),
            config,
            before,
        )
        self._replace_dialog_value(
            capture,
            driver,
            config.geometry.dialog_quantity,
            str(lot.quantity if quantity is None else quantity),
            config,
            before,
        )
        point = config.geometry.dialog_accept
        before_accept = self._require_dialog(capture, config, point, before)
        box = (
            max(0, point.x - 140),
            max(0, point.y - 115),
            min(before_accept.width, point.x + 170),
            min(before_accept.height, point.y + 35),
        )
        for _attempt in range(3):
            self._require_dialog(capture, config, point, before)
            driver.click(point.x, point.y)
            self._sleep(max(0.5, config.dialogs.field_delay))
            after_accept = capture.grab()
            if image_changed(before_accept, after_accept, box) >= 2.0:
                return
        raise RuntimeError(f"{lot.name}: PW не підтвердила діалог ціни/кількості")

    def _replace_dialog_value(
        self,
        capture: WindowCapture,
        driver: InputDriver,
        point,
        value: str,
        config: AppConfig,
        before: Image.Image | None,
    ) -> None:
        self._require_dialog(capture, config, config.geometry.dialog_accept, before)
        driver.click(point.x, point.y)
        # The PW custom edit control ignores Ctrl+A in background mode. Clicking the
        # right side puts the caret at the end, so repeated Backspace is reliable.
        driver.clear_text(16)
        driver.type_text(value)
        self._sleep(config.dialogs.field_delay)


runner: ShopRunner | None = None


def get_runner(repository: Repository) -> ShopRunner:
    global runner
    if runner is None or runner.repository is not repository:
        runner = ShopRunner(repository)
    return runner
