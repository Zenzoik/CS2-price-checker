"""Recent Steam profiles: the ones a user opened an inventory from, to open again in one tap."""

import asyncio

from cs2tracker.app.db import MAX_RECENT_PROFILES, Store
from cs2tracker.app.prices import InventoryService
from cs2tracker.app.server import RateLimiter
from cs2tracker.app.sync import NAME_RETRY, InventorySync
from cs2tracker.steam import ProfileInfo, ProfileNotFound, SteamMarket, parse_profile_xml

from fakes import FakeResponse, FakeSession
from test_app import STEAMID, FakeMarket, auth, inv_item, run_import

OTHER = "76561198000000001"
XML = ("<profile><steamID64>76561197960287930</steamID64><steamID><![CDATA[Rabscuttle]]></steamID>"
       "<avatarMedium><![CDATA[https://avatars.akamai.steamstatic.com/"
       "c5d56249ee5d28a07db4ac9f7f60af961fab5426_medium.jpg]]></avatarMedium></profile>")


def test_profile_xml_gives_the_name_and_a_steam_avatar_only():
    info = parse_profile_xml(XML)
    assert info == ProfileInfo("76561197960287930", "Rabscuttle",
                               "https://avatars.akamai.steamstatic.com/c5d56249ee5d28a07db4ac9f7f60af961fab5426_medium.jpg")
    assert parse_profile_xml(XML.replace("akamai.steamstatic.com", "evil.example")).avatar is None
    assert parse_profile_xml("<response><error>The specified profile could not be found.</error></response>") is None


def test_steam_profile_by_custom_url_or_steamid():
    session = FakeSession({"/id/": [FakeResponse(200, None)], "/profiles/": [FakeResponse(200, None)]})
    for r in session.routes.values():
        r[0].text = XML
    market = SteamMarket(session, request_delay=0, sleep=lambda s: None)
    assert market.profile("vanity", "gabelogannewell").name == "Rabscuttle"
    assert market.resolve_vanity("gabelogannewell") == "76561197960287930"
    assert market.profile("steamid", "76561197960287930").steamid == "76561197960287930"
    assert [c[0].split("steamcommunity.com")[1] for c in session.calls] == [
        "/id/gabelogannewell/", "/id/gabelogannewell/", "/profiles/76561197960287930/"]
    for kind, value in (("vanity", "no/slash"), ("steamid", "123")):
        try:
            market.profile(kind, value)
        except ProfileNotFound:
            pass
        else:
            raise AssertionError(kind)


def test_recent_profiles_are_newest_first_capped_and_keep_what_is_known(tmp_path):
    store = Store(tmp_path / "t.db", "UAH")
    store.remember_profile(1, STEAMID, 64, name="Ann", vanity="ann", avatar="a.jpg", at=10)
    store.remember_profile(1, OTHER, 3, at=20)
    store.remember_profile(1, STEAMID, 65, at=30)  # a trade link this time: no name, but one is known
    assert [(p["steamid"], p["name"], p["vanity"], p["items"]) for p in store.recent_profiles(1)] == [
        (STEAMID, "Ann", "ann", 65), (OTHER, None, None, 3)]
    assert store.recent_profiles(2) == []
    for i in range(MAX_RECENT_PROFILES + 3):
        store.remember_profile(1, f"76561198{i:09d}", 1, at=100 + i)
    profiles = store.recent_profiles(1)
    assert len(profiles) == MAX_RECENT_PROFILES and profiles[0]["steamid"] == f"76561198{MAX_RECENT_PROFILES + 2:09d}"
    assert store.forget_profile(1, profiles[0]["steamid"]) and len(store.recent_profiles(1)) == MAX_RECENT_PROFILES - 1


def test_opening_an_inventory_makes_it_a_recent_profile(tmp_path):
    async def scenario(client, store, market):
        r = await client.get("/api/inventory", params={"profile": "https://steamcommunity.com/id/ann"}, headers=auth())
        assert r.status == 200
        # A trade link: no extra Steam request just for the name (imports need the budget).
        link = "https://steamcommunity.com/tradeoffer/new/?partner=1&token=x"
        assert (await client.get("/api/inventory", params={"profile": link}, headers=auth())).status == 200
        assert [c for c in market.calls if c[0] == "steamid"] == []
        profiles = (await (await client.get("/api/profiles", headers=auth())).json())["profiles"]
        assert [(p["steamid"], p["name"], p["vanity"], p["items"]) for p in profiles] == [
            ("76561197960265729", None, None, 2), (STEAMID, "Ann", "ann", 2)]
        assert (await client.get("/api/profiles", headers=auth(7))).status == 200  # someone else: their own list
        assert (await (await client.get("/api/profiles", headers=auth(7))).json())["profiles"] == []
        r = await client.post("/api/profiles/delete", headers=auth(), json={"steamid": STEAMID})
        assert [p["steamid"] for p in (await r.json())["profiles"]] == ["76561197960265729"]
        r = await client.post("/api/profiles/delete", headers=auth(), json={"steamid": "../../x"})
        assert r.status == 400
        # A private inventory isn't remembered.
        market.inventory_items = ProfileNotFound(OTHER)
        await client.get("/api/inventory", params={"profile": OTHER}, headers=auth())
        assert [p["steamid"] for p in store.recent_profiles(42)] == ["76561197960265729"]
    run_import(tmp_path, scenario, [inv_item("A"), inv_item("B")])


def namer(tmp_path, limiter=None):
    market = FakeMarket()
    store = Store(tmp_path / "t.db", "UAH")
    clock = [1_000_000.0]

    async def send(user_id, text):
        return True
    job = InventorySync(store, InventoryService(market, clock=lambda: clock[0]),
                        limiter or RateLimiter(3, code="inventory_busy"), send, clock=lambda: clock[0])
    return store, market, job, clock


def test_names_come_later_from_spare_inventory_budget(tmp_path):
    store, market, job, clock = namer(tmp_path)
    store.remember_profile(1, OTHER, 5, at=clock[0])
    store.remember_profile(2, OTHER, 5, at=clock[0])
    assert asyncio.run(job.name_next())
    assert [p["name"] for p in store.recent_profiles(1) + store.recent_profiles(2)] == ["Ann", "Ann"]
    assert not asyncio.run(job.name_next())  # nothing left to name
    assert market.calls == [("steamid", OTHER)]


def test_naming_waits_for_spare_budget_and_retries_failures_a_day_later(tmp_path):
    busy = RateLimiter(3, code="inventory_busy")
    busy.take(0, 2)  # an import is going on: only one request left, which stays for users
    store, market, job, clock = namer(tmp_path, busy)
    store.remember_profile(1, OTHER, 5, at=clock[0])
    assert not asyncio.run(job.name_next()) and market.calls == []

    (tmp_path / "b").mkdir()
    store, market, job, clock = namer(tmp_path / "b")
    market.profile = lambda kind, value: (_ for _ in ()).throw(ProfileNotFound(value))
    store.remember_profile(1, OTHER, 5, at=clock[0])
    assert asyncio.run(job.name_next())
    assert not asyncio.run(job.name_next())  # not again right away
    clock[0] += NAME_RETRY + 1
    job.inventories._profiles.clear()  # the service's own short error cache has long expired by then
    assert asyncio.run(job.name_next())
