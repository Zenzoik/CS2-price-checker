"""Phase 3: sales with realized profit, and the daily inventory check."""

import asyncio
import sqlite3

import pytest

from cs2tracker.app.db import SCHEMA_VERSION, SYNC_EVERY, QuantityLimit, Store
from cs2tracker.app.prices import InventoryService
from cs2tracker.app.server import RateLimiter
from cs2tracker.app.sync import InventorySync, message
from cs2tracker.steam import PrivateInventory, RateLimited, SteamError

from test_app import CASE, STEAMID, FakeMarket, auth, inv_item, run_import

DAY = 86400


# -- sales ------------------------------------------------------------------------

def test_selling_part_keeps_the_average_and_records_the_profit(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    store.add_lot(1, CASE, 10, 1000)
    store.set_price(CASE, 1500)
    assert store.sell(1, CASE, 4, 1200) is not None
    h = store.holding(1, CASE)
    assert (h.qty, h.buy_cents) == (6, 1000)
    sale, = store.sales(1)
    assert (sale["qty"], sale["price_cents"], sale["buy_cents"]) == (4, 1200, 1000)
    assert store.realized(1) == {"count": 1, "proceeds": 4800, "cost": 4000, "profit": 800, "unknown": 0}
    assert store.sell(1, CASE, 7, 1200) is None          # only 6 left
    assert store.sell(1, CASE, 6, 900) is not None       # the rest: position closed
    assert store.holding(1, CASE) is None
    assert store.realized(1)["profit"] == 800 - 600
    assert store.sell(2, CASE, 1, 1) is None             # someone else's


def test_realized_profit_leaves_out_sales_without_a_price_paid(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    store.add_lot(1, "A", 2, 100)
    store.add_lot(1, "B", 3, None)
    store.sell(1, "A", 2, 150)
    store.sell(1, "B", 1, 500)
    assert store.realized(1) == {"count": 2, "proceeds": 800, "cost": 200, "profit": 100, "unknown": 1}
    assert Store(tmp_path / "t.db", "UAH").realized(9)["profit"] is None


def test_realized_totals_survive_sums_beyond_int64(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    with store.conn:
        for _ in range(1000):  # 1000 x 1e16 cents: SQLite's SUM would raise
            store.conn.execute("INSERT INTO sales (user_id, hash_name, qty, price_cents, buy_cents, sold_at) "
                               "VALUES (1, 'A', 1000000, 10000000000, 0, 0)")
    assert store.realized(1)["proceeds"] == pytest.approx(1e19)


def test_a_sale_is_not_a_market_move(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    store.add_lot(1, "A", 10, 100)
    store.set_price("A", 115)
    store.save_alert(1, None, "value_pct", above=True, threshold=200)
    store.save_alert(1, "A", "profit", above=True, threshold=1000)
    store.sell(1, "A", 4, 100)
    portfolio, = [a for a in store.alerts(1) if a["hash_name"] is None]
    assert portfolio["baseline"] == portfolio["value_cents"] == 600
    store.sell(1, "A", 6, 100)                            # closed: its profit alert goes
    assert [a["metric"] for a in store.alerts(1)] == ["value_pct"]
    assert store.portfolio_history(1, 7)["points"][-1]["value"] == 0


def test_undoing_a_sale_puts_the_items_back_at_the_price_paid(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    store.add_lot(1, CASE, 5, 1000)
    first = store.sell(1, CASE, 5, 1300)
    store.add_lot(1, CASE, 5, 2000)                       # bought again, dearer
    assert store.undo_sale(1, first)
    h = store.holding(1, CASE)
    assert (h.qty, h.buy_cents) == (10, 1500)
    assert store.sales(1) == [] and store.realized(1)["count"] == 0
    assert not store.undo_sale(1, first)
    second = store.sell(1, CASE, 10, 1)
    store.add_lot(1, CASE, 999_995, 1)
    with pytest.raises(QuantityLimit):
        store.undo_sale(1, second)
    assert len(store.sales(1)) == 1                       # nothing half-done


def test_api_sell_list_and_undo(tmp_path):
    async def scenario(client, store, market):
        store.add_lot(42, CASE, 3, 400)
        post = lambda path, body, user=42: client.post(path, headers=auth(user), json=body)  # noqa: E731
        r = await post("/api/sales", {"hash_name": CASE, "qty": 2, "price": 4.5})
        body = await r.json()
        assert r.status == 200 and body["items"][0]["qty"] == 1
        assert body["realized"] == {"count": 1, "proceeds": 9.0, "cost": 8.0, "profit": 1.0, "unknown": 0}
        for bad, code in [({"hash_name": CASE, "qty": 2, "price": 1}, "sell_qty"),
                          ({"hash_name": "Other", "qty": 1, "price": 1}, "gone"),
                          ({"hash_name": CASE, "qty": 1, "price": -1}, "invalid"),
                          ({"hash_name": CASE, "qty": 0, "price": 1}, "invalid")]:
            r = await post("/api/sales", bad)
            assert (await r.json())["error"] == code, bad
        sales = (await (await client.get("/api/sales", headers=auth())).json())["sales"]
        assert [(s["qty"], s["price"], s["buy_price"], s["profit"]) for s in sales] == [(2, 4.5, 4.0, 1.0)]
        r = await post("/api/sales/delete", {"id": sales[0]["id"]}, user=7)
        assert r.status == 404                            # not theirs
        r = await post("/api/sales/delete", {"id": sales[0]["id"]})
        body = await r.json()
        assert body["sales"] == [] and body["portfolio"]["items"][0]["qty"] == 3
        assert (await post("/api/sales/delete", {"id": True})).status == 400
    run_import(tmp_path, scenario, [])


def test_undo_needs_room_when_the_position_is_gone(tmp_path):
    async def scenario(client, store, market):
        store.add_lot(42, "A", 1, 1)
        sale = store.sell(42, "A", 1, 1)
        store.add_lot(42, "B", 1, 1)
        r = await client.post("/api/sales/delete", headers=auth(), json={"id": sale})
        assert (await r.json())["error"] == "full"
    run_import(tmp_path, scenario, [], max_items=1)


# -- inventory check --------------------------------------------------------------

def test_import_remembers_the_profile_and_its_contents(tmp_path):
    async def scenario(client, store, market):
        await client.get("/api/inventory", params={"profile": STEAMID}, headers=auth())
        await client.post("/api/import", headers=auth(), json={
            "steamid": STEAMID, "price_mode": "none", "items": [{"hash_name": CASE}]})
        prefs = await (await client.get("/api/prefs", headers=auth())).json()
        assert prefs["sync"]["steamid"] == STEAMID and prefs["sync"]["enabled"] is True
        # The case left behind isn't "new" tomorrow: it was there at the import.
        assert store.record_sync(42, STEAMID, {CASE: 1, "Left": 2, "Fresh": 1}) == (["Fresh"], {})
        r = await client.post("/api/prefs", headers=auth(), json={"sync_enabled": False})
        assert (await r.json())["sync"]["enabled"] is False
        assert store.due_syncs(10**12) == []
        r = await client.post("/api/prefs", headers=auth(7), json={"sync_enabled": True})
        assert (await r.json())["error"] == "no_sync"
    run_import(tmp_path, scenario, [inv_item(CASE), inv_item("Left", 2)])


def test_changes_add_up_until_reviewed(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    store.add_lot(1, "Held", 10, 100)
    store.remember_inventory(1, STEAMID, {"Held": 10, "Old": 1}, at=0)
    assert store.pending_sync(1) is None
    assert store.record_sync(1, STEAMID, {"Held": 7, "Old": 1, "New": 2}) == (["New"], {"Held": 3})
    assert store.record_sync(1, STEAMID, {"Old": 1, "New": 2}) == ([], {"Held": 7})
    pending = store.pending_sync(1)
    assert [(x["hash_name"], x["qty"]) for x in pending["new"]] == [("New", 2)]
    assert [(x["hash_name"], x["qty"]) for x in pending["gone"]] == [("Held", 10)]  # capped at what is held
    store.sell(1, "Held", 4, 150)                         # answers four of them
    assert store.pending_sync(1)["gone"][0]["qty"] == 6
    store.import_items(1, [("New", 2, None, False)])      # imported: no longer new
    assert store.pending_sync(1)["new"] == []
    store.dismiss_sync(1)
    assert store.pending_sync(1) is None
    # Another profile imported meanwhile: a read of the old one is dropped.
    store.remember_inventory(1, "76561190000000001", {})
    assert store.record_sync(1, STEAMID, {"X": 1}) == ([], {})


def test_the_reads_are_spread_over_the_day(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    store.remember_inventory(1, STEAMID, {}, at=1000)
    store.remember_inventory(2, STEAMID, {}, at=5000)
    assert store.due_syncs(1000 + SYNC_EVERY - 1) == []
    assert [d["user_id"] for d in store.due_syncs(10**9, limit=5)] == [1, 2]
    store.record_sync(1, STEAMID, {}, at=1000 + SYNC_EVERY)
    assert [d["user_id"] for d in store.due_syncs(5000 + SYNC_EVERY)] == [2]


class Outbox:
    def __init__(self):
        self.sent = []

    async def __call__(self, user_id, text):
        self.sent.append((user_id, text))
        return True


def syncer(tmp_path, items, limiter=None):
    market = FakeMarket()
    market.inventory_items = items
    store = Store(tmp_path / "t.db", "UAH")
    clock = [0.0]
    outbox = Outbox()
    job = InventorySync(store, InventoryService(market, clock=lambda: clock[0]),
                        limiter or RateLimiter(3, code="inventory_busy"), outbox, clock=lambda: clock[0])
    return store, market, job, outbox, clock


def test_the_daily_check_writes_once_about_what_changed(tmp_path):
    store, market, job, outbox, clock = syncer(tmp_path, [inv_item("Kept"), inv_item("New Case", 3)])
    store.add_lot(1, "Kept", 2, 100)
    store.set_prefs(1, write_access=1)
    store.remember_inventory(1, STEAMID, {"Kept": 2}, at=0)
    assert not asyncio.run(job.sync_next())               # not due yet
    clock[0] = SYNC_EVERY
    assert asyncio.run(job.sync_next())
    (user, text), = outbox.sent
    assert user == 1 and "(1): New Case." in text and "Kept ×1" in text
    clock[0] = 2 * SYNC_EVERY + 1
    asyncio.run(job.sync_next())
    assert len(outbox.sent) == 1                          # nothing changed since: no message
    assert [c[0] for c in market.calls] == ["inventory", "inventory"]


def test_the_check_leaves_room_for_users(tmp_path):
    limiter = RateLimiter(3, code="inventory_busy")
    store, market, job, outbox, clock = syncer(tmp_path, [], limiter)
    store.remember_inventory(1, STEAMID, {}, at=-SYNC_EVERY)
    limiter.take(0, 2)                                    # two users just imported
    assert not asyncio.run(job.sync_next()) and market.calls == []
    assert store.due_syncs(0)                             # still due
    limiter.calls.clear()
    assert asyncio.run(job.sync_next()) and len(limiter.calls[0]) == 1


@pytest.mark.parametrize("error, retry, shown", [
    (PrivateInventory(STEAMID), DAY, "private"),
    (RateLimited("429"), 3600, None),
    (SteamError("down"), 3600, None),
])
def test_a_failed_check_waits_and_says_why(tmp_path, error, retry, shown):
    store, market, job, outbox, clock = syncer(tmp_path, error)
    store.remember_inventory(1, STEAMID, {}, at=-SYNC_EVERY)
    asyncio.run(job.sync_next())
    assert store.due_syncs(retry - 1) == [] and store.due_syncs(retry)
    assert store.sync_settings(1)["error"] == shown
    assert outbox.sent == []


def test_no_message_without_write_access(tmp_path):
    store, market, job, outbox, clock = syncer(tmp_path, [inv_item("New")])
    store.remember_inventory(1, STEAMID, {}, at=-SYNC_EVERY)
    asyncio.run(job.sync_next())
    assert outbox.sent == [] and store.pending_sync(1)["new"][0]["name"] == "New"


def test_messages_name_a_few_and_count_the_rest():
    text = message(["a", "b", "c", "d", "e"], {"g": 2}, {"a": "Alpha"}, "ru")
    assert "Новое в инвентаре Steam (5): Alpha, b, c и ещё 2" in text and "g ×2" in text
    assert message([], {}, {}, "en") is None


def test_api_shows_and_dismisses_what_was_found(tmp_path):
    async def scenario(client, store, market):
        store.add_lot(42, "Held", 1, 100)
        store.remember_items([("New", "New Case", "ic")])
        store.remember_inventory(42, STEAMID, {"Held": 1}, at=0)
        store.record_sync(42, STEAMID, {"New": 2})
        body = await (await client.get("/api/portfolio", headers=auth())).json()
        assert body["sync"] == {"steamid": STEAMID,
                                "new": [{"hash_name": "New", "name": "New Case", "icon": "ic", "qty": 2}],
                                "gone": [{"hash_name": "Held", "name": "Held", "icon": None, "qty": 1}]}
        body = await (await client.post("/api/sync/dismiss", headers=auth(), json={})).json()
        assert body["sync"] is None
    run_import(tmp_path, scenario, [])


def test_migrates_v7_database(tmp_path):
    path = tmp_path / "t.db"
    store = Store(path, "UAH")
    store.add_lot(1, CASE, 1, 1)
    with store.conn:
        store.conn.execute("DROP TABLE sales")
        store.conn.execute("DROP TABLE inventory_sync")
        store.conn.execute("UPDATE meta SET value = '7' WHERE key = 'schema'")
    store.close()
    store = Store(path, "UAH")
    assert store.conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == str(SCHEMA_VERSION)
    assert store.sell(1, CASE, 1, 5) and store.pending_sync(1) is None
    assert sqlite3.connect(path).execute("PRAGMA integrity_check").fetchone()[0] == "ok"
