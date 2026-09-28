"""Phase 2: price alerts, the watchlist and the digest."""

import asyncio
import calendar
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cs2tracker.app.db import MAX_ALERTS, MAX_WATCHED, Store
from cs2tracker.app.notify import Notifier, current_value, decide, money, percent
from cs2tracker.app.settings import AppSettings
from cs2tracker.app.telegram import BotApiError, TelegramBot

from test_app import CASE, FakeMarket, auth, make_service, run_api

JAN_1 = calendar.timegm((2024, 1, 1, 12, 0, 0))  # a Monday
DAY = 86400


class Outbox:
    def __init__(self, ok=True):
        self.sent = []
        self.ok = ok

    async def __call__(self, user_id, text):
        self.sent.append((user_id, text))
        return self.ok


def reachable(store, *users):
    for user in users:
        store.set_prefs(user, write_access=1)
    return store


def notifier(store, outbox, now=None):
    clock = now or [JAN_1]
    return Notifier(store, outbox, currency="UAH", clock=lambda: clock[0]), clock


def check(n):
    return asyncio.run(n.check_alerts())


# -- alert rules -----------------------------------------------------------------

def test_price_alert_fires_once_per_crossing(tmp_path):
    store = reachable(Store(tmp_path / "t.db", "UAH"), 1)
    store.watch(1, CASE)
    store.save_alert(1, CASE, "price", above=False, threshold=4000)  # "below 40.00"
    outbox = Outbox()
    n, _ = notifier(store, outbox)
    for cents, expected in [(4200, 0), (3950, 1), (3900, 1), (3990, 1),  # still below: once
                            (4050, 1),   # back above, but inside the 2 % margin: not re-armed
                            (4100, 1),   # re-armed
                            (3999, 2),   # second crossing: second message
                            (4200, 2)]:
        store.set_price(CASE, cents)
        check(n)
        assert len(outbox.sent) == expected, cents
    assert "39.99" in outbox.sent[-1][1]  # no known language: English


def test_everything_due_for_one_user_goes_in_one_message(tmp_path):
    store = reachable(Store(tmp_path / "t.db", "UAH"), 1, 2)
    store.add_lot(1, "A", 1, 100)
    store.add_lot(1, "B", 1, 100)
    store.save_alert(1, "A", "price", above=True, threshold=150)
    store.save_alert(1, "B", "profit", above=True, threshold=2000)  # +20 % on the price paid
    store.save_alert(2, None, "value_pct", above=True, threshold=1000)
    store.set_price("A", 200)
    store.set_price("B", 150)  # net 1.30 on 1.00 paid: +30 %
    outbox = Outbox()
    n, _ = notifier(store, outbox)
    assert check(n) == 1
    (user, text), = outbox.sent
    assert user == 1 and text.count("•") == 2 and "+30.0%" in text


def test_portfolio_alert_counts_market_moves_not_purchases(tmp_path):
    store = reachable(Store(tmp_path / "t.db", "UAH"), 1)
    store.add_lot(1, "A", 10, 100)
    store.set_price("A", 115)                      # value 10.00
    store.save_alert(1, None, "value_pct", above=True, threshold=200)  # +2 %
    outbox = Outbox()
    n, _ = notifier(store, outbox)
    store.add_lot(1, "A", 10, 100)                 # doubled by buying: not a gain
    store.import_items(1, [("B", 5, None, False)])
    store.set_price("B", 1150)                     # a new item's first price: not a gain
    check(n)
    assert outbox.sent == []
    store.set_price("A", 127)                      # net 1.10: +2.00 on 70.00
    check(n)
    assert len(outbox.sent) == 1 and "+2.9%" in outbox.sent[0][1]
    alert, = store.alerts(1)
    assert alert["baseline"] == 20 * 100 + 5 * 1000
    store.delete_holding(1, "B")                   # selling is not a loss either
    assert current_value(store.alerts(1)[0]) == 1000


def test_rules_cover_both_directions_and_unknown_values():
    alert = {"metric": "profit", "above": 0, "threshold": -1000, "armed": 1}
    assert decide(alert, None) is None
    assert decide(alert, -900) is None
    assert decide(alert, -1000) == "fire"
    alert["armed"] = 0
    assert decide(alert, -960) is None     # inside the 0.5 pp margin
    assert decide(alert, -940) == "rearm"
    assert current_value({"metric": "profit", "price_cents": 115, "qty": 1, "buy_cents": None}) is None
    assert current_value({"metric": "value_pct", "baseline": 0, "value_cents": 5}) is None


class Blocked(Outbox):
    """Delivery as the bot does it for a user who blocked it."""

    def __init__(self, store):
        super().__init__(ok=False)
        self.store = store

    async def __call__(self, user_id, text):
        self.store.set_prefs(user_id, write_access=0)
        return await super().__call__(user_id, text)


def test_undelivered_alert_waits_for_permission_and_then_arrives(tmp_path):
    store = reachable(Store(tmp_path / "t.db", "UAH"), 1)
    store.save_alert(1, CASE, "price", above=True, threshold=100)
    store.set_price(CASE, 200)
    n, _ = notifier(store, Blocked(store))
    check(n)
    check(n)
    assert len(n.send.sent) == 1  # blocked: tried once, then no more attempts
    assert store.alerts(1)[0]["armed"] == 1
    store.set_prefs(1, write_access=1)  # the user allowed messages again
    outbox = Outbox()
    n, _ = notifier(store, outbox)
    check(n)
    check(n)
    assert len(outbox.sent) == 1


def test_no_message_without_permission(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")  # never wrote to the bot or allowed it
    store.save_alert(1, CASE, "price", above=True, threshold=100)
    store.set_price(CASE, 200)
    outbox = Outbox()
    n, _ = notifier(store, outbox)
    check(n)
    assert outbox.sent == [] and store.alerts(1)[0]["armed"] == 1


def test_alerted_items_are_refreshed_even_when_not_held_or_watched(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    store.save_alert(1, CASE, "price", above=False, threshold=100)
    assert [name for name, _ in store.tracked()] == [CASE]
    store.add_lot(1, "B", 1, 100)
    store.save_alert(1, "B", "profit", above=True, threshold=1000)
    store.save_alert(1, "B", "price", above=True, threshold=1000)
    store.delete_holding(1, "B")  # sold: the profit alert has nothing to measure
    assert [a["metric"] for a in store.alerts(1) if a["hash_name"] == "B"] == ["price"]
    assert "B" in [name for name, _ in store.tracked()]


def test_portfolio_alert_fires_again_after_crossing_back(tmp_path):
    store = reachable(Store(tmp_path / "t.db", "UAH"), 1)
    store.add_lot(1, "A", 100, 100)
    store.set_price("A", 115)  # value 100.00
    store.save_alert(1, None, "value_amount", above=False, threshold=-1000)  # down 10.00
    outbox = Outbox()
    n, _ = notifier(store, outbox)
    for cents, expected in [(104, 1), (100, 1), (115, 1), (104, 2)]:
        store.set_price("A", cents)
        check(n)
        assert len(outbox.sent) == expected, cents


def test_alert_limits_and_edits(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    ids = [store.save_alert(1, CASE, "price", above=True, threshold=100 + i) for i in range(MAX_ALERTS)]
    assert None not in ids
    assert store.save_alert(1, CASE, "price", above=True, threshold=1) is None
    assert store.save_alert(2, CASE, "price", above=True, threshold=1) is not None  # per user
    assert store.save_alert(2, CASE, "price", above=False, threshold=50, alert_id=ids[0]) is None  # not theirs
    assert store.save_alert(1, CASE, "price", above=False, threshold=50, alert_id=ids[0]) == ids[0]
    assert store.delete_alert(1, ids[0]) and not store.delete_alert(1, ids[0])


def test_formatting_follows_the_language():
    assert money(1479500, "UAH", "ru") == "14 795 ₴"
    assert money(-1250, "USD", "en", sign=True) == "−$12.50"
    assert money(1250, "UAH", "uk", sign=True) == "+12,50 ₴"
    assert percent(0.021, "en") == "+2.1%"
    assert percent(-0.03, "uk") == "−3,0 %"


# -- watchlist ---------------------------------------------------------------------

def test_watched_items_are_refreshed_but_never_counted(tmp_path):
    market = FakeMarket(books={CASE: (4.21, 4.64)})
    store, prices = make_service(tmp_path, market)
    store.watch(1, CASE)
    assert asyncio.run(prices.refresh()) == 1
    assert prices.passed.is_set()
    assert store.holdings(1) == [] and store.portfolio_value(1) == 0
    assert store.watching(1)[0]["price_cents"] == 464
    store.add_lot(1, CASE, 1, 400)  # bought: it's a holding now
    assert store.watching(1) == []
    assert store.watch(1, CASE) and store.watching(1) == []  # held items aren't watched
    for i in range(MAX_WATCHED):
        assert store.watch(2, f"item {i}")
    assert not store.watch(2, "one more")


# -- digest ------------------------------------------------------------------------

def digest_store(tmp_path, clock):
    store = Store(tmp_path / "t.db", "UAH")
    store.touch_user({"id": 1, "language_code": "en"}, "bot", at=clock[0])
    for name, qty, before, after in (("A", 10, 100, 115), ("B", 1, 1000, 900), ("C", 2, 500, 500)):
        store.add_lot(1, name, qty, before)
        store.set_price(name, before, at=clock[0] - DAY)
        store.set_price(name, after, at=clock[0])
    return store


def test_daily_digest_once_at_the_chosen_local_hour(tmp_path, monkeypatch):
    clock = [JAN_1 - 3 * 3600]  # 09:00 UTC = 11:00 in Kyiv
    monkeypatch.setattr("cs2tracker.app.db.time.time", lambda: clock[0])
    store = reachable(digest_store(tmp_path, clock), 1)
    store.set_prefs(1, digest="daily", digest_hour=11, tz="Europe/Kyiv")
    outbox = Outbox()
    n, _ = notifier(store, outbox, clock)
    clock[0] -= 60  # 10:59 local: not yet
    assert asyncio.run(n.send_digests()) == 0
    clock[0] += 120
    assert asyncio.run(n.send_digests()) == 1
    assert asyncio.run(n.send_digests()) == 0  # once per day
    text = outbox.sent[0][1]
    assert text.startswith("📊 Portfolio: ")
    assert "Best: A +15.0%" in text and "Worst: B −10.0%" in text
    clock[0] += DAY + 4 * 3600  # next day, but more than 3 h late (e.g. after a restart): skipped
    assert asyncio.run(n.send_digests()) == 0


def test_weekly_digest_only_on_mondays(tmp_path, monkeypatch):
    clock = [JAN_1 + DAY]  # Tuesday 12:00 UTC
    monkeypatch.setattr("cs2tracker.app.db.time.time", lambda: clock[0])
    store = reachable(digest_store(tmp_path, clock), 1)
    store.set_prefs(1, digest="weekly", digest_hour=12)
    outbox = Outbox()
    n, _ = notifier(store, outbox, clock)
    assert asyncio.run(n.send_digests()) == 0
    clock[0] += 6 * DAY  # Monday
    assert asyncio.run(n.send_digests()) == 1
    assert outbox.sent[0][1].startswith("📊 Your week: ")


def test_digest_is_offered_once_after_three_items_or_an_import(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    store.add_lot(1, "A", 1, 100)
    store.add_lot(1, "B", 1, 100)
    assert not store.offer_digest(1)
    store.add_lot(1, "C", 1, 100)
    assert store.offer_digest(1)
    store.set_prefs(1, digest_offered=1)
    assert not store.offer_digest(1)
    store.log_event(2, "import")
    assert store.offer_digest(2)


# -- bot -----------------------------------------------------------------------------

class RefusingBot(TelegramBot):
    async def call(self, method, **params):
        raise BotApiError("sendMessage: Forbidden: bot was blocked by the user")


def test_blocked_user_is_asked_for_permission_again(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    store.set_prefs(1, write_access=1)
    bot = RefusingBot(AppSettings(bot_token="1:x", public_url="https://x/"), session=None, store=store)
    assert asyncio.run(bot.message_user(1, "hi")) is False
    assert store.prefs(1)["write_access"] == 0


def test_migration_lets_the_bot_write_to_users_who_wrote_to_it(tmp_path):
    path = tmp_path / "t.db"
    store = Store(path, "UAH")
    store.touch_user({"id": 5}, "bot")
    store.touch_user({"id": 6}, "app")
    with store.conn:
        store.conn.execute("DROP TABLE prefs")
        store.conn.execute("UPDATE meta SET value = '6' WHERE key = 'schema'")
    store.close()
    store = Store(path, "UAH")
    assert store.prefs(5)["write_access"] == 1 and store.prefs(6)["write_access"] == 0


# -- API -----------------------------------------------------------------------------

def test_api_alerts_crud_and_validation(tmp_path):
    async def scenario(client, store, market):
        post = lambda path, body, user=42: client.post(path, json=body, headers=auth(user))  # noqa: E731
        r = await post("/api/alerts", {"hash_name": CASE, "metric": "profit", "above": True, "threshold": 0.2})
        assert r.status == 400 and (await r.json())["error"] == "no_buy_price"
        r = await post("/api/alerts", {"hash_name": CASE, "metric": "price", "above": False,
                                       "threshold": 40, "write_access": True})
        alerts = (await r.json())["alerts"]
        assert alerts == [{"id": alerts[0]["id"], "hash_name": CASE, "name": CASE, "icon": None,
                           "metric": "price", "above": False, "threshold": 40.0, "current": 4.64,
                           "armed": True, "fired_at": None, "created_at": alerts[0]["created_at"],
                           "baseline": None, "price": 4.64}]
        assert store.prefs(42)["write_access"] == 1
        alert_id = alerts[0]["id"]
        r = await post("/api/alerts", {"id": alert_id, "hash_name": CASE, "metric": "price",
                                       "above": True, "threshold": 50})
        assert (await r.json())["alerts"][0]["above"] is True
        assert (await post("/api/alerts", {"id": alert_id, "hash_name": CASE, "metric": "price",
                                           "above": True, "threshold": 50}, user=7)).status == 404
        for bad in ({"hash_name": None, "metric": "price", "above": True, "threshold": 1},
                    {"hash_name": None, "metric": "value_pct", "above": True, "threshold": -0.1},
                    {"hash_name": None, "metric": "value_amount", "above": False, "threshold": 0},
                    {"hash_name": CASE, "metric": "price", "above": True, "threshold": float("nan")},
                    {"hash_name": CASE, "metric": "price", "above": "yes", "threshold": 1},
                    {"hash_name": CASE, "metric": "price", "above": True, "threshold": 0.001},  # rounds to 0
                    {"hash_name": None, "metric": "value_pct", "above": True, "threshold": 0.00001},
                    {"hash_name": None, "metric": "value_amount", "above": False, "threshold": -0.001},
                    {"hash_name": CASE, "metric": "price", "above": True, "threshold": 10**400},
                    {"id": 2**70, "hash_name": CASE, "metric": "price", "above": True, "threshold": 1}):
            assert (await post("/api/alerts", bad)).status == 400, bad
        r = await post("/api/alerts", {"hash_name": None, "metric": "value_pct", "above": False, "threshold": -0.1})
        assert len((await r.json())["alerts"]) == 2
        assert (await post("/api/alerts/delete", {"id": 2**70})).status == 400
        r = await post("/api/alerts/delete", {"id": alert_id}, user=7)  # someone else's: untouched
        assert len(store.alerts(42)) == 2
        r = await post("/api/alerts/delete", {"id": alert_id})
        assert [a["metric"] for a in (await r.json())["alerts"]] == ["value_pct"]
    run_api(tmp_path, scenario)


def test_api_watch_and_prefs(tmp_path):
    async def scenario(client, store, market):
        post = lambda path, body: client.post(path, json=body, headers=auth())  # noqa: E731
        r = await post("/api/watch", {"hash_name": CASE, "on": True})
        body = await r.json()
        assert body["items"] == [] and body["watching"][0]["hash_name"] == CASE
        r = await post("/api/watch", {"hash_name": CASE, "on": False})
        assert (await r.json())["watching"] == []
        assert (await post("/api/watch", {"hash_name": CASE, "on": "yes"})).status == 400

        r = await post("/api/prefs", {"digest": "daily", "digest_hour": 9, "tz": "Europe/Kyiv"})
        assert await r.json() == {"digest": "daily", "digest_hour": 9, "tz": "Europe/Kyiv", "can_notify": False}
        assert store.prefs(42)["digest_offered"] == 1
        for bad in ({"digest": "hourly"}, {"digest_hour": 24}, {"digest_hour": True},
                    {"tz": "Mars/Olympus"}, {"write_access": 1}):
            assert (await post("/api/prefs", bad)).status == 400, bad
        r = await post("/api/prefs", {"write_access": True})
        assert (await r.json())["can_notify"] is True
        # Today's digest already went out: moving the hour later must not send another.
        today = datetime.now(ZoneInfo("Europe/Kyiv")).date().isoformat()
        store.set_prefs(42, digest_sent=today)
        await post("/api/prefs", {"digest_hour": 23})
        await post("/api/prefs", {"digest": "off"})
        await post("/api/prefs", {"digest": "daily"})
        assert store.prefs(42)["digest_sent"] == today
    run_api(tmp_path, scenario)
