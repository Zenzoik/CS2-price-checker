"""Inline mode: "@bot kilowatt" in any chat, and the price cards it sends."""

import asyncio
import gzip
import io
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from cs2tracker.app.cards import ItemCard, PortfolioCard, item_card, portfolio_card
from cs2tracker.app.catalog import NAMES_URL, PICTURE_SOURCES, Catalog, save_catalog
from cs2tracker.app.db import Store
from cs2tracker.app.inline import Budget, Cards, InlineMode, NoBudget, item_key
from cs2tracker.app.prices import PriceService
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

def catalog_with(tmp_path, names, **catalog_kw):
    store = Store(tmp_path / "t.db", "UAH")
    save_catalog(store.path, names, {}, time.time(), False)
    return store, Catalog(store, **catalog_kw)


def test_catalog_ranks_what_people_mean(tmp_path):
    store, catalog = catalog_with(tmp_path, {
        "AWP | Dragon Lore (Factory New)": (1_500_000, True),
        "AWP | Dragon Lore (Battle-Scarred)": (400_000, True),
        "StatTrak™ AWP | Dragon Lore (Field-Tested)": (900_000, False),  # no such item; a test
        "Souvenir AWP | Dragon Lore (Field-Tested)": (2_000_000, True),
        "SSG 08 | Dragonfire (Field-Tested)": (3_000, True),
        "Sticker | Dragon Lore (Foil)": (500, True),
        "Sticker | Snakedragon": (40, False),
        "AK-47 | Redline (Field-Tested)": (2_500, True),
        "AK-47 | Case Hardened (Battle-Scarred)": (9_000, True),
        "★ Karambit | Case Hardened (Field-Tested)": (90_000, True),
        "★ Karambit | Fade (Factory New)": (150_000, True),
        "Sticker | Natus Vincere | Katowice 2019": (300, True),
        "Kilowatt Case": (60, True),
        "Revolution Case": (50, True),
        "Operation Wildfire Case": (300, False),
    })
    # Every wear of the skin before stickers of the same name; plain before StatTrak / Souvenir.
    assert catalog.search("dragon lore") == [
        "AWP | Dragon Lore (Factory New)", "AWP | Dragon Lore (Battle-Scarred)",
        "Souvenir AWP | Dragon Lore (Field-Tested)", "StatTrak™ AWP | Dragon Lore (Field-Tested)",
        "Sticker | Dragon Lore (Foil)"]
    # One of each skin first; words must start with what was typed ("Snakedragon" doesn't).
    assert catalog.search("dragon")[:2] == ["AWP | Dragon Lore (Factory New)", "SSG 08 | Dragonfire (Field-Tested)"]
    assert "Sticker | Snakedragon" not in catalog.search("dragon")
    assert catalog.search("stattrak dragon")[0] == "StatTrak™ AWP | Dragon Lore (Field-Tested)"
    # "red" is Redline, not "Battle-Scarred"; "AK-47" and "ak47" are one word.
    assert catalog.search("ak47 red") == catalog.search("AK-47 | Red") == ["AK-47 | Redline (Field-Tested)"]
    # "case" means cases, then the Case Hardened skins.
    assert catalog.search("case")[:3] == ["Kilowatt Case", "Revolution Case", "Operation Wildfire Case"]
    # What players type: team and wear short names, and Russian words.
    assert catalog.search("sticker navi 2019") == ["Sticker | Natus Vincere | Katowice 2019"]
    assert catalog.search("dragon lore fn") == ["AWP | Dragon Lore (Factory New)"]
    assert catalog.search("керамбит фейд") == ["★ Karambit | Fade (Factory New)"]
    assert catalog.search("кейс")[0] == "Kilowatt Case"
    # Among equals: what users here hold, then what sells today, then the dearer.
    store.add_lot(1, "Revolution Case", 1, None)
    catalog._holders_at = -10**9
    assert catalog.search("case")[:3] == ["Revolution Case", "Kilowatt Case", "Operation Wildfire Case"]
    assert catalog.search("") == catalog.search("|||") == []


def test_untracked_items_get_an_estimate_from_items_of_a_similar_price(tmp_path):
    # Cheap items list far above their USD median, skins close to the exchange rate:
    # one ratio for all would price a $10 skin like a sticker.
    cheap = {f"Sticker {i}": (3, True) for i in range(8)}
    skins = {f"Skin {i}": (3_000 + i * 100, True) for i in range(8)}
    names = cheap | skins | {"Rare Case": (1_000, False), "Rare Sticker": (4, False), "Unsold Case": (0, False)}
    store, catalog = catalog_with(tmp_path, names)
    assert catalog.estimate("Rare Case") is None  # nothing priced both ways yet
    for i in range(8):
        store.set_price(f"Sticker {i}", 300)  # 100 UAH per USD
        store.set_price(f"Skin {i}", round((3_000 + i * 100) * (40 if i != 7 else 90)))  # one outlier
    catalog._holders_at = -10**9
    assert catalog.estimate("Rare Case") == 40_000  # like the skins, the outlier outvoted
    assert catalog.estimate("Rare Sticker") == 400  # like the stickers
    assert catalog.estimate("Unsold Case") is None and catalog.estimate("Nope") is None

    bot = Bot()
    prices = PriceService(store, FakeMarket(), currency="UAH", kind="sell", refresh_minutes=10)
    inline = InlineMode(settings(), store, prices, Cards(settings(), store, prices, png, bot, catalog=catalog), bot)
    result, = asyncio.run(inline.results(5, "rare case", "ru"))
    assert result["description"] == "≈ 400\xa0₴ · оценка"  # two significant digits: it is an estimate
    from cs2tracker.app.notify import approx_money
    assert [approx_money(c, "UAH", "en") for c in (355, 3_640, 119_600, 1_178_300)] == [
        "3.55\xa0₴", "36\xa0₴", "1,200\xa0₴", "12,000\xa0₴"]
    assert "≈" not in result["input_message_content"]["message_text"]  # the chat gets the card's exact price
    card = ItemCard(name="Rare Case", currency="UAH", kind="sell", cents=None, net_cents=None, changes=[],
                    estimate_cents=41_500)
    assert Image.open(io.BytesIO(item_card(card, "ru", "bot"))).size == (1200, 630)


def test_the_refresh_warms_rare_items_and_asked_for_ones(tmp_path):
    rare = {f"Rare {i}": (0, False) for i in range(25)}
    store, catalog = catalog_with(tmp_path, rare | {"Common Case": (50, True)})
    books = {name: (10.0, 12.0) for name in rare} | {"Wanted Knife": (500.0, 600.0)}
    clock = [time.time()]
    market = FakeMarket(books=books)
    prices = PriceService(store, market, currency="UAH", kind="sell", refresh_minutes=10, clock=lambda: clock[0])
    prices.want(["Wanted Knife"])
    assert prices._wake.is_set()  # the pass starts now
    asyncio.run(prices.refresh())
    fetched = [c[1] for c in market.calls]
    assert fetched[0] == "Wanted Knife" and len(fetched) == 21  # asked-for first, then 20 rare ones
    assert "Common Case" not in fetched  # it has an estimate
    asyncio.run(prices.refresh())
    assert len(market.calls) == 26  # the other 5; nothing is asked twice within the week
    asyncio.run(prices.refresh())
    assert len(market.calls) == 26
    clock[0] += 8 * 86400
    asyncio.run(prices.refresh())
    assert len(market.calls) == 46


def test_results_say_how_old_a_price_is_and_ask_for_missing_ones(tmp_path):
    store, catalog = catalog_with(tmp_path, {"Old Knife": (0, False), "Unknown Knife": (0, False)})
    store.set_price("Old Knife", 123_400, at=time.time() - 3 * 86400)
    bot = Bot()
    prices = PriceService(store, FakeMarket(), currency="UAH", kind="sell", refresh_minutes=10)
    inline = InlineMode(settings(), store, prices, Cards(settings(), store, prices, png, bot, catalog=catalog), bot)
    results = {r["title"]: r for r in asyncio.run(inline.results(5, "knife", "ru"))}
    assert results["Old Knife"]["description"] == "1\u202f234\xa0₴ · 3 дн. назад"
    assert "₴" not in results["Old Knife"]["input_message_content"]["message_text"]  # the card re-asks Steam
    assert results["Unknown Knife"]["description"] == "цена на карточке"
    assert list(prices._wanted) == ["Unknown Knife"] and prices._wake.is_set()
    asyncio.run(inline.answer({"id": "q", "from": {"id": 5, "language_code": "ru"}, "query": "knife"}))
    assert bot.calls[-1][1]["cache_time"] == 5  # asking again soon shows the price


def test_catalog_learns_names_met_elsewhere(tmp_path):
    store, catalog = catalog_with(tmp_path, {"Kilowatt Case": (60, True)})
    assert catalog.search("gallery") == []
    catalog.remember(["Gallery Case", "Kilowatt Case"])
    assert catalog.search("gallery") == ["Gallery Case"] and len(catalog) == 2
    store.remember_items([("Fever Case", "Fever Case", "ic")])  # e.g. from an inventory
    catalog.load()
    assert catalog.search("fever") == ["Fever Case"]


def test_catalog_refresh_downloads_names_daily_and_pictures_weekly(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    store.remember_items([("AWP | Asiimov (Field-Tested)", "AWP | Asiimov (Field-Tested)", "steam-icon")])
    names = {f"Filler {i}": {"last_7d": 1.0} for i in range(1000)}
    names |= {"AWP | Asiimov (Field-Tested)": {"last_7d": 30.5, "last_24h": 31},
              "StatTrak™ AWP | Asiimov (Battle-Scarred)": {"last_30d": 25},
              "Sticker | Crown (Foil)": {"last_24h": None, "last_90d": 400}}
    pictures = {
        "skins": [{"name": "AWP | Asiimov", "image": "https://community.akamai.steamstatic.com/economy/image/asiimov"}],
        "stickers": [{"name": "x", "market_hash_name": "Sticker | Crown (Foil)",
                      "image": "https://community.akamai.steamstatic.com/economy/image/crown/360fx360f"}],
    }
    fetched = []

    async def fetch(url):
        fetched.append(url)
        if url == NAMES_URL:
            return gzip.compress(json.dumps(names).encode())
        source = url.rsplit("/", 1)[1].removesuffix(".json")
        return json.dumps(pictures.get(source, [])).encode()
    clock = [1_000_000.0]
    catalog = Catalog(store, fetch=fetch, clock=lambda: clock[0])
    asyncio.run(catalog.refresh_if_due())
    assert len(fetched) == 1 + len(PICTURE_SOURCES) and len(catalog) == 1003
    assert store.item("StatTrak™ AWP | Asiimov (Battle-Scarred)")[1] == "asiimov"  # the skin's picture
    assert store.item("AWP | Asiimov (Field-Tested)")[1] == "steam-icon"  # Steam's own is kept
    assert store.item("Sticker | Crown (Foil)")[1] == "crown"
    assert catalog.search("asiimov")[0] == "AWP | Asiimov (Field-Tested)"

    clock[0] += 3600  # nothing due
    asyncio.run(catalog.refresh_if_due())
    assert len(fetched) == 1 + len(PICTURE_SOURCES)
    clock[0] += 86400  # names are due, pictures not
    asyncio.run(catalog.refresh_if_due())
    assert fetched[-1] == NAMES_URL and len(fetched) == 2 + len(PICTURE_SOURCES)


def test_a_failed_catalog_refresh_keeps_the_copy_and_retries_in_an_hour(tmp_path):
    store, catalog = catalog_with(tmp_path, {"Kilowatt Case": (60, True)})
    calls = []

    async def broken(url):
        calls.append(url)
        raise OSError("network down")
    clock = [time.time() + 2 * 86400]
    catalog.fetch, catalog.clock = broken, lambda: clock[0]
    asyncio.run(catalog.refresh_if_due())
    assert catalog.search("kilowatt") == ["Kilowatt Case"] and len(calls) == 1
    asyncio.run(catalog.refresh_if_due())  # not again right away
    assert len(calls) == 1
    clock[0] += 3601
    asyncio.run(catalog.refresh_if_due())
    assert len(calls) == 2


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
    inline.catalog.load()

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
    assert first["thumbnail_url"].endswith("/ic1/96fx96f")
    assert [r["title"] for r in params["results"]] == [CASE]
    assert not any(call[0] == "search" for call in market.calls)  # the catalogue knew it: no Steam


def test_steam_searches_only_for_names_the_catalogue_lacks(tmp_path):
    store, prices, market, bot, inline = service(tmp_path)

    async def main():
        return await inline.results(5, "kilowatt", "en"), await inline.results(5, "kilowatt", "en")
    first, again = asyncio.run(main())
    assert [r["title"] for r in first] == [OTHER, CASE]  # Steam's answer, as it came
    assert [r["title"] for r in again] == [OTHER]  # now in the catalogue: no Steam
    # Both of the first query's Steam searches (cases, then everything), none for the second.
    assert [c for c in market.calls if c[0] == "search"] == [("search", "kilowatt", True), ("search", "kilowatt", False)]


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
    inline.catalog.load()
    inline.cards.budget = Budget(limit=0)

    async def main():
        return await inline.results(5, "operation breakout", "en"), await inline.results(5, "nothing known", "en")
    known, unknown = asyncio.run(main())
    assert [r["title"] for r in known] == [CASE] and unknown == []
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
