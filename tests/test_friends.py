"""Friends: invite links, what a friend sees, and the bot's side of an invite."""

import asyncio
import sqlite3

import pytest
from aiohttp.test_utils import TestClient, TestServer

from cs2tracker.app.db import INVITE_RENEW, INVITE_TTL, MAX_FRIENDS, SCHEMA_VERSION, Store
from cs2tracker.app.friends import summary
from cs2tracker.app.server import create_app

from test_app import CASE, FakeMarket, RecordingBot, auth, make_service, result, settings

OTHER = "Chroma 2 Case"


class FriendlyBot:
    username = "cstrackerbot"

    def __init__(self):
        self.messages = []

    async def message_user(self, user_id, text):
        self.messages.append((user_id, text))
        return True


def run(tmp_path, scenario, bot=None):
    market = FakeMarket(books={CASE: (4.21, 4.64)}, containers=[result(CASE)])
    store, prices = make_service(tmp_path, market)
    app = create_app(settings(), store, prices, bot=bot if bot is not None else FriendlyBot())

    async def main():
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            await scenario(client, store, app)
        finally:
            await client.close()
    asyncio.run(main())


def code_of(link):
    return link.rsplit("fr_", 1)[1]


# -- store ------------------------------------------------------------------------

def test_invite_codes_are_stable_until_renewed(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    code = store.invite_code(1)
    assert store.invite_code(1) == code and store.invite_owner(code) == 1
    fresh = store.invite_code(1, renew=True)
    assert fresh != code and store.invite_owner(code) is None and store.invite_owner(fresh) == 1
    assert store.invite_code(2) != fresh


def test_invite_links_expire_after_a_day(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    code = store.invite_code(1, at=1000)
    assert store.invite_code(1, at=1000 + INVITE_RENEW - 1) == code  # still fresh: the same link
    assert store.invite_owner(code, now=1000 + INVITE_TTL - 1) == 1
    assert store.invite_owner(code, now=1000 + INVITE_TTL) is None
    assert store.invite_lookup(code, now=1000 + INVITE_TTL) == (1, True)
    # Half a day old: the app hands out a new one, and the old one still works out its day.
    fresh = store.invite_code(1, at=1000 + INVITE_RENEW)
    assert fresh != code and store.invite_owner_code(1) == fresh
    assert store.invite_owner(code, now=1000 + INVITE_RENEW + 1) == 1
    assert store.invite_owner(fresh, now=1000 + INVITE_RENEW + 1) == 1
    # "New link" (or removing a friend) voids all of them at once.
    newest = store.invite_code(1, renew=True, at=1000 + INVITE_RENEW + 2)
    assert store.invite_lookup(code) == (None, False) and store.invite_lookup(fresh) == (None, False)
    assert store.invite_owner(newest, now=1000 + INVITE_RENEW + 3) == 1


def test_friendship_goes_both_ways(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    assert store.add_friend(1, 1) == "self"
    assert store.add_friend(1, 2) == "added"
    assert store.add_friend(2, 1) == "already"
    assert store.are_friends(1, 2) and store.are_friends(2, 1)
    assert [f["user_id"] for f in store.friends(2)] == [1]
    assert store.remove_friend(2, 1) and not store.are_friends(1, 2) and not store.friends(1)
    assert not store.remove_friend(2, 1)


def test_friends_are_capped_on_both_sides(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    for n in range(MAX_FRIENDS):
        store.add_friend(1, 100 + n)
    assert store.add_friend(2, 1) == "full" and store.add_friend(1, 2) == "full"
    assert store.add_friend(2, 3) == "added"


def test_schema_v9_gains_the_friends_switch(tmp_path):
    path = tmp_path / "t.db"
    Store(path, "UAH").set_prefs(1, digest="daily")
    conn = sqlite3.connect(path)
    with conn:  # back to v9: prefs without the column
        conn.execute("""CREATE TABLE prefs_v9 (user_id INTEGER PRIMARY KEY, digest TEXT NOT NULL DEFAULT 'off',
                        digest_hour INTEGER NOT NULL DEFAULT 10, tz TEXT, digest_sent TEXT,
                        digest_offered INTEGER NOT NULL DEFAULT 0, write_access INTEGER NOT NULL DEFAULT 0)""")
        conn.execute("INSERT INTO prefs_v9 SELECT user_id, digest, digest_hour, tz, digest_sent, "
                     "digest_offered, write_access FROM prefs")
        conn.execute("DROP TABLE prefs")
        conn.execute("ALTER TABLE prefs_v9 RENAME TO prefs")
        conn.execute("UPDATE meta SET value = '9' WHERE key = 'schema'")
    conn.close()
    store = Store(path, "UAH")
    assert store.prefs(1)["digest"] == "daily" and store.prefs(1)["friends_view"] == "percent"
    store.set_prefs(1, friends_view="full")
    assert store.prefs(1)["friends_view"] == "full"
    assert store.conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == str(SCHEMA_VERSION)


# -- what a friend sees ---------------------------------------------------------------

def portfolio(store):
    store.set_price(CASE, 1150, at=0)      # 10.00 net of the fee
    store.set_price(OTHER, 2300)           # 20.00 net
    store.add_lot(1, CASE, 3, 800)         # paid 24.00, worth 30.00
    store.add_lot(1, OTHER, 1, None)       # price paid unknown: out of the profit
    store.add_lot(1, "Unpriced Item", 1, 500)


def test_percent_view_is_percentages_only(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    portfolio(store)
    s = summary(store, 1, view="percent", detail=True)
    # Market moves only. Not profit: with the holdings known (a public Steam inventory),
    # it would give the price paid away. Nor what is held, nor how much.
    assert set(s) == {"view", "change_24h", "change_7d", "change_30d"}
    assert set(summary(store, 1, view="percent")) == {"view", "change_24h", "change_30d"}


def test_full_view_is_the_whole_portfolio(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    portfolio(store)
    s = summary(store, 1, view="full", detail=True)
    assert s["value"] == 50.0 and s["pnl"] == 6.0 and s["invested"] == 24.0 and s["items_count"] == 3
    assert [i["hash_name"] for i in s["items"]] == [CASE, OTHER, "Unpriced Item"]
    case = s["items"][0]
    assert (case["qty"], case["price"], case["buy_price"], case["value"]) == (3, 11.5, 8.0, 30.0)
    assert case["pnl_ratio"] == pytest.approx(0.25) and s["items"][1]["pnl_ratio"] is None
    assert s["items"][2]["value"] is None and s["items"][2]["buy_price"] == 5.0


def test_summary_of_an_empty_portfolio(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    s = summary(store, 1, view="full", detail=True)
    assert s["items_count"] == 0 and s["pnl_ratio"] is None and s["change_24h"] is None
    assert s["value"] == 0 and s["items"] == [] and s["change_7d"] is None


# -- API ------------------------------------------------------------------------------

def test_invite_accept_and_see_each_other(tmp_path):
    bot = FriendlyBot()

    async def scenario(client, store, app):
        store.set_price(CASE, 1150)
        store.add_lot(42, CASE, 2, 800)
        store.set_prefs(42, write_access=1)
        mine = await (await client.get("/api/friends", headers=auth(42))).json()
        assert mine["link"].startswith("https://t.me/cstrackerbot?start=fr_")
        assert mine["friends"] == [] and mine["view"] == "percent"
        assert "value" not in mine["me"] and "pnl_ratio" not in mine["me"]  # yourself as friends see you
        code = code_of(mine["link"])

        info = await (await client.get("/api/friends/invite", params={"code": code}, headers=auth(7))).json()
        assert info == {"id": 42, "status": "new", "name": "Ann"}
        r = await client.post("/api/friends/accept", json={"code": code}, headers=auth(7))
        assert await r.json() == {"id": 42, "status": "added"}
        await asyncio.sleep(0)
        assert bot.messages and bot.messages[0][0] == 42 and "Ann" in bot.messages[0][1]
        r = await client.post("/api/friends/accept", json={"code": code}, headers=auth(7))
        assert (await r.json())["status"] == "already" and len(bot.messages) == 1

        theirs = await (await client.get("/api/friends", headers=auth(7))).json()
        friend, = theirs["friends"]
        assert friend["id"] == 42 and "pnl_ratio" not in friend and "value" not in friend
        detail = await (await client.get("/api/friends/42", headers=auth(7))).json()
        assert detail["view"] == "percent" and "items" not in detail

        # The owner shows everything: now the friend sees the portfolio.
        r = await client.post("/api/prefs", json={"friends_view": "full"}, headers=auth(42))
        assert (await r.json())["friends_view"] == "full"
        assert (await client.post("/api/prefs", json={"friends_view": "all"}, headers=auth(42))).status == 400
        detail = await (await client.get("/api/friends/42", headers=auth(7))).json()
        assert detail["view"] == "full" and detail["value"] == 20.0 and detail["pnl_ratio"] == pytest.approx(0.25)
        assert detail["items"][0]["qty"] == 2 and detail["items"][0]["buy_price"] == 8.0
        assert (await (await client.get("/api/friends", headers=auth(42))).json())["friends"][0]["id"] == 7

        # Either side can end it, for both, and the removed one can't come back with the old link.
        r = await client.post("/api/friends/delete", json={"id": 7}, headers=auth(42))
        body = await r.json()
        assert body["removed"] and code_of(body["link"]) != code
        r = await client.get("/api/friends/42", headers=auth(7))
        assert r.status == 404 and (await r.json())["error"] == "not_friend"
        r = await client.post("/api/friends/accept", json={"code": code}, headers=auth(7))
        assert r.status == 404
        # Back with the new link: the inviter isn't told twice the same day.
        r = await client.post("/api/friends/accept", json={"code": code_of(body["link"])}, headers=auth(7))
        assert (await r.json())["status"] == "added"
        await asyncio.sleep(0)
        assert len(bot.messages) == 1
    run(tmp_path, scenario, bot)


def test_invite_edge_cases(tmp_path):
    async def scenario(client, store, app):
        code = code_of((await (await client.get("/api/friends", headers=auth(42))).json())["link"])
        info = await (await client.get("/api/friends/invite", params={"code": code}, headers=auth(42))).json()
        assert info["status"] == "self"
        r = await client.post("/api/friends/accept", json={"code": code}, headers=auth(42))
        assert r.status == 400 and (await r.json())["error"] == "own_invite"
        for bad in ("nope", "x" * 40, 5, None, "a/b\\c.d.e.f"):
            r = await client.post("/api/friends/accept", json={"code": bad}, headers=auth(7))
            assert r.status == 404 and (await r.json())["error"] == "invite_invalid"
        # A new link voids the old one; friendships made with it stay.
        await client.post("/api/friends/accept", json={"code": code}, headers=auth(7))
        fresh = code_of((await (await client.post("/api/friends/link", headers=auth(42))).json())["link"])
        assert fresh != code
        r = await client.post("/api/friends/accept", json={"code": code}, headers=auth(8))
        assert r.status == 404 and store.are_friends(7, 42)
        for path in ("/api/friends/8", "/api/friends/abc", "/api/friends/0"):
            assert (await client.get(path, headers=auth(7))).status in (400, 404)
    run(tmp_path, scenario)


def test_no_link_without_the_bot(tmp_path):
    async def scenario(client, store, app):
        assert (await (await client.get("/api/friends", headers=auth(42))).json())["link"] is None
        assert (await client.post("/api/friends/link", headers=auth(42))).status == 503
    run(tmp_path, scenario, bot=object())


def test_new_friend_is_not_announced_without_write_access(tmp_path):
    bot = FriendlyBot()

    async def scenario(client, store, app):
        code = code_of((await (await client.get("/api/friends", headers=auth(42))).json())["link"])
        await client.post("/api/friends/accept", json={"code": code}, headers=auth(7))
        await asyncio.sleep(0)
        assert store.are_friends(7, 42) and bot.messages == []
    run(tmp_path, scenario, bot)


# -- the bot ----------------------------------------------------------------------------

def start(bot, text, user_id=5, lang="ru"):
    bot.sent.clear()
    asyncio.run(bot.handle({"update_id": 1, "message": {
        "text": text, "chat": {"id": user_id, "type": "private"},
        "from": {"id": user_id, "first_name": "Bo", "language_code": lang}}}))
    (method, params), = bot.sent
    return params["text"], params["reply_markup"]["inline_keyboard"][0][0]


def test_bot_opens_the_app_on_an_invite(tmp_path):
    bot = RecordingBot(settings())
    bot.store = Store(tmp_path / "t.db", "UAH")
    bot.store.touch_user({"id": 42, "first_name": "Ann", "username": "ann"}, "app")
    code = bot.store.invite_code(42)
    text, button = start(bot, f"/start fr_{code}")
    assert "Ann (@ann)" in text and "друзья" in text  # anyone can be called Ann; the handle is unique
    assert button["web_app"]["url"] == f"https://example.com/?friend={code}"
    text, button = start(bot, f"/start fr_{code}", user_id=42, lang="en")
    assert "your own invite" in text and button["web_app"]["url"] == "https://example.com/"
    bot.store.conn.execute("UPDATE friend_invites SET created_at = created_at - ?", (INVITE_TTL,))
    text, button = start(bot, f"/start fr_{code}")
    assert "устарела" in text and button["web_app"]["url"] == "https://example.com/"
    text, button = start(bot, "/start fr_unknowncode")  # a void link: the usual welcome
    assert button["web_app"]["url"] == "https://example.com/" and "CS2" in text
