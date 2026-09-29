from __future__ import annotations

import io
import re
import threading
import time
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from PIL import Image


DEFAULT_URL_TEMPLATE = "https://comeback.pw/cats/136/?item_id={item_id}"
# Kept for compatibility with older imports.
BASE_URL = DEFAULT_URL_TEMPLATE
USER_AGENT = "Mozilla/5.0 (PwCatBot market sync)"
MAX_PROFILE_ITEMS = 16
REQUESTS_PER_ITEM = 2
CHARACTER_LOADING_SECONDS = 70.0
REQUEST_INTERVAL_SECONDS = CHARACTER_LOADING_SECONDS / (MAX_PROFILE_ITEMS * REQUESTS_PER_ITEM)
RETRY_DELAYS_SECONDS = (5.0, 15.0)
RETRYABLE_HTTP_STATUSES = {403, 408, 425, 429, 500, 502, 503, 504}

_request_lock = threading.Lock()
_last_request_at = 0.0


def _wait_for_request_slot() -> None:
    """Spread 16 sell/buy request pairs over the 70-second client startup."""
    global _last_request_at
    with _request_lock:
        wait = REQUEST_INTERVAL_SECONDS - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _read_url(request: Request, timeout: float) -> bytes:
    """Read a URL with rate limiting and bounded retries for transient blocks."""
    attempts = len(RETRY_DELAYS_SECONDS) + 1
    for attempt in range(attempts):
        _wait_for_request_slot()
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as error:
            if error.code not in RETRYABLE_HTTP_STATUSES or attempt == attempts - 1:
                raise
            time.sleep(RETRY_DELAYS_SECONDS[attempt])
    raise RuntimeError("unreachable")


@dataclass(frozen=True)
class MarketQuote:
    item_id: int
    name: str
    icon_url: str
    sell_prices: list[int]
    buy_prices: list[int]
    source_url: str = ""

    @property
    def recommended_sell(self) -> int | None:
        return min(self.sell_prices, default=None)

    @property
    def recommended_buy(self) -> int | None:
        return max(self.buy_prices, default=None)

    def as_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "name": self.name,
            "icon_url": self.icon_url,
            "sell": self.recommended_sell,
            "buy": self.recommended_buy,
            "sell_offers": len(self.sell_prices),
            "buy_offers": len(self.buy_prices),
            "source_url": self.source_url or DEFAULT_URL_TEMPLATE.format(item_id=self.item_id),
        }


def _number(value: str) -> int | None:
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else None


def _market_url(url_template: str, item_id: int, side: str | None = None, page: int | None = None) -> str:
    base = url_template.format(item_id=item_id)
    parsed = urlsplit(base)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if side is not None:
        query["show"] = side
    if page is not None:
        query["page"] = str(page)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def parse_quote(html: bytes | str, item_id: int, source_url: str | None = None) -> MarketQuote:
    soup = BeautifulSoup(html, "html.parser")
    hero = soup.select_one("img.recent_search")
    if hero is None:
        raise RuntimeError(f"ComebackPW не знайшов предмет ID {item_id}")
    page_url = source_url or DEFAULT_URL_TEMPLATE.format(item_id=item_id)
    sell_prices = [value for node in soup.select("p.sell_search") if (value := _number(node.get_text()))]
    buy_prices = [value for node in soup.select("p.buy_search") if (value := _number(node.get_text()))]
    return MarketQuote(
        item_id=item_id,
        name=(hero.get("data-name") or f"Item {item_id}").strip(),
        icon_url=urljoin(page_url, str(hero.get("src") or "")),
        sell_prices=sell_prices,
        buy_prices=buy_prices,
        source_url=page_url,
    )


def fetch_quote(
    item_id: int,
    timeout: float = 12.0,
    url_template: str = DEFAULT_URL_TEMPLATE,
    max_pages: int = 20,
) -> MarketQuote:
    name = ""
    icon_url = ""
    prices: dict[str, list[int]] = {"sell": [], "buy": []}
    source_url = _market_url(url_template, item_id)

    # The combined view can omit the best offer when the result is split.
    for side in ("sell", "buy"):
        previous_page: tuple[int, ...] | None = None
        for page in range(1, max_pages + 1):
            url = _market_url(url_template, item_id, side=side, page=page)
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"})
            quote = parse_quote(_read_url(request, timeout), item_id, source_url=url)
            name = name or quote.name
            icon_url = icon_url or quote.icon_url
            page_prices = tuple(quote.sell_prices if side == "sell" else quote.buy_prices)
            if not page_prices or page_prices == previous_page:
                break
            prices[side].extend(page_prices)
            previous_page = page_prices

    return MarketQuote(
        item_id=item_id,
        name=name or f"Item {item_id}",
        icon_url=icon_url,
        sell_prices=prices["sell"],
        buy_prices=prices["buy"],
        source_url=source_url,
    )


def fetch_icon(url: str, timeout: float = 12.0) -> Image.Image:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    payload = _read_url(request, timeout)
    with Image.open(io.BytesIO(payload)) as image:
        return image.convert("RGB")
