from urllib.error import HTTPError

from app import market
from app.market import fetch_quote, parse_quote


def test_market_quote_uses_lowest_sale_and_highest_buy():
    html = """
    <img class="recent_search" data-name="Водний шар" src="/images/item.png">
    <p class="sell_search">670,000,000</p>
    <p class="sell_search">640,000,000</p>
    <p class="buy_search">120 000</p>
    <p class="buy_search">150 000</p>
    """
    quote = parse_quote(html, 15047)
    assert quote.name == "Водний шар"
    assert quote.recommended_sell == 640_000_000
    assert quote.recommended_buy == 150_000
    assert quote.icon_url.startswith("https://comeback.pw/")


def test_market_fetches_each_side_and_all_pages(monkeypatch):
    pages = {
        ("sell", "1"): ["900", "700"],
        ("sell", "2"): ["650"],
        ("sell", "3"): [],
        ("buy", "1"): ["400", "500"],
        ("buy", "2"): [],
    }
    requested = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return self.payload.encode()

    def fake_open(request, timeout):
        from urllib.parse import parse_qs, urlsplit

        requested.append(request.full_url)
        query = parse_qs(urlsplit(request.full_url).query)
        side, page = query["show"][0], query["page"][0]
        price_class = "sell_search" if side == "sell" else "buy_search"
        offers = "".join(f'<p class="{price_class}">{price}</p>' for price in pages[(side, page)])
        return Response(f'<img class="recent_search" data-name="Loot" src="/loot.png">{offers}')

    monkeypatch.setattr(market, "urlopen", fake_open)
    monkeypatch.setattr(market, "REQUEST_INTERVAL_SECONDS", 0)
    quote = fetch_quote(
        77,
        url_template="https://prices.example.test/list?server=136&item_id={item_id}",
        max_pages=5,
    )

    assert quote.recommended_sell == 650
    assert quote.recommended_buy == 500
    assert len(requested) == 5
    assert all("server=136" in url and "item_id=77" in url for url in requested)
    assert any("show=sell" in url and "page=2" in url for url in requested)
    assert any("show=buy" in url and "page=2" in url for url in requested)


def test_market_retries_temporary_forbidden_response(monkeypatch):
    attempts = []
    sleeps = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return b"ok"

    def fake_open(request, timeout):
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise HTTPError(request.full_url, 403, "Forbidden", {}, None)
        return Response()

    monkeypatch.setattr(market, "urlopen", fake_open)
    monkeypatch.setattr(market, "REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(market.time, "sleep", sleeps.append)

    payload = market._read_url(market.Request("https://example.test"), timeout=1)

    assert payload == b"ok"
    assert len(attempts) == 2
    assert sleeps == [5.0]


def test_market_requests_use_character_loading_time_budget(monkeypatch):
    sleeps = []
    moments = iter([10.0, 12.1875])
    monkeypatch.setattr(market, "_last_request_at", 10.0)
    monkeypatch.setattr(market.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(market.time, "sleep", sleeps.append)

    market._wait_for_request_slot()

    assert sleeps == [2.1875]
    assert market._last_request_at == 12.1875
