from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class InputMode(str, Enum):
    BACKGROUND = "background"
    FOREGROUND = "foreground"


class LotSide(str, Enum):
    SALE = "sale"
    BUY = "buy"


class PriceMode(str, Enum):
    MANUAL = "manual"
    MARKET = "market"
    MARKET_PERCENT = "market_percent"
    MARKET_AMOUNT = "market_amount"


class Point(BaseModel):
    x: int
    y: int


class Grid(BaseModel):
    first: Point
    step: Point = Field(default_factory=lambda: Point(x=33, y=33))
    columns: int = Field(ge=1)
    rows: int = Field(ge=1)
    icon_size: int = Field(default=26, ge=16, le=40)

    def point(self, index: int) -> Point:
        row, column = divmod(index, self.columns)
        return Point(
            x=self.first.x + column * self.step.x,
            y=self.first.y + row * self.step.y,
        )

    @property
    def count(self) -> int:
        return self.columns * self.rows


class Geometry(BaseModel):
    """Координати клієнта 1440x1080 з поточного макета Ellnalise."""

    client_width: int = 1440
    client_height: int = 1080
    shop_probe: Point = Field(default_factory=lambda: Point(x=270, y=257))
    bag_probe: Point = Field(default_factory=lambda: Point(x=956, y=258))
    bag_grid: Grid = Field(default_factory=lambda: Grid(first=Point(x=863, y=549), columns=8, rows=4))
    sale_grid: Grid = Field(default_factory=lambda: Grid(first=Point(x=135, y=302), columns=5, rows=4))
    buy_grid: Grid = Field(default_factory=lambda: Grid(first=Point(x=309, y=302), columns=5, rows=4))
    shop_name: Point = Field(default_factory=lambda: Point(x=319, y=455))
    return_button: Point = Field(default_factory=lambda: Point(x=151, y=503))
    ok_button: Point = Field(default_factory=lambda: Point(x=386, y=503))
    offline_button: Point = Field(default_factory=lambda: Point(x=283, y=633))
    dialog_price: Point = Field(default_factory=lambda: Point(x=795, y=811))
    dialog_quantity: Point = Field(default_factory=lambda: Point(x=720, y=837))
    dialog_maximum: Point = Field(default_factory=lambda: Point(x=795, y=837))
    dialog_accept: Point = Field(default_factory=lambda: Point(x=691, y=866))
    split_accept: Point = Field(default_factory=lambda: Point(x=743, y=875))


class DialogSequence(BaseModel):
    """Послідовність полів у модальному вікні після перетягування."""

    sale: list[Literal["quantity", "price"]] = Field(default_factory=lambda: ["quantity", "price"])
    buy: list[Literal["quantity", "price"]] = Field(default_factory=lambda: ["quantity", "price"])
    open_delay: float = Field(default=0.55, ge=0)
    field_delay: float = Field(default=0.25, ge=0)


class AppConfig(BaseModel):
    window_index: int = Field(default=0, ge=0)
    window_hwnd: int | None = None
    input_mode: InputMode = InputMode.BACKGROUND
    shop_key: str = "f1"
    bag_key: str = "b"
    shop_name: str = ""
    open_shop: bool = True
    click_ok: bool = True
    offline_trade: bool = False
    action_delay: float = Field(default=0.65, ge=0.05, le=5)
    drag_duration: float = Field(default=0.35, ge=0.05, le=3)
    match_threshold: float = Field(default=0.60, ge=0.2, le=1)
    market_url_template: str = Field(
        default="https://comeback.pw/cats/136/?item_id={item_id}",
        min_length=12,
        max_length=1000,
    )
    market_max_pages: int = Field(default=1, ge=1, le=1)
    queue_login_delay: float = Field(default=75.0, ge=0, le=180)
    queue_window_timeout: float = Field(default=120.0, ge=10, le=600)
    queue_game_timeout: float = Field(default=240.0, ge=10, le=900)
    queue_close_timeout: float = Field(default=45.0, ge=5, le=300)
    geometry: Geometry = Field(default_factory=Geometry)
    dialogs: DialogSequence = Field(default_factory=DialogSequence)

    @field_validator("market_url_template")
    @classmethod
    def valid_market_url_template(cls, value: str) -> str:
        value = value.strip()
        if "{item_id}" not in value:
            raise ValueError("Посилання має містити {item_id}")
        try:
            parsed = urlsplit(value.format(item_id=1))
        except (KeyError, ValueError) as error:
            raise ValueError("Некоректний шаблон посилання") from error
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Потрібне повне HTTP(S)-посилання")
        return value

    @field_validator("market_max_pages", mode="before")
    @classmethod
    def use_one_market_page_per_side(cls, value):
        # Older configs used up to 20 pages. One best-offer page for sell and
        # one for buy keeps a 16-item profile inside the client loading window.
        return 1


class Lot(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = Field(min_length=1, max_length=100)
    side: LotSide = LotSide.SALE
    price: int = Field(default=1, ge=1, le=2_000_000_000)
    quantity: int = Field(default=1, ge=1, le=999_999)
    enabled: bool = True
    icon_file: str

    @field_validator("icon_file")
    @classmethod
    def safe_icon_name(cls, value: str) -> str:
        name = Path(value).name
        if value != name or not name.lower().endswith(".png"):
            raise ValueError("icon_file must be a PNG filename")
        return name


class LotCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    side: LotSide = LotSide.SALE
    price: int = Field(default=1, ge=1)
    quantity: int = Field(default=1, ge=1)
    x: int = Field(ge=0)
    y: int = Field(ge=0)


class LotUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    side: LotSide | None = None
    price: int | None = Field(default=None, ge=1)
    quantity: int | None = Field(default=None, ge=1)
    enabled: bool | None = None


class CatalogItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = Field(min_length=1, max_length=120)
    icon_file: str
    market_item_id: int | None = Field(default=None, ge=1)
    market_sell: int | None = Field(default=None, ge=1)
    market_buy: int | None = Field(default=None, ge=1)
    market_updated_at: str | None = None
    stack_limit: int = Field(default=100, ge=1, le=999_999)

    @field_validator("icon_file")
    @classmethod
    def safe_catalog_icon(cls, value: str) -> str:
        name = Path(value).name
        if value != name or not name.lower().endswith(".png"):
            raise ValueError("icon_file must be a PNG filename")
        return name


class CatalogCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    market_item_id: int | None = Field(default=None, ge=1)
    stack_limit: int = Field(default=100, ge=1, le=999_999)
    x: int = Field(ge=0)
    y: int = Field(ge=0)


class CatalogUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    market_item_id: int | None = Field(default=None, ge=1)
    stack_limit: int | None = Field(default=None, ge=1, le=999_999)


class MarketImport(BaseModel):
    item_id: int = Field(ge=1)


class ProfileEntry(BaseModel):
    item_id: str
    enabled: bool = True
    sale_enabled: bool = False
    buy_enabled: bool = True
    max_owned: int = Field(default=100, ge=0, le=999_999)
    sale_price_mode: PriceMode = PriceMode.MANUAL
    buy_price_mode: PriceMode = PriceMode.MANUAL
    sale_price: int = Field(default=1, ge=1, le=2_000_000_000)
    buy_price: int = Field(default=1, ge=1, le=2_000_000_000)
    sale_adjustment: float = Field(default=0, ge=-2_000_000_000, le=2_000_000_000)
    buy_adjustment: float = Field(default=0, ge=-2_000_000_000, le=2_000_000_000)


class Profile(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = Field(min_length=1, max_length=100)
    character_name: str = Field(default="", max_length=100)
    window_hwnd: int | None = None
    window_index: int = Field(default=0, ge=0)
    shop_name: str = Field(default="", max_length=100)
    entries: list[ProfileEntry] = Field(default_factory=list)


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    character_name: str = Field(default="", max_length=100)
    window_hwnd: int | None = None
    window_index: int = Field(default=0, ge=0)
    shop_name: str = Field(default="", max_length=100)


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    character_name: str | None = Field(default=None, max_length=100)
    window_hwnd: int | None = None
    window_index: int | None = Field(default=None, ge=0)
    shop_name: str | None = Field(default=None, max_length=100)
    entries: list[ProfileEntry] | None = None


class Character(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    character_name: str = Field(min_length=1, max_length=100)
    profile_id: str
    window_hwnd: int | None = None
    window_index: int = Field(default=0, ge=0)
    shop_name: str = Field(default="", max_length=100)
    game_dir: str = Field(default="", max_length=500)
    login: str = Field(default="", max_length=200)
    window_title: str = Field(default="", max_length=100)
    launcher_file: str = Field(default="", max_length=500)
    queue_enabled: bool = True
    offline_trade: bool = False
    free_funds: int | None = Field(default=None, ge=0)
    sale_value: int | None = Field(default=None, ge=0)
    stats_updated_at: str | None = None
    last_run_status: str = Field(default="idle", max_length=30)
    last_run_message: str = Field(default="", max_length=1000)
    last_run_at: str | None = None


class CharacterCreate(BaseModel):
    character_name: str = Field(min_length=1, max_length=100)
    profile_id: str
    window_hwnd: int | None = None
    window_index: int = Field(default=0, ge=0)
    shop_name: str = Field(default="", max_length=100)
    game_dir: str = Field(default="", max_length=500)
    login: str = Field(default="", max_length=200)
    window_title: str = Field(default="", max_length=100)
    queue_enabled: bool = True
    offline_trade: bool = False


class CharacterUpdate(BaseModel):
    character_name: str | None = Field(default=None, min_length=1, max_length=100)
    profile_id: str | None = None
    window_hwnd: int | None = None
    window_index: int | None = Field(default=None, ge=0)
    shop_name: str | None = Field(default=None, max_length=100)
    game_dir: str | None = Field(default=None, max_length=500)
    login: str | None = Field(default=None, max_length=200)
    window_title: str | None = Field(default=None, max_length=100)
    launcher_file: str | None = Field(default=None, max_length=500)
    queue_enabled: bool | None = None
    offline_trade: bool | None = None
    free_funds: int | None = Field(default=None, ge=0)
    sale_value: int | None = Field(default=None, ge=0)
    stats_updated_at: str | None = None
    last_run_status: str | None = Field(default=None, max_length=30)
    last_run_message: str | None = Field(default=None, max_length=1000)
    last_run_at: str | None = None


class LauncherUpdate(BaseModel):
    game_dir: str = Field(min_length=1, max_length=500)
    login: str = Field(min_length=1, max_length=200, pattern=r'^[^&|<>^%"\r\n]+$')
    password: str | None = Field(default=None, min_length=1, max_length=200, pattern=r'^[^&|<>^%"\r\n]+$')
    window_title: str = Field(min_length=1, max_length=100, pattern=r'^[^&|<>^%"\r\n]+$')


class RunRequest(BaseModel):
    dry_run: bool = True
    profile_id: str | None = None
    character_id: str | None = None


class QueueRequest(BaseModel):
    character_ids: list[str] | None = None


class JobState(BaseModel):
    running: bool = False
    stop_requested: bool = False
    stage: str = "idle"
    message: str = ""
    error: str | None = None
    log: list[str] = Field(default_factory=list)
    preview: list[dict] = Field(default_factory=list)


class QueueState(BaseModel):
    running: bool = False
    stop_requested: bool = False
    stage: str = "idle"
    message: str = ""
    current_character_id: str | None = None
    current_character_name: str = ""
    completed: int = 0
    total: int = 0
    error: str | None = None
    log: list[str] = Field(default_factory=list)
