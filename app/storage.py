from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.models import AppConfig, CatalogItem, Character, Lot, LotSide, Profile, ProfileEntry

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ICONS = DATA / "icons"


class Repository:
    def __init__(self, root: Path = DATA) -> None:
        self.root = root
        self.icons = root / "icons"
        self.config_file = root / "config.json"
        self.lots_file = root / "lots.json"
        self.catalog_file = root / "catalog.json"
        self.profiles_file = root / "profiles.json"
        self.characters_file = root / "characters.json"
        self._lock = threading.RLock()
        self.icons.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _read(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def config(self) -> AppConfig:
        with self._lock:
            return AppConfig.model_validate(self._read(self.config_file, {}))

    def save_config(self, config: AppConfig) -> AppConfig:
        with self._lock:
            self._write(self.config_file, config.model_dump(mode="json"))
        return config

    def lots(self) -> list[Lot]:
        with self._lock:
            return [Lot.model_validate(item) for item in self._read(self.lots_file, [])]

    def save_lots(self, lots: list[Lot]) -> list[Lot]:
        with self._lock:
            self._write(self.lots_file, [lot.model_dump(mode="json") for lot in lots])
        return lots

    def add_lot(self, lot: Lot) -> Lot:
        lots = self.lots()
        lots.append(lot)
        self.save_lots(lots)
        return lot

    def replace_lot(self, lot: Lot) -> Lot:
        lots = self.lots()
        for index, current in enumerate(lots):
            if current.id == lot.id:
                lots[index] = lot
                self.save_lots(lots)
                return lot
        raise KeyError(lot.id)

    def delete_lot(self, lot_id: str) -> None:
        lots = self.lots()
        keep = [lot for lot in lots if lot.id != lot_id]
        if len(keep) == len(lots):
            raise KeyError(lot_id)
        removed = next(lot for lot in lots if lot.id == lot_id)
        self.save_lots(keep)
        icon = self.icons / removed.icon_file
        if icon.exists():
            icon.unlink()

    def _migrate_legacy(self) -> None:
        if self.catalog_file.exists() and self.profiles_file.exists():
            return
        legacy = self.lots()
        if not legacy:
            if not self.catalog_file.exists():
                self._write(self.catalog_file, [])
            if not self.profiles_file.exists():
                self._write(self.profiles_file, [])
            return
        sales = [lot for lot in legacy if lot.side is LotSide.SALE]
        buys = [lot for lot in legacy if lot.side is LotSide.BUY]
        catalog: list[CatalogItem] = []
        entries: list[ProfileEntry] = []
        for index, sale in enumerate(sales):
            buy = buys[index] if index < len(buys) else None
            item = CatalogItem(name=sale.name, icon_file=sale.icon_file)
            catalog.append(item)
            entries.append(
                ProfileEntry(
                    item_id=item.id,
                    sale_enabled=True,
                    buy_enabled=buy is not None,
                    max_owned=100,
                    sale_price=sale.price,
                    buy_price=buy.price if buy else 1,
                )
            )
        config = self.config()
        profile = Profile(
            name=config.shop_name or "20k / 500",
            character_name="Ellnalise",
            window_hwnd=config.window_hwnd,
            window_index=config.window_index,
            shop_name=config.shop_name,
            entries=entries,
        )
        self._write(self.catalog_file, [item.model_dump(mode="json") for item in catalog])
        self._write(self.profiles_file, [profile.model_dump(mode="json")])

    def catalog(self) -> list[CatalogItem]:
        with self._lock:
            self._migrate_legacy()
            return [CatalogItem.model_validate(item) for item in self._read(self.catalog_file, [])]

    def save_catalog(self, items: list[CatalogItem]) -> list[CatalogItem]:
        with self._lock:
            self._write(self.catalog_file, [item.model_dump(mode="json") for item in items])
        return items

    def add_catalog_item(self, item: CatalogItem) -> CatalogItem:
        items = self.catalog()
        items.append(item)
        self.save_catalog(items)
        return item

    def replace_catalog_item(self, item: CatalogItem) -> CatalogItem:
        items = self.catalog()
        for index, current in enumerate(items):
            if current.id == item.id:
                items[index] = item
                self.save_catalog(items)
                return item
        raise KeyError(item.id)

    def delete_catalog_item(self, item_id: str) -> None:
        items = self.catalog()
        removed = next((item for item in items if item.id == item_id), None)
        if removed is None:
            raise KeyError(item_id)
        self.save_catalog([item for item in items if item.id != item_id])
        profiles = []
        for profile in self.profiles():
            profile.entries = [entry for entry in profile.entries if entry.item_id != item_id]
            profiles.append(profile)
        self.save_profiles(profiles)
        icon = self.icons / removed.icon_file
        if icon.exists() and not any(item.icon_file == removed.icon_file for item in self.catalog()):
            icon.unlink()

    def profiles(self) -> list[Profile]:
        with self._lock:
            self._migrate_legacy()
            return [Profile.model_validate(item) for item in self._read(self.profiles_file, [])]

    def save_profiles(self, profiles: list[Profile]) -> list[Profile]:
        with self._lock:
            self._write(self.profiles_file, [profile.model_dump(mode="json") for profile in profiles])
        return profiles

    def add_profile(self, profile: Profile) -> Profile:
        profiles = self.profiles()
        profiles.append(profile)
        self.save_profiles(profiles)
        return profile

    def replace_profile(self, profile: Profile) -> Profile:
        profiles = self.profiles()
        for index, current in enumerate(profiles):
            if current.id == profile.id:
                profiles[index] = profile
                self.save_profiles(profiles)
                return profile
        raise KeyError(profile.id)

    def delete_profile(self, profile_id: str) -> None:
        profiles = self.profiles()
        keep = [profile for profile in profiles if profile.id != profile_id]
        if len(keep) == len(profiles):
            raise KeyError(profile_id)
        self.save_profiles(keep)

    def _migrate_characters(self) -> None:
        if self.characters_file.exists():
            return
        characters = []
        for profile in self.profiles():
            characters.append(
                Character(
                    character_name=profile.character_name or profile.name,
                    profile_id=profile.id,
                    window_hwnd=profile.window_hwnd,
                    window_index=profile.window_index,
                    shop_name=profile.shop_name,
                )
            )
        self._write(self.characters_file, [item.model_dump(mode="json") for item in characters])

    def characters(self) -> list[Character]:
        with self._lock:
            self._migrate_characters()
            return [Character.model_validate(item) for item in self._read(self.characters_file, [])]

    def save_characters(self, characters: list[Character]) -> list[Character]:
        with self._lock:
            self._write(self.characters_file, [item.model_dump(mode="json") for item in characters])
        return characters

    def add_character(self, character: Character) -> Character:
        characters = self.characters()
        characters.append(character)
        self.save_characters(characters)
        return character

    def replace_character(self, character: Character) -> Character:
        characters = self.characters()
        for index, current in enumerate(characters):
            if current.id == character.id:
                characters[index] = character
                self.save_characters(characters)
                return character
        raise KeyError(character.id)

    def delete_character(self, character_id: str) -> None:
        characters = self.characters()
        keep = [item for item in characters if item.id != character_id]
        if len(keep) == len(characters):
            raise KeyError(character_id)
        self.save_characters(keep)


repository = Repository()
