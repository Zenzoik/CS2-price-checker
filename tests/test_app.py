import asyncio
import json
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from cs2tracker.app.auth import AuthError, sign_init_data, validate_init_data
from cs2tracker.app.db import MAX_QTY, QuantityLimit, Store, StoreError
from cs2tracker.app.prices import PriceService, SteamBusy
from cs2tracker.app.server import CSP, create_app
from cs2tracker.app.settings import AppSettings, SettingsError, load_app_settings
from cs2tracker.app.telegram import BotApiError, TelegramBot
from cs2tracker.steam import ItemNotFound, Quote, RateLimited, SearchResult, SteamError

TOKEN = "123456:TEST-token"
CASE = "Operation Breakout Weapon Case"


def init_data(user_id=42, auth_date=None, token=TOKEN, lang="en"):
    return sign_init_data({
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "query_id": "AAE",
        "user": json.dumps({"id": user_id, "first_name": "Ann", "language_code": lang}),
    }, token)


class FakeMarket:
    """Order books in UAH; `books` maps hash name -> (buy, sell) or an exception."""

    def __init__(self, books=None, containers=None, everything=None):
        self.books = books or {}
        self.containers = containers or []
        self.everything = everything or []
        self.calls = []

    def orderbook(self, hash_name):
        self.calls.append(("orderbook", hash_name))
        book = self.books.get(hash_name, ItemNotFound(hash_name))
        if isinstance(book, BaseException):
            raise book
        return Quote(hash_name, "UAH", book[0], book[1], "orderbook")

    def search(self, query, containers_only=True):
        self.calls.append(("search", query, containers_only))
        return list(self.containers if containers_only else self.everything)


def result(name, icon="abc"):
    return SearchResult(hash_name=name, name=name, sell_price_usd=1.0, sell_listings=5, icon_url=icon)


def make_service(tmp_path, market, clock=None, kind="sell"):
    store = Store(tmp_path / "t.db", "UAH")
    kw = {"clock": clock} if clock else {}
    return store, PriceService(store, market, currency="UAH", kind=kind, refresh_minutes=10, **kw)


def settings(**kw):
    return AppSettings(bot_token=TOKEN, public_url="https://example.com/", **kw)


# -- auth -----------------------------------------------------------------------

def test_valid_init_data_returns_user():
    user = validate_init_data(init_data(user_id=7), TOKEN)
    assert user["id"] == 7 and user["first_name"] == "Ann"


@pytest.mark.parametrize("data, error", [
    ("", "no init data"),
    (init_data(token="999:other"), "bad signature"),
    (init_data().replace("Ann", "Bob"), "bad signature"),
    (init_data(auth_date=int(time.time()) - 2 * 86400), "expired"),
    ("auth_date=1&hash=%C3%A9", "bad signature"),  # non-ASCII hash used to crash compare_digest
    ("auth_date=1&hash=" + "A" * 64, "bad signature"),
])
def test_invalid_init_data(data, error):
    with pytest.raises(AuthError, match=error):
        validate_init_data(data, TOKEN)


def test_init_data_without_user():
    data = sign_init_data({"auth_date": str(int(time.time()))}, TOKEN)
    with pytest.raises(AuthError, match="no user"):
        validate_init_data(data, TOKEN)


# -- settings -------------------------------------------------------------------

def test_settings_from_env():
    s = load_app_settings({
        "CS2BOT_TOKEN": TOKEN, "CS2BOT_URL": "https://cs.example.com/",
        "CS2BOT_CURRENCY": "usd", "CS2BOT_PRICE": "buy", "CS2BOT_ALLOWED_USERS": "1, 2",
    })
    assert (s.currency, s.price, s.allowed_users) == ("USD", "buy", frozenset({1, 2}))
    assert s.allows(1) and not s.allows(3)
    assert settings().allows(3)


@pytest.mark.parametrize("env", [
    {},
    {"CS2BOT_TOKEN": TOKEN, "CS2BOT_URL": "http://insecure"},
    {"CS2BOT_TOKEN": TOKEN, "CS2BOT_URL": "https://x", "CS2BOT_CURRENCY": "XYZ"},
    {"CS2BOT_TOKEN": TOKEN, "CS2BOT_URL": "https://x", "CS2BOT_REFRESH_MINUTES": "0"},
    {"CS2BOT_TOKEN": TOKEN, "CS2BOT_URL": "https://x", "CS2BOT_ALLOWED_USERS": "me"},
])
def test_bad_settings(env):
    with pytest.raises(SettingsError):
        load_app_settings(env)


# -- store ----------------------------------------------------------------------

def test_add_lot_averages_buy_price(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    store.add_lot(1, CASE, 10, 30000)
    store.add_lot(1, CASE, 5, 45001)
    h = store.holding(1, CASE)
    assert (h.qty, h.buy_cents) == (15, 35000)  # (300000 + 225005) / 15 = 35000.33
    assert store.holding(2, CASE) is None


def test_set_and_delete_are_per_user(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    store.add_lot(1, CASE, 1, 100)
    assert not store.set_holding(2, CASE, 5, 5)
    assert store.set_holding(1, CASE, 5, 200)
    assert not store.delete_holding(2, CASE)
    assert store.delete_holding(1, CASE)
    assert store.holdings(1) == []


def test_store_refuses_other_currency(tmp_path):
    Store(tmp_path / "t.db", "UAH").close()
    with pytest.raises(StoreError, match="UAH"):
        Store(tmp_path / "t.db", "USD")


# -- prices ---------------------------------------------------------------------

def test_search_falls_back_to_all_items_and_remembers_icons(tmp_path):
    market = FakeMarket(containers=[result(CASE)], everything=[result(CASE), result("AK-47 | Redline (Field-Tested)")])
    store, prices = make_service(tmp_path, market)
    found = asyncio.run(prices.search("  Breakout "))
    assert [r.hash_name for r in found] == [CASE, "AK-47 | Redline (Field-Tested)"]
    assert store.item(CASE) == (CASE, "abc")
    asyncio.run(prices.search("breakout"))  # cached
    assert [c[0] for c in market.calls] == ["search", "search"]


def test_quote_is_cached_until_stale(tmp_path):
    now = [1000.0]
    market = FakeMarket(books={CASE: (4.21, 4.64)})
    store, prices = make_service(tmp_path, market, clock=lambda: now[0])
    assert asyncio.run(prices.quote(CASE)) == 464
    now[0] += 60
    assert asyncio.run(prices.quote(CASE)) == 464
    assert len(market.calls) == 1
    now[0] += 600
    market.books[CASE] = (4.0, 5.0)
    assert asyncio.run(prices.quote(CASE)) == 500


def test_refresh_updates_stale_items_and_stops_on_rate_limit(tmp_path):
    market = FakeMarket(books={"A": (1, 2), "B": RateLimited("429"), "C": (5, 6)})
    store, prices = make_service(tmp_path, market, kind="buy")
    for name in ("A", "B", "C"):
        store.add_lot(1, name, 1, 100)
    assert asyncio.run(prices.refresh()) == 1
    assert store.price("A")[0] == 100
    assert store.price("C") is None  # pass stopped at B


def test_refresh_marks_unknown_items_and_skips_fresh_ones(tmp_path):
    market = FakeMarket(books={"A": (1, 2)})
    store, prices = make_service(tmp_path, market)
    store.add_lot(1, "A", 1, 100)
    store.add_lot(1, "Gone", 1, 100)
    asyncio.run(prices.refresh())
    assert store.price("Gone")[0] is None
    market.calls.clear()
    asyncio.run(prices.refresh())
    assert market.calls == []


# -- HTTP API -------------------------------------------------------------------

def run_api(tmp_path, scenario, market=None, **settings_kw):
    market = market or FakeMarket(books={CASE: (4.21, 4.64)}, containers=[result(CASE)])
    store, prices = make_service(tmp_path, market)
    app = create_app(settings(**settings_kw), store, prices)

    async def main():
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            await scenario(client, store, market)
        finally:
            await client.close()
    asyncio.run(main())


def auth(user_id=42):
    return {"Authorization": f"tma {init_data(user_id)}"}


def test_api_requires_telegram_auth(tmp_path):
    async def scenario(client, store, market):
        r = await client.get("/api/portfolio")
        assert r.status == 401 and (await r.json())["error"] == "auth"
        r = await client.get("/api/portfolio", headers={"Authorization": "tma " + init_data(token="1:x")})
        assert r.status == 401
        assert r.headers["Cache-Control"] == "no-store"
        assert r.headers["Content-Security-Policy"] == CSP
    run_api(tmp_path, scenario)


def test_api_private_bot(tmp_path):
    async def scenario(client, store, market):
        assert (await client.get("/api/portfolio", headers=auth(1))).status == 200
        r = await client.get("/api/portfolio", headers=auth(2))
        assert r.status == 403 and (await r.json())["error"] == "private"
    run_api(tmp_path, scenario, allowed_users=frozenset({1}))


def test_api_add_edit_delete_flow(tmp_path):
    async def scenario(client, store, market):
        r = await client.get("/api/portfolio", headers=auth())
        assert await r.json() == {"currency": "UAH", "price_kind": "sell", "updated_at": None, "items": []}

        found = await (await client.get("/api/search", params={"q": "breakout"}, headers=auth())).json()
        assert found["results"] == [{"hash_name": CASE, "name": CASE, "icon": "abc", "held": False}]

        q = await (await client.get("/api/quote", params={"hash_name": CASE}, headers=auth())).json()
        assert q == {"price": 4.64, "holding": None}

        r = await client.post("/api/holdings", headers=auth(),
                              json={"hash_name": CASE, "qty": 10, "buy_price": 3.5, "mode": "add"})
        items = (await r.json())["items"]
        assert items[0] | {} == {"hash_name": CASE, "name": CASE, "icon": "abc", "qty": 10,
                                 "buy_price": 3.5, "price": 4.64}

        await client.post("/api/holdings", headers=auth(),
                          json={"hash_name": CASE, "qty": 10, "buy_price": 4.5, "mode": "add"})
        q = await (await client.get("/api/quote", params={"hash_name": CASE}, headers=auth())).json()
        assert q["holding"]["qty"] == 20 and q["holding"]["buy_price"] == 4.0

        r = await client.post("/api/holdings", headers=auth(),
                              json={"hash_name": CASE, "qty": 3, "buy_price": 1, "mode": "set"})
        assert (await r.json())["items"][0]["qty"] == 3

        # Another user sees nothing and cannot edit.
        assert (await (await client.get("/api/portfolio", headers=auth(99))).json())["items"] == []
        r = await client.post("/api/holdings", headers=auth(99),
                              json={"hash_name": CASE, "qty": 3, "buy_price": 1, "mode": "set"})
        assert r.status == 404

        r = await client.post("/api/holdings/delete", headers=auth(), json={"hash_name": CASE})
        assert (await r.json())["items"] == []
        # Only one Steam price request: the add reused the quote.
        assert [c for c in market.calls if c[0] == "orderbook"] == [("orderbook", CASE)]
    run_api(tmp_path, scenario)


@pytest.mark.parametrize("body", [
    {"hash_name": CASE, "qty": 0, "buy_price": 1, "mode": "add"},
    {"hash_name": CASE, "qty": 1.5, "buy_price": 1, "mode": "add"},
    {"hash_name": CASE, "qty": True, "buy_price": 1, "mode": "add"},
    {"hash_name": CASE, "qty": 1, "buy_price": -1, "mode": "add"},
    {"hash_name": CASE, "qty": 1, "buy_price": "1", "mode": "add"},
    {"hash_name": CASE, "qty": 1, "buy_price": 1, "mode": "replace"},
    {"hash_name": "", "qty": 1, "buy_price": 1, "mode": "add"},
    {"hash_name": "x" * 300, "qty": 1, "buy_price": 1, "mode": "add"},
    ["not", "an", "object"],
])
def test_api_rejects_bad_input(tmp_path, body):
    async def scenario(client, store, market):
        r = await client.post("/api/holdings", headers=auth(), json=body)
        assert r.status == 400 and (await r.json())["error"] == "invalid"
    run_api(tmp_path, scenario)


def test_api_refuses_unknown_items(tmp_path):
    async def scenario(client, store, market):
        r = await client.post("/api/holdings", headers=auth(),
                              json={"hash_name": "Nope", "qty": 1, "buy_price": 1, "mode": "add"})
        assert r.status == 404 and (await r.json())["error"] == "not_found"
        market.books["Flaky"] = SteamError("500")
        r = await client.post("/api/holdings", headers=auth(),
                              json={"hash_name": "Flaky", "qty": 1, "buy_price": 1, "mode": "add"})
        assert r.status == 503 and (await r.json())["error"] == "steam"
        assert store.holdings(42) == []
    run_api(tmp_path, scenario)


def test_api_portfolio_limit(tmp_path):
    async def scenario(client, store, market):
        store.add_lot(42, "A", 1, 1)
        r = await client.post("/api/holdings", headers=auth(),
                              json={"hash_name": CASE, "qty": 1, "buy_price": 1, "mode": "add"})
        assert (await r.json())["error"] == "full"
        # Adding to an existing position is still fine.
        r = await client.post("/api/holdings", headers=auth(),
                              json={"hash_name": "A", "qty": 1, "buy_price": 1, "mode": "add"})
        assert r.status == 200
    run_api(tmp_path, scenario, max_items=1)


def test_api_rate_limits_steam_calls(tmp_path):
    async def scenario(client, store, market):
        statuses = []
        for i in range(21):  # a search may cost 2 Steam calls; 40 per minute
            r = await client.get("/api/search", params={"q": f"case {i}"}, headers=auth())
            statuses.append(r.status)
        assert statuses[:20] == [200] * 20 and statuses[-1] == 429
        # Cached answers cost nothing.
        assert (await client.get("/api/search", params={"q": "case 1"}, headers=auth())).status == 200
        # Other users are unaffected.
        assert (await client.get("/api/search", params={"q": "x y"}, headers=auth(7))).status == 200
    run_api(tmp_path, scenario)


def test_quote_falls_back_to_cached_price_when_steam_fails(tmp_path):
    async def scenario(client, store, market):
        store.set_price(CASE, 999, at=0)  # stale
        market.books[CASE] = SteamError("down")
        q = await (await client.get("/api/quote", params={"hash_name": CASE}, headers=auth())).json()
        assert q["price"] == 9.99
    run_api(tmp_path, scenario)


def test_index_is_served(tmp_path):
    async def scenario(client, store, market):
        r = await client.get("/")
        assert r.status == 200 and "telegram-web-app.js" in await r.text()
        assert r.headers["Cache-Control"] == "no-cache"
        assert (await client.get("/static/app.js")).status == 200
    run_api(tmp_path, scenario)


# -- bot ------------------------------------------------------------------------

class RecordingBot(TelegramBot):
    def __init__(self, s):
        super().__init__(s, session=None)
        self.sent = []

    async def call(self, method, **params):
        self.sent.append((method, params))


@pytest.mark.parametrize("lang, button", [("ru", "Открыть портфель"), ("uk", "Відкрити портфель"), ("de", "Open portfolio")])
def test_bot_answers_with_localized_button(lang, button):
    bot = RecordingBot(settings())
    asyncio.run(bot.handle({"update_id": 1, "message": {
        "text": "/start", "chat": {"id": 5, "type": "private"}, "from": {"id": 5, "language_code": lang}}}))
    (method, params), = bot.sent
    kb = params["reply_markup"]["inline_keyboard"][0][0]
    assert method == "sendMessage" and kb["text"] == button
    assert kb["web_app"]["url"] == "https://example.com/"


def test_bot_ignores_groups_and_refuses_strangers():
    bot = RecordingBot(settings(allowed_users=frozenset({1})))
    asyncio.run(bot.handle({"update_id": 1, "message": {
        "text": "/start", "chat": {"id": -5, "type": "group"}, "from": {"id": 1}}}))
    asyncio.run(bot.handle({"update_id": 2, "message": {
        "text": "/start", "chat": {"id": 2, "type": "private"}, "from": {"id": 2}}}))
    assert len(bot.sent) == 1 and "reply_markup" not in bot.sent[0][1]


# -- regressions from the adversarial review -------------------------------------

def test_quantity_cap_prevents_overflow(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    store.add_lot(1, CASE, MAX_QTY, 10**10)
    with pytest.raises(QuantityLimit):
        store.add_lot(1, CASE, 1, 10**10)
    h = store.holding(1, CASE)
    assert (h.qty, h.buy_cents) == (MAX_QTY, 10**10)


def test_api_reports_quantity_cap(tmp_path):
    async def scenario(client, store, market):
        store.add_lot(42, CASE, MAX_QTY, 100)
        r = await client.post("/api/holdings", headers=auth(),
                              json={"hash_name": CASE, "qty": 1, "buy_price": 1, "mode": "add"})
        assert r.status == 400 and (await r.json())["error"] == "too_many"
    run_api(tmp_path, scenario)


def test_failing_items_do_not_block_the_refresh(tmp_path):
    now = [10_000.0]
    books = {f"bad{i}": SteamError("500") for i in range(3)}
    books.update({"good": (1, 2)})
    market = FakeMarket(books=books)
    store, prices = make_service(tmp_path, market, clock=lambda: now[0])
    for name in ["bad0", "bad1", "bad2", "good"]:
        store.add_lot(1, name, 1, 100)
    assert asyncio.run(prices.refresh()) == 0  # paused after 3 errors
    now[0] += 60
    assert asyncio.run(prices.refresh()) == 1  # the failing ones moved to the back
    assert store.price("good")[0] == 200


def test_not_found_keeps_the_last_known_price(tmp_path):
    market = FakeMarket(books={"A": ItemNotFound("A")})
    store, prices = make_service(tmp_path, market)
    store.add_lot(1, "A", 1, 100)
    store.set_price("A", 555, at=0)
    asyncio.run(prices.refresh())
    assert store.price("A") == (555, 0)
    assert store.tracked()[0][1] > 0  # but the attempt was recorded


class SlowMarket(FakeMarket):
    def __init__(self, delay, **kw):
        super().__init__(**kw)
        self.delay = delay
        self.active = 0
        self.max_active = 0

    def orderbook(self, hash_name):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        self.active -= 1
        return super().orderbook(hash_name)


def test_concurrent_quotes_hit_steam_once(tmp_path):
    market = SlowMarket(0.05, books={CASE: (1, 2)})
    store, prices = make_service(tmp_path, market)

    async def main():
        return await asyncio.gather(*[prices.quote(CASE) for _ in range(10)])
    assert asyncio.run(main()) == [200] * 10
    assert market.calls == [("orderbook", CASE)]


def test_cancelled_request_keeps_steam_locked_until_the_thread_ends(tmp_path):
    market = SlowMarket(0.2, books={"A": (1, 2), "B": (1, 2)})
    store, prices = make_service(tmp_path, market)

    async def main():
        first = asyncio.ensure_future(prices.quote("A"))
        await asyncio.sleep(0.05)
        first.cancel()
        await prices.quote("B")
    asyncio.run(main())
    assert market.max_active == 1


def test_interactive_request_gives_up_when_steam_is_busy(tmp_path):
    market = SlowMarket(0.3, books={"A": (1, 2), "B": (1, 2)})
    store = Store(tmp_path / "t.db", "UAH")
    prices = PriceService(store, market, currency="UAH", kind="sell", refresh_minutes=10, interactive_wait=0.05)

    async def main():
        slow = asyncio.ensure_future(prices.quote("A"))
        await asyncio.sleep(0.02)
        with pytest.raises(SteamBusy):
            await prices.quote("B")
        await slow
    asyncio.run(main())


class FakeResp:
    def __init__(self, status, body):
        self.status, self.body = status, body

    async def json(self, content_type=None):
        return json.loads(self.body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)

    def post(self, url, json=None, timeout=None):
        return self.responses.pop(0)


def test_bot_api_garbage_becomes_bot_api_error():
    bot = TelegramBot(settings(), FakeSession(FakeResp(502, "<html>Bad Gateway</html>"), FakeResp(200, "[]")))
    with pytest.raises(BotApiError, match="getMe"):
        asyncio.run(bot.call("getMe"))
    with pytest.raises(BotApiError, match="HTTP 200"):
        asyncio.run(bot.call("getMe"))


def test_bot_skips_malformed_updates():
    bot = RecordingBot(settings())
    for update in [{"update_id": 1}, {"update_id": 2, "message": "x"},
                   {"update_id": 3, "message": {"chat": {"type": "private", "id": 1}, "from": {"id": "1"}}}]:
        asyncio.run(bot.handle(update))
    assert bot.sent == []


def test_result_is_stored_before_the_lock_is_released(tmp_path):
    """Waiters must see the cached price whatever order asyncio wakes them in."""
    market = SlowMarket(0.02, books={CASE: (1, 2)})
    store, prices = make_service(tmp_path, market)
    seen = []
    real_release = prices._lock.release

    def release():
        seen.append(store.price(CASE))
        real_release()
    prices._lock.release = release
    asyncio.run(prices.quote(CASE))
    assert seen[0] is not None and seen[0][0] == 200
