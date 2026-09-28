from fastapi.testclient import TestClient

from app.market import MarketQuote
from app.models import CatalogItem, Character, Profile, ProfileEntry
from app.storage import Repository
from app import web


def test_main_admin_sections_have_separate_pages():
    client = TestClient(web.app)

    dashboard = client.get("/")
    strategies = client.get("/strategies")
    items = client.get("/items")
    windows = client.get("/windows")

    assert dashboard.status_code == 200
    assert strategies.status_code == 200
    assert items.status_code == 200
    assert windows.status_code == 200
    assert "PwCatBot · Головна" in dashboard.text
    assert "PwCatBot · Лавки (стратегії)" in strategies.text
    assert "PwCatBot · База предметів" in items.text
    assert "PwCatBot · Вікна" in windows.text


def test_profile_entries_round_trip_as_models(tmp_path, monkeypatch):
    repository = Repository(tmp_path)
    item = repository.add_catalog_item(CatalogItem(name="Loot", icon_file="loot.png"))
    profile = repository.add_profile(Profile(name="Farmer"))
    monkeypatch.setattr(web, "repository", repository)
    client = TestClient(web.app)

    response = client.patch(
        f"/api/profiles/{profile.id}",
        json={"entries": [ProfileEntry(item_id=item.id, max_owned=100).model_dump(mode="json")]},
    )

    assert response.status_code == 200
    assert response.json()["entries"][0]["max_owned"] == 100
    assert repository.profiles()[0].entries[0].item_id == item.id


def test_profile_copy_keeps_entries_and_gets_new_identity(tmp_path, monkeypatch):
    repository = Repository(tmp_path)
    item = repository.add_catalog_item(CatalogItem(name="Loot", icon_file="loot.png"))
    original = repository.add_profile(
        Profile(name="Bot 1", character_name="Cat", entries=[ProfileEntry(item_id=item.id)])
    )
    monkeypatch.setattr(web, "repository", repository)
    response = TestClient(web.app).post(f"/api/profiles/{original.id}/copy")
    copied = response.json()
    assert response.status_code == 200
    assert copied["id"] != original.id
    assert copied["name"] == "Bot 1 — копія"
    assert copied["character_name"] == "Cat"
    assert copied["entries"][0]["item_id"] == item.id


def test_profile_market_refresh_updates_all_profile_items(tmp_path, monkeypatch):
    repository = Repository(tmp_path)
    item = repository.add_catalog_item(
        CatalogItem(name="Loot", icon_file="loot.png", market_item_id=77)
    )
    profile = repository.add_profile(Profile(name="Bot", entries=[ProfileEntry(item_id=item.id)]))
    monkeypatch.setattr(web, "repository", repository)
    monkeypatch.setattr(
        web,
        "fetch_quote",
        lambda item_id, **_: MarketQuote(item_id, "Loot", "", [120, 100], [40, 50]),
    )

    response = TestClient(web.app).post(f"/api/profiles/{profile.id}/market")

    assert response.status_code == 200
    assert response.json()["errors"] == []
    assert repository.catalog()[0].market_sell == 100
    assert repository.catalog()[0].market_buy == 50


def test_multiple_characters_can_share_one_scenario(tmp_path, monkeypatch):
    repository = Repository(tmp_path)
    scenario = repository.add_profile(Profile(name="Shared shop"))
    monkeypatch.setattr(web, "repository", repository)
    client = TestClient(web.app)

    first = client.post(
        "/api/characters",
        json={"character_name": "CatOne", "profile_id": scenario.id, "shop_name": "Shop 1"},
    )
    second = client.post(
        "/api/characters",
        json={"character_name": "CatTwo", "profile_id": scenario.id, "shop_name": "Shop 2"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert {item.profile_id for item in repository.characters()} == {scenario.id}
    blocked = client.delete(f"/api/profiles/{scenario.id}")
    assert blocked.status_code == 409


def test_state_clears_closed_window_and_stale_not_found_error(tmp_path, monkeypatch):
    repository = Repository(tmp_path)
    scenario = repository.add_profile(Profile(name="Shared shop"))
    repository.save_characters([])
    character = repository.add_character(
        Character(
            character_name="Ellnalise",
            profile_id=scenario.id,
            window_hwnd=6685858,
            window_title="Ellnalise",
            last_run_status="error",
            last_run_message="вікно 6685858 не знайдено",
        )
    )
    monkeypatch.setattr(web, "repository", repository)
    monkeypatch.setattr(web, "game_windows", lambda: [])

    payload = TestClient(web.app).get("/api/state").json()

    row = next(item for item in payload["characters"] if item["id"] == character.id)
    assert row["window_hwnd"] is None
    assert row["last_run_status"] == "idle"
    assert row["last_run_message"] == "Клієнт закрито"
