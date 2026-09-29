from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.account_queue import get_account_queue
from app.capture import WindowCapture
from app.game_state import detect_game_state
from app.launcher import LauncherManager
from app.market import fetch_icon, fetch_quote
from app.models import (
    AppConfig,
    CatalogCreate,
    CatalogItem,
    CatalogUpdate,
    Character,
    CharacterCreate,
    CharacterUpdate,
    Lot,
    LotCreate,
    LotUpdate,
    LauncherUpdate,
    MarketImport,
    Profile,
    ProfileCreate,
    ProfileUpdate,
    QueueRequest,
    RunRequest,
)
from app.shop import get_runner
from app.storage import repository
from app.vision import crop_icon
from app.windows import game_windows, resolve_window

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"

app = FastAPI(title="PwCatBot", version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
launcher_manager = LauncherManager(repository.root)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "dashboard.html").read_text(encoding="utf-8"))


@app.get("/strategies", response_class=HTMLResponse)
def strategies_page() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/items", response_class=HTMLResponse)
def items_page() -> HTMLResponse:
    return HTMLResponse((STATIC / "items.html").read_text(encoding="utf-8"))


@app.get("/windows", response_class=HTMLResponse)
def windows_page() -> HTMLResponse:
    return HTMLResponse((STATIC / "windows.html").read_text(encoding="utf-8"))


@app.get("/api/state")
def state() -> dict:
    windows = game_windows()
    live_hwnds = {window.hwnd for window in windows}
    characters = []
    for item in repository.characters():
        if item.window_hwnd is not None and item.window_hwnd not in live_hwnds:
            stale_hwnd = item.window_hwnd
            updates = {"window_hwnd": None}
            if (
                item.last_run_status == "error"
                and item.last_run_message == f"вікно {stale_hwnd} не знайдено"
            ):
                updates.update(
                    last_run_status="idle",
                    last_run_message="Клієнт закрито",
                )
            item = item.model_copy(update=updates)
            repository.replace_character(item)
        row = item.model_dump(mode="json")
        row["has_password"] = launcher_manager.credentials.has(item.id)
        row["total_value"] = (item.free_funds or 0) + (item.sale_value or 0)
        characters.append(row)
    return {
        "windows": [window.as_dict() for window in windows],
        "config": repository.config().model_dump(mode="json"),
        "lots": [lot.model_dump(mode="json") for lot in repository.lots()],
        "catalog": [item.model_dump(mode="json") for item in repository.catalog()],
        "profiles": [profile.model_dump(mode="json") for profile in repository.profiles()],
        "characters": characters,
        "job": get_runner(repository).snapshot().model_dump(mode="json"),
        "queue": get_account_queue(repository).snapshot().model_dump(mode="json"),
    }


@app.put("/api/config")
def save_config(config: AppConfig) -> dict:
    return repository.save_config(config).model_dump(mode="json")


@app.get("/api/frame")
def frame(hwnd: int | None = None) -> StreamingResponse:
    config = repository.config()
    window = resolve_window(hwnd or config.window_hwnd, config.window_index)
    image = WindowCapture(window.hwnd).grab()
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return StreamingResponse(output, media_type="image/png", headers={"Cache-Control": "no-store"})


def _icon_response(icon_file: str) -> FileResponse:
    path = repository.icons / icon_file
    if not path.exists():
        raise HTTPException(404, "Іконку не знайдено")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


def _nearest_bag_point(x: int, y: int):
    config = repository.config()
    grid = config.geometry.bag_grid
    points = [grid.point(index) for index in range(grid.count)]
    nearest = min(points, key=lambda point: (point.x - x) ** 2 + (point.y - y) ** 2)
    distance = ((nearest.x - x) ** 2 + (nearest.y - y) ** 2) ** 0.5
    if distance > max(abs(grid.step.x), abs(grid.step.y)) * 0.65:
        raise HTTPException(400, "Клацніть у центр комірки рюкзака")
    return nearest, grid


@app.get("/api/catalog/{item_id}/icon")
def catalog_icon(item_id: str) -> FileResponse:
    item = next((value for value in repository.catalog() if value.id == item_id), None)
    if item is None:
        raise HTTPException(404, "Предмет не знайдено")
    return _icon_response(item.icon_file)


@app.post("/api/catalog")
def add_catalog_item(payload: CatalogCreate) -> dict:
    config = repository.config()
    window = resolve_window(config.window_hwnd, config.window_index)
    image = WindowCapture(window.hwnd).grab()
    nearest, grid = _nearest_bag_point(payload.x, payload.y)
    item = CatalogItem(
        name=payload.name,
        icon_file="pending.png",
        market_item_id=payload.market_item_id,
        stack_limit=payload.stack_limit,
    )
    item.icon_file = f"catalog-{item.id}.png"
    crop_icon(image, nearest.x, nearest.y, grid.icon_size).save(repository.icons / item.icon_file)
    repository.add_catalog_item(item)
    return item.model_dump(mode="json")


def _market_update(item: CatalogItem) -> CatalogItem:
    if item.market_item_id is None:
        raise HTTPException(400, "Для предмета не задано ComebackPW item ID")
    try:
        config = repository.config()
        quote = fetch_quote(
            item.market_item_id,
            url_template=config.market_url_template,
            max_pages=config.market_max_pages,
        )
    except Exception as error:
        raise HTTPException(502, f"Не вдалося прочитати ComebackPW: {error}") from error
    updated = item.model_copy(
        update={
            "name": quote.name or item.name,
            "market_sell": quote.recommended_sell,
            "market_buy": quote.recommended_buy,
            "market_updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    if quote.icon_url:
        try:
            fetch_icon(quote.icon_url).save(repository.icons / updated.icon_file)
        except Exception:
            pass
    repository.replace_catalog_item(updated)
    return updated


@app.post("/api/catalog/import-market")
def import_market_item(payload: MarketImport) -> dict:
    duplicate = next(
        (item for item in repository.catalog() if item.market_item_id == payload.item_id),
        None,
    )
    if duplicate is not None:
        return _market_update(duplicate).model_dump(mode="json")
    try:
        config = repository.config()
        quote = fetch_quote(
            payload.item_id,
            url_template=config.market_url_template,
            max_pages=config.market_max_pages,
        )
        image = fetch_icon(quote.icon_url)
    except Exception as error:
        raise HTTPException(502, f"Не вдалося імпортувати з ComebackPW: {error}") from error
    item = CatalogItem(
        name=quote.name,
        icon_file="pending.png",
        market_item_id=payload.item_id,
        market_sell=quote.recommended_sell,
        market_buy=quote.recommended_buy,
        market_updated_at=datetime.now(timezone.utc).isoformat(),
    )
    item.icon_file = f"catalog-{item.id}.png"
    image.save(repository.icons / item.icon_file)
    repository.add_catalog_item(item)
    return item.model_dump(mode="json")


@app.post("/api/catalog/{item_id}/market")
def refresh_market_item(item_id: str) -> dict:
    item = next((value for value in repository.catalog() if value.id == item_id), None)
    if item is None:
        raise HTTPException(404, "Предмет не знайдено")
    return _market_update(item).model_dump(mode="json")


@app.patch("/api/catalog/{item_id}")
def update_catalog_item(item_id: str, payload: CatalogUpdate) -> dict:
    item = next((value for value in repository.catalog() if value.id == item_id), None)
    if item is None:
        raise HTTPException(404, "Предмет не знайдено")
    updated = item.model_copy(update=payload.model_dump(exclude_unset=True))
    repository.replace_catalog_item(updated)
    return updated.model_dump(mode="json")


@app.delete("/api/catalog/{item_id}", status_code=204)
def delete_catalog_item(item_id: str) -> None:
    try:
        repository.delete_catalog_item(item_id)
    except KeyError:
        raise HTTPException(404, "Предмет не знайдено")


def _validate_profile(profile: Profile) -> None:
    available = {item.id for item in repository.catalog()}
    ids = [entry.item_id for entry in profile.entries]
    missing = sorted(set(ids) - available)
    if missing:
        raise HTTPException(400, f"У базі немає предметів: {', '.join(missing)}")
    if len(ids) != len(set(ids)):
        raise HTTPException(400, "Один предмет не можна додати у профіль двічі")
    sale_count = sum(entry.enabled and entry.sale_enabled for entry in profile.entries)
    buy_count = sum(entry.enabled and entry.buy_enabled for entry in profile.entries)
    if sale_count > 20 or buy_count > 20:
        raise HTTPException(400, "У кожній стороні лавки може бути не більше 20 лотів")


@app.post("/api/profiles")
def add_profile(payload: ProfileCreate) -> dict:
    profile = Profile(**payload.model_dump())
    repository.add_profile(profile)
    return profile.model_dump(mode="json")


@app.post("/api/profiles/{profile_id}/copy")
def copy_profile(profile_id: str) -> dict:
    profile = next((value for value in repository.profiles() if value.id == profile_id), None)
    if profile is None:
        raise HTTPException(404, "Профіль не знайдено")
    names = {value.name for value in repository.profiles()}
    base = f"{profile.name} — копія"
    name = base
    suffix = 2
    while name in names:
        name = f"{base} {suffix}"
        suffix += 1
    copied = profile.model_copy(deep=True, update={"id": uuid4().hex, "name": name})
    repository.add_profile(copied)
    return copied.model_dump(mode="json")


@app.post("/api/profiles/{profile_id}/market")
def refresh_profile_market(profile_id: str) -> dict:
    profile = next((value for value in repository.profiles() if value.id == profile_id), None)
    if profile is None:
        raise HTTPException(404, "Профіль не знайдено")
    catalog = repository.catalog()
    wanted_ids = {entry.item_id for entry in profile.entries}
    wanted = [item for item in catalog if item.id in wanted_ids and item.market_item_id is not None]
    if not wanted:
        raise HTTPException(400, "У предметів профілю не задані ComebackPW item ID")

    def fetch_bundle(item: CatalogItem):
        config = repository.config()
        quote = fetch_quote(
            item.market_item_id,
            url_template=config.market_url_template,
            max_pages=config.market_max_pages,
        )
        image = None
        if quote.icon_url:
            try:
                image = fetch_icon(quote.icon_url)
            except Exception:
                pass
        return quote, image

    quotes = {}
    icons = {}
    errors = []
    for item in wanted:
        try:
            quote, image = fetch_bundle(item)
            quotes[item.id] = quote
            icons[item.id] = image
        except Exception as error:
            errors.append({"item_id": item.id, "name": item.name, "error": str(error)})

    timestamp = datetime.now(timezone.utc).isoformat()
    updated_items = []
    next_catalog = []
    for item in catalog:
        quote = quotes.get(item.id)
        if quote is None:
            next_catalog.append(item)
            continue
        updated = item.model_copy(
            update={
                "name": quote.name or item.name,
                "market_sell": quote.recommended_sell,
                "market_buy": quote.recommended_buy,
                "market_updated_at": timestamp,
            }
        )
        next_catalog.append(updated)
        updated_items.append(updated.model_dump(mode="json"))
        image = icons.get(item.id)
        if image is not None:
            image.save(repository.icons / updated.icon_file)
    repository.save_catalog(next_catalog)
    return {"updated": updated_items, "errors": errors}


@app.patch("/api/profiles/{profile_id}")
def update_profile(profile_id: str, payload: ProfileUpdate) -> dict:
    profile = next((value for value in repository.profiles() if value.id == profile_id), None)
    if profile is None:
        raise HTTPException(404, "Профіль не знайдено")
    updated = Profile.model_validate(
        {**profile.model_dump(mode="python"), **payload.model_dump(exclude_unset=True, mode="python")}
    )
    _validate_profile(updated)
    repository.replace_profile(updated)
    return updated.model_dump(mode="json")


def _validate_character(character: Character) -> None:
    if not any(profile.id == character.profile_id for profile in repository.profiles()):
        raise HTTPException(400, "Сценарій не знайдено")


@app.post("/api/characters")
def add_character(payload: CharacterCreate) -> dict:
    character = Character(**payload.model_dump())
    _validate_character(character)
    repository.add_character(character)
    return character.model_dump(mode="json")


@app.patch("/api/characters/{character_id}")
def update_character(character_id: str, payload: CharacterUpdate) -> dict:
    character = next(
        (value for value in repository.characters() if value.id == character_id),
        None,
    )
    if character is None:
        raise HTTPException(404, "Персонажа не знайдено")
    updated = Character.model_validate(
        {**character.model_dump(mode="python"), **payload.model_dump(exclude_unset=True, mode="python")}
    )
    _validate_character(updated)
    repository.replace_character(updated)
    if updated.launcher_file and updated.game_dir and updated.login and updated.window_title:
        try:
            updated = launcher_manager.configure(
                updated,
                LauncherUpdate(
                    game_dir=updated.game_dir,
                    login=updated.login,
                    window_title=updated.window_title,
                ),
            )
            repository.replace_character(updated)
        except RuntimeError:
            pass
    return updated.model_dump(mode="json")


@app.put("/api/characters/{character_id}/launcher")
def configure_character_launcher(character_id: str, payload: LauncherUpdate) -> dict:
    character = next((value for value in repository.characters() if value.id == character_id), None)
    if character is None:
        raise HTTPException(404, "Персонажа не знайдено")
    try:
        updated = launcher_manager.configure(character, payload)
    except RuntimeError as error:
        raise HTTPException(400, str(error)) from error
    repository.replace_character(updated)
    row = updated.model_dump(mode="json")
    row["has_password"] = launcher_manager.credentials.has(updated.id)
    row["total_value"] = (updated.free_funds or 0) + (updated.sale_value or 0)
    return row


@app.post("/api/characters/{character_id}/launch")
def launch_character(character_id: str) -> dict:
    character = next((value for value in repository.characters() if value.id == character_id), None)
    if character is None:
        raise HTTPException(404, "Персонажа не знайдено")
    try:
        launcher_manager.launch(character)
    except RuntimeError as error:
        raise HTTPException(400, str(error)) from error
    return {"launched": True, "character_id": character.id}


@app.post("/api/characters/{character_id}/detect")
def detect_character_state(character_id: str) -> dict:
    character = next((value for value in repository.characters() if value.id == character_id), None)
    if character is None:
        raise HTTPException(404, "Персонажа не знайдено")
    try:
        window = resolve_window(
            character.window_hwnd,
            character.window_index,
            character.window_title,
        )
        detected = detect_game_state(WindowCapture(window.hwnd).grab())
    except RuntimeError as error:
        raise HTTPException(400, str(error)) from error
    updates = {"stats_updated_at": detected.detected_at, "window_hwnd": window.hwnd}
    if detected.free_funds is not None:
        updates["free_funds"] = detected.free_funds
    updated = character.model_copy(update=updates)
    repository.replace_character(updated)
    result = detected.as_dict()
    result["sale_value"] = updated.sale_value
    result["total_value"] = (updated.free_funds or 0) + (updated.sale_value or 0)
    return result


@app.delete("/api/characters/{character_id}", status_code=204)
def delete_character(character_id: str) -> None:
    character = next((value for value in repository.characters() if value.id == character_id), None)
    if character is not None:
        launcher_manager.delete(character)
    try:
        repository.delete_character(character_id)
    except KeyError:
        raise HTTPException(404, "Персонажа не знайдено")


@app.delete("/api/profiles/{profile_id}", status_code=204)
def delete_profile(profile_id: str) -> None:
    assigned = [item.character_name for item in repository.characters() if item.profile_id == profile_id]
    if assigned:
        raise HTTPException(409, f"Сценарій призначений персонажам: {', '.join(assigned)}")
    try:
        repository.delete_profile(profile_id)
    except KeyError:
        raise HTTPException(404, "Профіль не знайдено")


# Legacy endpoints remain available for existing data and scripts.
@app.get("/api/lots/{lot_id}/icon")
def lot_icon(lot_id: str) -> FileResponse:
    lot = next((item for item in repository.lots() if item.id == lot_id), None)
    if lot is None:
        raise HTTPException(404, "Лот не знайдено")
    return _icon_response(lot.icon_file)


@app.post("/api/lots")
def add_lot(payload: LotCreate) -> dict:
    config = repository.config()
    window = resolve_window(config.window_hwnd, config.window_index)
    image = WindowCapture(window.hwnd).grab()
    nearest, grid = _nearest_bag_point(payload.x, payload.y)
    lot = Lot(
        name=payload.name,
        side=payload.side,
        price=payload.price,
        quantity=payload.quantity,
        icon_file="pending.png",
    )
    lot.icon_file = f"{lot.id}.png"
    crop_icon(image, nearest.x, nearest.y, grid.icon_size).save(repository.icons / lot.icon_file)
    repository.add_lot(lot)
    return lot.model_dump(mode="json")


@app.patch("/api/lots/{lot_id}")
def update_lot(lot_id: str, payload: LotUpdate) -> dict:
    lot = next((item for item in repository.lots() if item.id == lot_id), None)
    if lot is None:
        raise HTTPException(404, "Лот не знайдено")
    updated = lot.model_copy(update=payload.model_dump(exclude_none=True))
    repository.replace_lot(updated)
    return updated.model_dump(mode="json")


@app.delete("/api/lots/{lot_id}", status_code=204)
def delete_lot(lot_id: str) -> None:
    try:
        repository.delete_lot(lot_id)
    except KeyError:
        raise HTTPException(404, "Лот не знайдено")


@app.post("/api/run")
def run(payload: RunRequest) -> dict:
    try:
        return get_runner(repository).start(
            payload.dry_run,
            payload.profile_id,
            payload.character_id,
        ).model_dump(mode="json")
    except RuntimeError as error:
        raise HTTPException(409, str(error))


@app.post("/api/stop")
def stop() -> dict:
    return get_runner(repository).stop().model_dump(mode="json")


@app.post("/api/queue/start")
def start_account_queue(payload: QueueRequest) -> dict:
    try:
        return get_account_queue(repository).start(payload.character_ids).model_dump(mode="json")
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/queue/stop")
def stop_account_queue() -> dict:
    return get_account_queue(repository).stop().model_dump(mode="json")
