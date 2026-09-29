"""Phase 4: folders, CSV export, share pictures and admin broadcasts."""

import asyncio
import sqlite3

import pytest
from aiohttp.test_utils import TestClient, TestServer

from cs2tracker.app.broadcast import Broadcaster
from cs2tracker.app.db import MAX_FOLDERS, SCHEMA_VERSION, Store
from cs2tracker.app.export import holdings_csv, sales_csv
from cs2tracker.app.server import create_app
from cs2tracker.app.telegram import BotApiError, TelegramBot

from test_app import CASE, FakeMarket, FakeResp, auth, make_service, result, settings

JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 100


class FakeBot:
    username = "cstrackerbot"

    def __init__(self, prepare_fails=False, send_fails=False):
        self.prepare_fails = prepare_fails
        self.send_fails = send_fails
        self.documents, self.photos, self.prepared = [], [], []

    async def send_document(self, user_id, filename, data, caption=""):
        if self.send_fails:
            raise BotApiError("sendDocument: Forbidden: bot was blocked by the user")
        self.documents.append((user_id, filename, data))

    async def send_photo(self, user_id, data, caption=""):
        self.photos.append((user_id, data, caption))

    async def prepare_share(self, user_id, url, caption=""):
        if self.prepare_fails:
            raise BotApiError("savePreparedInlineMessage: Bad Request: method not found")
        self.prepared.append((user_id, url, caption))
        return "prep-1"


def run(tmp_path, scenario, bot=None, **settings_kw):
    market = FakeMarket(books={CASE: (4.21, 4.64)}, containers=[result(CASE)])
    store, prices = make_service(tmp_path, market)
    app = create_app(settings(**settings_kw), store, prices, bot=bot)

    async def main():
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            await scenario(client, store)
        finally:
            await client.close()
    asyncio.run(main())


# -- folders ----------------------------------------------------------------------

def test_folders_group_positions(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    main = store.save_folder(1, "Main")
    alt = store.save_folder(1, "Alt")
    assert store.save_folder(1, "Main") is None              # taken
    assert store.save_folder(1, "Alt", folder_id=alt) == alt  # renaming to its own name is fine
    assert store.save_folder(2, "Hijack", folder_id=main) is None
    store.add_lot(1, "A", 1, 100, folder_id=main)
    store.add_lot(1, "A", 1, 100, folder_id=alt)              # buying more keeps its folder
    store.add_lot(1, "B", 1, 100, folder_id=store.save_folder(2, "Theirs"))  # someone else's: ignored
    store.import_items(1, [("C", 2, None, False)], folder_id=alt)
    assert {h.hash_name: h.folder_id for h in store.holdings(1)} == {"A": main, "B": None, "C": alt}
    assert store.move_holdings(1, ["B", "Nope"], main) == 1
    assert store.move_holdings(1, ["B"], 999) is None
    assert [(f["name"], f["count"]) for f in store.folders(1)] == [("Main", 2), ("Alt", 1)]
    assert store.delete_folder(1, main) and not store.delete_folder(1, main)
    assert {h.hash_name: h.folder_id for h in store.holdings(1)} == {"A": None, "B": None, "C": alt}
    for i in range(MAX_FOLDERS - 1):
        store.save_folder(1, f"F{i}")
    assert store.save_folder(1, "One too many") is None


def test_history_of_a_folder_counts_only_its_items(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    long_term = store.save_folder(1, "Long-term")
    store.add_lot(1, "A", 2, 100, folder_id=long_term)
    store.add_lot(1, "B", 3, 100)
    store.set_price("A", 115)
    store.set_price("B", 230)
    assert store.portfolio_history(1, 7)["points"][-1]["value"] == 2 * 1 + 3 * 2
    assert store.portfolio_history(1, 7, folder_id=long_term)["points"][-1]["value"] == 2


def test_api_folders(tmp_path):
    async def scenario(client, store):
        post = lambda path, body: client.post(path, headers=auth(), json=body)  # noqa: E731
        store.add_lot(42, CASE, 1, 100)
        body = await (await post("/api/folders", {"name": "  Long   term "})).json()
        (folder,) = body["folders"]
        assert folder == {"id": folder["id"], "name": "Long term", "count": 0}
        for bad, code in [({"name": ""}, "invalid"), ({"name": "x" * 33}, "invalid"), ({"name": "a\nb"}, "invalid"),
                          ({"name": "Long term"}, "folder_exists"), ({"name": "X", "id": 999}, "gone")]:
            assert (await (await post("/api/folders", bad)).json())["error"] == code, bad
        body = await (await post("/api/holdings/folder", {"hash_names": [CASE], "folder": folder["id"]})).json()
        assert body["items"][0]["folder"] == folder["id"] and body["folders"][0]["count"] == 1
        r = await client.get("/api/portfolio/history", params={"period": "7d", "folder": str(folder["id"])},
                             headers=auth())
        assert r.status == 200
        assert (await client.get("/api/portfolio/history", params={"folder": "x"}, headers=auth())).status == 400
        r = await post("/api/holdings", {"hash_name": "Other", "qty": 1, "buy_price": 1, "mode": "add",
                                         "folder": folder["id"]})
        assert r.status in (200, 404)  # "Other" is unknown to the fake market
        body = await (await post("/api/folders/delete", {"id": folder["id"]})).json()
        assert body["folders"] == [] and body["items"][0]["folder"] is None
        other = await client.post("/api/folders", headers=auth(7), json={"name": "Mine"})
        theirs = (await other.json())["folders"][0]["id"]
        r = await post("/api/holdings/folder", {"hash_names": [CASE], "folder": theirs})
        assert (await r.json())["error"] == "gone"
    run(tmp_path, scenario)


# -- export -----------------------------------------------------------------------

def test_csv_follows_the_language(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    store.save_folder(1, "Main")
    store.add_lot(1, CASE, 3, 1000, folder_id=1)
    store.add_lot(1, "No price paid", 1, None)
    store.set_price(CASE, 1150)
    en = holdings_csv(store, 1, "en").decode("utf-8-sig").splitlines()
    assert en[0].startswith("Item,Market hash name,Folder")
    assert en[1] == f"{CASE},{CASE},Main,3,10.00,11.50,10.00,30.00,0.00,0.0,UAH"
    assert en[2] == "No price paid,No price paid,,1,,,,,,,UAH"
    ru = holdings_csv(store, 1, "ru").decode("utf-8-sig").splitlines()
    assert ru[1] == f"{CASE};{CASE};Main;3;10,00;11,50;10,00;30,00;0,00;0,0;UAH"
    assert holdings_csv(store, 1, "ru").startswith("﻿".encode())
    assert sales_csv(store, 1, "en") is None
    store.sell(1, CASE, 1, 1300, at=0)
    assert sales_csv(store, 1, "uk").decode("utf-8-sig").splitlines()[1] == \
        f"1970-01-01;{CASE};{CASE};1;13,00;10,00;13,00;3,00;UAH"


def test_api_export_sends_files_through_the_bot(tmp_path):
    bot = FakeBot()

    async def scenario(client, store):
        store.add_lot(42, CASE, 1, 100)
        r = await client.post("/api/export", headers=auth(), json={})
        assert (await r.json())["error"] == "no_write_access" and bot.documents == []
        r = await client.post("/api/export", headers=auth(), json={"write_access": True})
        assert (await r.json()) == {"sent": 1}
        store.sell(42, CASE, 1, 150)
        await client.post("/api/export", headers=auth(), json={})
        assert [d[1][:13] for d in bot.documents] == ["cs2-portfolio", "cs2-portfolio", "cs2-sales-202"]
        assert (await client.post("/api/export", headers=auth(), json={})).status == 200
        r = await client.post("/api/export", headers=auth(), json={})
        assert r.status == 429  # three per ten minutes; the refusal above cost nothing
        bot.send_fails = True
        r = await client.post("/api/export", headers=auth(7), json={"write_access": True})
        assert (await r.json())["error"] == "send_failed" and not store.prefs(7)["write_access"]
    run(tmp_path, scenario, bot=bot)


def test_export_without_a_bot(tmp_path):
    async def scenario(client, store):
        r = await client.post("/api/export", headers=auth(), json={"write_access": True})
        assert (await r.json())["error"] == "unavailable"
    run(tmp_path, scenario)


# -- share ------------------------------------------------------------------------

def test_share_story_serves_the_picture_publicly(tmp_path):
    async def scenario(client, store):
        r = await client.post("/api/share?mode=story", headers=auth(), data=JPEG)
        body = await r.json()
        assert body["url"].startswith("https://example.com/share/") and "t.me/cstrackerbot" in body["text"]
        picture = await client.get("/share/" + body["url"].rsplit("/", 1)[1])  # no auth: Telegram fetches it
        assert picture.status == 200 and await picture.read() == JPEG
        assert picture.headers["Content-Type"] == "image/jpeg"
        assert (await client.get("/share/nope.jpg")).status == 404
        assert (await client.post("/api/share?mode=story", headers=auth(), data=b"GIF89a")).status == 400
        assert (await client.post("/api/share?mode=other", headers=auth(), data=JPEG)).status == 400
        assert (await (await client.get("/api/portfolio", headers=auth())).json())["bot"] == "cstrackerbot"
        assert (await client.post("/api/share?mode=story", data=JPEG)).status == 401
    run(tmp_path, scenario, bot=FakeBot())


def test_share_chat_sends_the_picture_directly(tmp_path):
    bot = FakeBot()

    async def scenario(client, store):
        body = await (await client.post("/api/share?mode=chat", headers=auth(), data=JPEG)).json()
        assert body == {"sent": True} and bot.prepared == [] and len(bot.photos) == 1
    run(tmp_path, scenario, bot=bot)


@pytest.mark.parametrize("prepare_fails", [False, True])
def test_share_message_prepares_or_falls_back_to_the_chat(tmp_path, prepare_fails):
    bot = FakeBot(prepare_fails=prepare_fails)

    async def scenario(client, store):
        body = await (await client.post("/api/share?mode=message", headers=auth(), data=JPEG)).json()
        if prepare_fails:
            assert body == {"sent": True} and bot.photos[0][:2] == (42, JPEG)
        else:
            assert body == {"prepared": "prep-1"} and bot.prepared[0][1].endswith(".jpg")
        caption = (bot.photos or bot.prepared)[0][2]
        assert caption == 'My CS2 portfolio. <a href="https://t.me/cstrackerbot">Track yours</a>'
    run(tmp_path, scenario, bot=bot)


def test_share_is_rate_limited(tmp_path):
    async def scenario(client, store):
        codes = [(await client.post("/api/share?mode=story", headers=auth(), data=JPEG)).status for _ in range(11)]
        assert codes == [200] * 10 + [429]
    run(tmp_path, scenario, bot=FakeBot())


def test_icons_are_served_from_our_origin_for_known_items_only(tmp_path):
    fetched = []

    async def fake_fetch(name):
        fetched.append(name)
        if name == "broken":
            raise ValueError("HTTP 404")
        return "image/png", b"\x89PNG" + name.encode()

    market = FakeMarket()
    store, prices = make_service(tmp_path, market)
    store.remember_items([(CASE, CASE, "abc-DEF_1"), ("B", "B", "broken")])
    app = create_app(settings(), store, prices, icon_fetch=fake_fetch)

    async def main():
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            for _ in range(2):
                r = await client.get("/api/icon", params={"name": "abc-DEF_1"}, headers=auth())
                assert r.status == 200 and await r.read() == b"\x89PNGabc-DEF_1"
                assert r.headers["Content-Type"] == "image/png"
            assert fetched == ["abc-DEF_1"]                   # cached
            r = await client.get("/api/icon", params={"name": "https://evil.example/x"}, headers=auth())
            assert r.status == 404 and fetched == ["abc-DEF_1"]  # not an open proxy
            assert (await client.get("/api/icon", params={"name": "broken"}, headers=auth())).status == 502
            assert (await client.get("/api/icon", params={"name": "abc-DEF_1"})).status == 401
        finally:
            await client.close()
    asyncio.run(main())


def test_icon_fetch_reads_the_whole_body_when_it_arrives_in_pieces(monkeypatch):
    from aiohttp import web
    from cs2tracker.app import server
    body = b"\x89PNG" + bytes(range(256)) * 60 + b"IEND"

    async def slow(request):
        resp = web.StreamResponse(headers={"Content-Type": "image/png"})
        await resp.prepare(request)
        for i in range(0, len(body), 4000):
            await resp.write(body[i:i + 4000])
            await asyncio.sleep(0.01)
        return resp

    async def main():
        app = web.Application()
        app.router.add_get("/{icon}/96fx96f", slow)
        test_server = TestServer(app)
        await test_server.start_server()
        try:
            monkeypatch.setattr(server, "ICON_URL", str(test_server.make_url("/")) + "{icon}/96fx96f")
            assert await server.fetch_icon("x") == ("image/png", body)
        finally:
            await test_server.close()
    asyncio.run(main())


class UploadSession:
    def __init__(self, body='{"ok": true, "result": {"id": "p1"}}'):
        self.body = body
        self.posts = []

    def post(self, url, json=None, data=None, timeout=None):
        self.posts.append((url, json, data))
        return FakeResp(200, self.body)


def test_bot_uploads_files_and_prepares_shares():
    session = UploadSession()
    bot = TelegramBot(settings(), session)
    bot.username = "cstrackerbot"
    asyncio.run(bot.send_document(5, "a.csv", b"x,y"))
    url, _, form = session.posts[0]
    assert url.endswith("/sendDocument") and form is not None
    assert asyncio.run(bot.prepare_share(5, "https://example.com/share/k.jpg", "hi")) == "p1"
    _, params, _ = session.posts[1]
    assert params["result"]["photo_url"] == params["result"]["thumbnail_url"]
    assert params["result"]["parse_mode"] == "HTML"
    assert params["result"]["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://t.me/cstrackerbot"
    session.body = '{"ok": false, "description": "Forbidden: bot was blocked by the user"}'
    with pytest.raises(BotApiError, match="sendPhoto: Forbidden"):
        asyncio.run(bot.send_photo(5, JPEG))


# -- broadcast --------------------------------------------------------------------

class Outbox:
    def __init__(self, store=None, cancel_after=None):
        self.sent = []
        self.store = store
        self.cancel_after = cancel_after

    async def __call__(self, user_id, text):
        self.sent.append((user_id, text))
        if self.cancel_after and len(self.sent) == self.cancel_after:
            self.store.finish_broadcast(self.store.running_broadcast()["id"], cancelled=True)
        return user_id != 3  # user 3 blocked the bot


def users(store, *ids):
    for user_id in ids:
        store.set_prefs(user_id, write_access=1)
    store.set_prefs(99, write_access=0)  # never allowed it
    return store


def test_broadcast_goes_to_everyone_once_and_counts(tmp_path):
    store = users(Store(tmp_path / "t.db", "UAH"), 5, 1, 3, 4)
    assert store.start_broadcast(1, "Hello") is not None
    assert store.start_broadcast(1, "Again") is None           # one at a time
    outbox = Outbox()
    job = Broadcaster(store, outbox, allows=lambda u: u != 4, gap=0)
    while asyncio.run(job.step()):
        pass
    assert outbox.sent == [(1, "Hello"), (3, "Hello"), (5, "Hello")]
    last = store.last_broadcast()
    assert (last["total"], last["sent"], last["failed"], last["cancelled"]) == (4, 2, 1, 0)
    assert last["finished_at"] is not None and store.running_broadcast() is None


def test_broadcast_resumes_after_a_restart_and_can_be_cancelled(tmp_path):
    store = users(Store(tmp_path / "t.db", "UAH"), *range(1, 121))
    broadcast = store.start_broadcast(1, "Hi")
    asyncio.run(Broadcaster(store, Outbox(), gap=0).step())      # first batch, then "restart"
    assert store.running_broadcast()["cursor"] == 50
    outbox = Outbox(store, cancel_after=10)
    asyncio.run(Broadcaster(store, outbox, gap=0).step())
    assert [u for u, _ in outbox.sent] == list(range(51, 61))     # carried on, then stopped
    assert store.last_broadcast()["cancelled"] == 1 and store.last_broadcast()["id"] == broadcast


def test_api_broadcast_is_for_admins(tmp_path):
    async def scenario(client, store):
        users(store, 1, 2)
        assert (await client.get("/api/admin/broadcast", headers=auth())).status == 403
        assert (await client.post("/api/admin/broadcast", headers=auth(), json={"text": "x"})).status == 403
        admin = auth(7)
        body = await (await client.get("/api/admin/broadcast", headers=admin)).json()
        assert body == {"audience": 2, "last": None}
        assert (await client.post("/api/admin/broadcast", headers=admin, json={"text": " "})).status == 400
        body = await (await client.post("/api/admin/broadcast", headers=admin, json={"text": " News "})).json()
        assert body["last"]["text"] == "News" and body["last"]["total"] == 2
        r = await client.post("/api/admin/broadcast", headers=admin, json={"text": "More"})
        assert (await r.json())["error"] == "broadcast_running"
        body = await (await client.post("/api/admin/broadcast/cancel", headers=admin, json={})).json()
        assert body["last"]["cancelled"] == 1
    run(tmp_path, scenario, admins=frozenset({7}))


# -- migration --------------------------------------------------------------------

def test_migrates_v8_database(tmp_path):
    path = tmp_path / "t.db"
    store = Store(path, "UAH")
    store.add_lot(1, CASE, 1, 1)
    with store.conn:
        store.conn.execute("ALTER TABLE holdings DROP COLUMN folder_id")
        store.conn.execute("DROP TABLE folders")
        store.conn.execute("DROP TABLE broadcasts")
        store.conn.execute("UPDATE meta SET value = '8' WHERE key = 'schema'")
    store.close()
    store = Store(path, "UAH")
    assert store.conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == str(SCHEMA_VERSION)
    folder = store.save_folder(1, "Main")
    assert store.move_holdings(1, [CASE], folder) == 1 and store.holding(1, CASE).folder_id == folder
    assert sqlite3.connect(path).execute("PRAGMA integrity_check").fetchone()[0] == "ok"
