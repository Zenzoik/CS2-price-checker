import json

import pytest

from cs2tracker.steam import (
    CONTAINER_TAG, ItemNotFound, RateLimited, SteamError, SteamMarket, parse_price,
)
from fakes import FakeResponse, FakeSession, NetworkDown, orderbook_body


def make_market(routes, **kw):
    """Market with a fake clock that advances only when the client sleeps."""
    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    kw.setdefault("request_delay", 0.5)
    market = SteamMarket(FakeSession(routes), sleep=sleep, clock=lambda: now[0], **kw)
    return market, sleeps


@pytest.mark.parametrize("text, expected", [
    ("$10.89", 10.89),
    ("$1,234.56", 1234.56),
    ("485,55₴", 485.55),
    ("487₴", 487.0),
    ("10,--€", 10.0),
    ("1.234,56€", 1234.56),
    ("1 234,56 pуб.", 1234.56),
    ("R$ 5,4", 5.4),
    ("¥ 1,234", 1234.0),
    ("Rp 12 345", 12345.0),
    ("2,079", 2079.0),
    ("", None),
    (None, None),
    ("--", None),
])
def test_parse_price(text, expected):
    assert parse_price(text) == expected


def test_orderbook_parses_cents_and_currency():
    market, _ = make_market({"/orderbook": [FakeResponse(body=orderbook_body(buy=42100, sell=46400, currency=18))]})
    q = market.orderbook("Operation Breakout Weapon Case")
    assert (q.currency, q.highest_buy, q.lowest_sell) == ("UAH", 421.0, 464.0)
    url, params = market.session.calls[0]
    assert params["q"] == "Load"
    assert json.loads(params["qp"]) == [730, "Operation Breakout Weapon Case"]


def test_orderbook_without_orders_has_no_price():
    market, _ = make_market({"/orderbook": [FakeResponse(body=orderbook_body(buy=0, n_buy=0))]})
    assert market.orderbook("X").highest_buy is None


def test_orderbook_unknown_item():
    market, _ = make_market({"/orderbook": [FakeResponse(body={"data": {"success": False}})]})
    with pytest.raises(ItemNotFound):
        market.orderbook("Nope")


def test_price_overview_uses_explicit_currency():
    body = {"success": True, "lowest_price": "487₴", "volume": "2,079", "median_price": "485,55₴"}
    market, _ = make_market({"/priceoverview": [FakeResponse(body=body)]})
    q = market.price_overview("Operation Breakout Weapon Case", "UAH")
    assert (q.currency, q.lowest_sell, q.highest_buy) == ("UAH", 487.0, None)
    assert market.session.calls[0][1]["currency"] == 18


def test_search_filters_containers_and_maps_results():
    body = {"results": [{"hash_name": "Operation Breakout Weapon Case", "name": "Operation Breakout Weapon Case",
                         "sell_price": 1089, "sell_listings": 19301}]}
    market, _ = make_market({"/search/render": [FakeResponse(body=body)]})
    [r] = market.search("breakout case")
    assert r.sell_price_usd == 10.89 and r.sell_listings == 19301
    assert market.session.calls[0][1]["category_730_Type[]"] == CONTAINER_TAG


def test_retries_429_with_backoff_and_retry_after():
    responses = [FakeResponse(429, "null"), FakeResponse(429, "null", {"Retry-After": "60"}),
                 FakeResponse(body=orderbook_body())]
    market, sleeps = make_market({"/orderbook": responses}, backoff=5)
    assert market.orderbook("X").lowest_sell == 9.09
    assert sleeps == [5, 60]


def test_gives_up_after_max_retries():
    market, sleeps = make_market({"/orderbook": [FakeResponse(429, "null")]}, max_retries=2, backoff=1)
    with pytest.raises(RateLimited):
        market.orderbook("X")
    assert sleeps == [1, 2]


def test_network_errors_are_retried_then_surface_as_steam_error():
    market, _ = make_market({"/orderbook": [NetworkDown("boom")]}, max_retries=1, backoff=1)
    with pytest.raises(SteamError, match="network error"):
        market.orderbook("X")


def test_client_error_is_not_retried():
    market, sleeps = make_market({"/orderbook": [FakeResponse(400, "")]})
    with pytest.raises(SteamError, match="HTTP 400"):
        market.orderbook("X")
    assert sleeps == []


def test_throttle_spaces_requests():
    now = [100.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    session = FakeSession({"/orderbook": [FakeResponse(body=orderbook_body())]})
    market = SteamMarket(session, request_delay=1.5, sleep=sleep, clock=lambda: now[0])
    market.orderbook("A")
    market.orderbook("B")
    assert sleeps == [1.5]


def test_priceoverview_unknown_item_answered_with_500_is_not_retried():
    market, sleeps = make_market({"/priceoverview": [FakeResponse(500, {"success": False})]})
    with pytest.raises(ItemNotFound):
        market.price_overview("Typo Case", "UAH")
    assert sleeps == []


def test_500_success_false_on_other_endpoints_is_a_server_error_not_a_typo():
    market, sleeps = make_market({"/orderbook": [FakeResponse(500, {"success": False})]}, backoff=1)
    with pytest.raises(SteamError) as exc:
        market.orderbook("X")
    assert not isinstance(exc.value, ItemNotFound) and sleeps == [1, 2]


def test_malformed_search_is_a_steam_error():
    market, _ = make_market({"/search/render": [FakeResponse(body={"results": [{"name": "no hash"}]})]})
    with pytest.raises(SteamError):
        market.search("x")


def test_server_errors_get_fewer_retries_than_rate_limits():
    market, sleeps = make_market({"/orderbook": [FakeResponse(502, "")]}, backoff=1, server_retries=2)
    with pytest.raises(SteamError, match="server error"):
        market.orderbook("X")
    assert sleeps == [1, 2]


def test_item_name_containing_429_is_not_mistaken_for_rate_limit():
    market, _ = make_market({"/orderbook": [NetworkDown("GET /orderbook?qp=[730,'Case 429']")]},
                            server_retries=0)
    with pytest.raises(SteamError) as exc:
        market.orderbook("Case 429")
    assert not isinstance(exc.value, RateLimited)


@pytest.mark.parametrize("body", [
    {"data": "maintenance"}, [1, 2], {"data": {"success": True, "data": [1]}},
    {"data": {"success": True, "data": {"eCurrency": None}}},
])
def test_malformed_orderbook_is_a_steam_error(body):
    market, _ = make_market({"/orderbook": [FakeResponse(body=body)]})
    with pytest.raises(SteamError):
        market.orderbook("X")
