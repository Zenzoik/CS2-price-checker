"""Inline mode: "@bot kilowatt" in any chat, and the price cards it sends."""

import asyncio
import io
import time
from urllib.parse import parse_qs, urlparse

import pytest
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from cs2tracker.app.cards import ItemCard, PortfolioCard, item_card, portfolio_card
from cs2tracker.app.db import Store
from cs2tracker.app.inline import Budget, Cards, InlineMode, NoBudget, item_key
from cs2tracker.app.server import CARDS, create_app
from cs2tracker.app.telegram import TelegramBot

from test_app import CASE, FakeMarket, make_service, result, settings

OTHER = "Kilowatt Case"
SKIN = "AK-47 | Redline (Field-Tested)"


class Bot:
    username = "cstrackerbot"

    def __init__(self):
        self.calls = []

    async def call(self, method, **params):
        self.calls.append((method, params))


async def png(icon, size=96):
    image = Image.new("RGBA", (size, size), (200, 150, 40, 255))
    out = io.BytesIO()
    image.save(out, "PNG")
    return "image/png", out.getvalue()


def service(tmp_path, market=None, **settings_kw):
    market = market or FakeMarket(books={CASE: (4.21, 4.64), OTHER: (30.0, 33.12)},
                                  containers=[result(OTHER), result(CASE)])
    store, prices = make_service(tmp_path, market)
    bot = Bot()
    s = settings(**settings_kw)
    cards = Cards(s, store, prices, icon_fetch=png, bot=bot)
    return store, prices, market, bot, InlineMode(s, store, prices, cards, bot)


# -- search and snapshots -------------------------------------------------------

def test_catalogue_search_puts_the_exact_name_first_and_escapes_like(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    store.remember_items([(n, n, None) for n in (
        "Operation Breakout Weapon Case", "Breakout Case Key", "Sticker | 100% Case", "Breakout")])
    store.add_lot(1, "Breakout Case Key", 1, None)
    assert store.search_items(["breakout"]) == ["Breakout", "Breakout Case Key", "Operation Breakout Weapon Case"]
    assert store.search_items(["case", "breakout"])[0] == "Breakout Case Key"  # held by someone
    assert store.search_items(["100%"]) == ["Sticker | 100% Case"]
    assert store.search_items(["1_0"]) == []  # "_" is a letter here, not a wildcard
    assert store.search_items([]) == []


def test_market_snapshot_compares_with_daily_closes(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    now = time.time()
    day = lambda d: time.strftime("%Y-%m-%d", time.gmtime(now - d * 86400))  # noqa: E731
    with store.conn:
        for d, cents in ((30, 800), (7, 900), (1, 1100), (3, None)):
            store.conn.execute("INSERT INTO price_history VALUES (?, ?, ?)", (CASE, day(d), cents))
    store.set_price(CASE, 1000, at=now, buy_orders=5, sell_listings=7)
    snap = store.market_snapshot(CASE, now=now)
    assert snap["change_24h"] == pytest.approx(1000 / 1100 - 1)
    assert snap["change_7d"] == pytest.approx(1000 / 900 - 1)
    assert snap["change_30d"] == pytest.approx(1000 / 800 - 1)
    assert snap["closes"] == [800, 900, 1100, 1000]  # "no price" days left out, today is live
    assert (snap["sell_listings"], snap["buy_orders"], snap["name"]) == (7, 5, CASE)
    assert store.market_snapshot("Unknown")["cents"] is None


# -- answering queries -------------------------------------------------------------

def test_a_typed_query_lists_items_as_articles_with_a_signed_card(tmp_path):
    store, prices, market, bot, inline = service(tmp_path)
    store.remember_items([(CASE, CASE, "ic1")])
    store.set_price(CASE, 464)
    store.add_lot(5, CASE, 3, 400)

    async def main():
        await inline.answer({"id": "q1", "from": {"id": 5, "language_code": "ru"}, "query": "breakout"})
    asyncio.run(main())
    (method, params), = bot.calls
    assert method == "answerInlineQuery" and params["is_personal"] and params["cache_time"] == 30
    assert params["button"]["web_app"]["url"] == "https://example.com/"
    first = params["results"][0]
    assert first["type"] == "article" and first["id"] == item_key(CASE) and first["title"] == CASE
    assert first["description"].startswith("4,64\xa0₴") and "У вас 3 шт." in first["description"]
    message = first["input_message_content"]
    assert "шт." not in message["message_text"]  # what the chat sees never has the sender's quantities
    assert message["message_text"].startswith(f"<b>{CASE}</b>\n4,64\xa0₴")
    preview = message["link_preview_options"]
    assert preview["prefer_large_media"] and preview["show_above_text"]
    assert urlparse(preview["url"]).path == f"/card/i/{item_key(CASE)}.jpg"
    assert first["reply_markup"]["inline_keyboard"][0][0]["url"] == \
        f"https://t.me/cstrackerbot?start=it_{item_key(CASE)}"
    assert first["thumbnail_url"].endswith("/abc/96fx96f")  # the Steam search refreshed it
    # Few catalogue hits for a longer query: Steam's search fills in (the user's budget).
    assert ("search", "breakout", True) in market.calls
    assert [r["title"] for r in params["results"]] == [CASE, OTHER]


def test_an_empty_query_offers_the_portfolio_and_holdings(tmp_path):
    store, prices, market, bot, inline = service(tmp_path)
    store.set_price(CASE, 464)
    store.set_price(OTHER, 3312)
    store.add_lot(5, CASE, 10, 300)
    store.add_lot(5, OTHER, 1, 2000)

    async def main():
        return await inline.results(5, "", "en"), await inline.results(6, "", "en")
    mine, newcomer = asyncio.run(main())
    assert [r["id"] for r in mine] == ["portfolio", item_key(CASE), item_key(OTHER)]  # by value
    assert "no amounts" in mine[0]["description"] and "₴" not in mine[0]["input_message_content"]["message_text"]
    assert [r["title"] for r in newcomer] == [CASE, OTHER]  # popular: what others hold
    assert not any(call[0] == "search" for call in market.calls)


def test_without_steam_budget_the_catalogue_still_answers(tmp_path):
    store, prices, market, bot, inline = service(tmp_path)
    store.remember_items([(CASE, CASE, None)])
    inline.cards.budget = Budget(limit=0)

    async def main():
        return await inline.results(5, "operation breakout", "en")
    assert [r["title"] for r in asyncio.run(main())] == [CASE]
    assert market.calls == []


def test_a_private_bot_answers_strangers_with_nothing(tmp_path):
    store, prices, market, bot, inline = service(tmp_path, allowed_users=frozenset({1}))
    asyncio.run(inline.answer({"id": "q", "from": {"id": 2}, "query": "case"}))
    assert bot.calls[0][1]["results"] == [] and market.calls == []


def test_budget_counts_all_inline_steam_calls():
    clock = [0.0]
    budget = Budget(limit=3, clock=lambda: clock[0])
    budget.charge(2)
    with pytest.raises(NoBudget):
        budget.charge(2)
    clock[0] = 61
    budget.charge(3)


# -- cards -------------------------------------------------------------------------

def run_cards(tmp_path, scenario, market=None):
    market = market or FakeMarket(books={CASE: (4.21, 4.64)}, containers=[result(CASE)])
    store, prices = make_service(tmp_path, market)
    bot = Bot()
    app = create_app(settings(), store, prices, bot=bot, icon_fetch=png)

    async def main():
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            await scenario(client, store, market, app)
        finally:
            await client.close()
    asyncio.run(main())


def path_of(url):
    u = urlparse(url)
    return f"{u.path}?{u.query}"


def test_signed_item_cards_are_drawn_and_cached(tmp_path):
    async def scenario(client, store, market, app):
        store.remember_items([(CASE, CASE, "ic1")])
        url = path_of(app[CARDS].item_url(CASE, "uk", None))
        r = await client.get(url)  # nobody tracks it: Steam is asked once, within inline's budget
        assert r.status == 200 and r.headers["Content-Type"] == "image/jpeg"
        assert Image.open(io.BytesIO(await r.read())).size == (1200, 630)
        assert market.calls == [("orderbook", CASE)] and store.price(CASE)[0] == 464
        assert (await client.get(url)).status == 200 and len(market.calls) == 1  # cached
        query = parse_qs(urlparse(url).query)
        for bad in (url.replace("l=uk", "l=ru"), url.replace(query["s"][0], "0" * 20),
                    url.replace(item_key(CASE), item_key("Other")), "/card/i/x.jpg"):
            assert (await client.get(bad)).status == 404
    run_cards(tmp_path, scenario)


def test_portfolio_cards_show_percent_only(tmp_path):
    async def scenario(client, store, market, app):
        store.set_price(CASE, 464)
        store.add_lot(5, CASE, 2, 300)
        r = await client.get(path_of(app[CARDS].portfolio_url(5, "en", "1")))
        assert r.status == 200 and Image.open(io.BytesIO(await r.read())).size == (1200, 630)
        assert (await client.get(path_of(app[CARDS].portfolio_url(6, "en", "1")))).status == 404  # nothing held
    run_cards(tmp_path, scenario)


def test_cards_render_edge_cases():
    for card in (ItemCard(name="X " * 60, currency="UAH", kind="buy", cents=10**12, net_cents=10**12, changes=[],
                          icon=b"not a picture"),
                 ItemCard(name="Kilowatt Case", currency="USD", kind="sell", cents=None, net_cents=None,
                          changes=[("day", None)], series=[5, 5], day=(2026, 1, 31))):
        assert Image.open(io.BytesIO(item_card(card, "en", None))).size == (1200, 630)
    card = PortfolioCard(ratio=None, month=None, count=0, top=[])
    assert Image.open(io.BytesIO(portfolio_card(card, "ru", "bot"))).size == (1200, 630)


# -- the bot -------------------------------------------------------------------------

class RecordingBot(TelegramBot):
    def __init__(self, s):
        super().__init__(s, session=None)
        self.sent = []

    async def call(self, method, **params):
        self.sent.append((method, params))


def test_bot_answers_inline_queries_and_opens_a_cards_item(tmp_path):
    store, prices, market, _, _ = service(tmp_path)
    bot = RecordingBot(settings())
    bot.store, bot.username = store, "cstrackerbot"
    cards = Cards(settings(), store, prices, icon_fetch=png, bot=bot)
    bot.inline = InlineMode(settings(), store, prices, cards, bot)
    store.remember_items([(SKIN, SKIN, None)])

    async def main():
        bot._answer_inline({"id": "q1", "from": {"id": 5}, "query": "red"})
        bot._answer_inline({"id": "q2", "from": {"id": 5}, "query": "redline"})  # kept typing
        await asyncio.sleep(0.05)
        await bot.handle({"update_id": 3, "message": {"text": f"/start it_{item_key(SKIN)}",
                                                      "chat": {"id": 5, "type": "private"}, "from": {"id": 5}}})
        await bot.handle({"update_id": 4, "chosen_inline_result": {"from": {"id": 5}, "result_id": "x", "query": "red"}})
    asyncio.run(main())
    answered = [p["inline_query_id"] for m, p in bot.sent if m == "answerInlineQuery"]
    assert answered == ["q2"] and bot.supports_inline  # the older query was dropped
    method, params = bot.sent[-1]
    assert method == "sendMessage" and params["text"].startswith(SKIN)
    url = params["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"]
    assert parse_qs(urlparse(url).query)["item"] == [SKIN]
    assert store.stats()["actions_7d"]["inline_sent"] == 1


def test_welcome_mentions_inline_mode_once_it_is_on():
    bot = RecordingBot(settings())
    bot.username, bot.supports_inline = "cstrackerbot", True
    asyncio.run(bot.handle({"update_id": 1, "message": {"text": "/start", "chat": {"id": 5, "type": "private"},
                                                        "from": {"id": 5, "language_code": "ru"}}}))
    assert "@cstrackerbot" in bot.sent[0][1]["text"]
