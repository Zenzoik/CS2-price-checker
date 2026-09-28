"""HTTP side of the Mini App: static front end plus a small JSON API.

Every /api request carries the Mini App's signed init data in
``Authorization: tma <initData>``; the Telegram user id in it scopes all data.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

from aiohttp import web

from ..steam import ItemNotFound, PrivateInventory, ProfileNotFound, RateLimited, SteamError, parse_profile
from .auth import AuthError, validate_init_data
from .db import DIGESTS, ITEM_METRICS, MAX_QTY, PORTFOLIO_METRICS, QuantityLimit, Store, net_cents
from .notify import current_value, valid_zone, zone
from .prices import InventoryService, PriceService, SteamBusy
from .settings import AppSettings

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
MAX_PRICE = 100_000_000  # in currency units
MAX_NAME = 256
# Steam calls one user may cause per minute (a search counts as 2), and writes.
STEAM_CALLS_PER_MINUTE = 40
WRITES_PER_MINUTE = 60
# Steam allows only a few inventory requests per minute per IP, for all users,
# so the server as a whole stays under that and no single user can use it all.
INVENTORY_LOOKUPS_PER_MINUTE = 3
INVENTORY_LOOKUPS_PER_USER = (3, 300)  # calls per window (seconds)
IMPORT_TTL = 1800
MAX_BULK = 10_000
MAX_BODY = 256 * 1024

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://telegram.org",
    # telegram-web-app.js injects a <style> when running in Telegram's web client.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https://community.fastly.steamstatic.com https://community.akamai.steamstatic.com",
    "connect-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
])

def static_version() -> str:
    """Short hash of the front end, so a deploy changes every URL that loads it."""
    digest = hashlib.sha256()
    for path in sorted(STATIC_DIR.iterdir()):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


SETTINGS = web.AppKey("settings", AppSettings)
VERSION = web.AppKey("version", str)
INDEX_HTML = web.AppKey("index_html", str)
STORE = web.AppKey("store", Store)
PRICES = web.AppKey("prices", PriceService)
LIMITER = web.AppKey("limiter", object)
WRITE_LIMITER = web.AppKey("write_limiter", object)
INVENTORY = web.AppKey("inventory", InventoryService)
INVENTORY_LIMITER = web.AppKey("inventory_limiter", object)
USER_INVENTORY_LIMITER = web.AppKey("user_inventory_limiter", object)
# user id -> (time, steamid, {hash name: qty}) of the last inventory preview
PREVIEWS = web.AppKey("previews", dict)
# user id -> monotonic time we last wrote their last_seen (at most once a minute)
TOUCHED = web.AppKey("touched", dict)
TOUCH_EVERY = 60
# RequestKey appeared in aiohttp 3.12; a plain string works everywhere.
USER_ID = web.RequestKey("user_id", int) if hasattr(web, "RequestKey") else "user_id"


class ApiError(web.HTTPException):
    """JSON error; `code` is what the front end translates, `message` is for logs/devs."""

    def __init__(self, status: int, code: str, message: str):
        self.status_code = status
        super().__init__(text=json.dumps({"error": code, "message": message}, ensure_ascii=False),
                         content_type="application/json")


class RateLimiter:
    """At most `limit` units per `window` seconds per key."""

    def __init__(self, limit: int, window: float = 60, clock=time.monotonic, code: str = "rate"):
        self.limit = limit
        self.window = window
        self.clock = clock
        self.code = code
        self.calls: dict[int, deque[float]] = defaultdict(deque)

    def allows(self, key: int, cost: int = 1) -> bool:
        now = self.clock()
        q = self.calls[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return len(q) + cost <= self.limit

    def take(self, key: int, cost: int = 1) -> None:
        now = self.clock()
        self.calls[key].extend([now] * cost)
        if len(self.calls) > 10_000:  # forget idle keys
            for k in [k for k, v in self.calls.items() if not v or now - v[-1] > self.window]:
                del self.calls[k]

    def check(self, key: int, cost: int = 1) -> None:
        if not self.allows(key, cost):
            raise ApiError(429, self.code, "Too many requests, wait a minute")
        self.take(key, cost)


# -- middlewares ----------------------------------------------------------------

@web.middleware
async def security_headers(request: web.Request, handler):
    try:
        resp = await handler(request)
    except web.HTTPException as e:
        resp = e
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    else:
        # Telegram's WebView caches hard; make it revalidate so updates show up.
        resp.headers.setdefault("Cache-Control", "no-cache")
    if isinstance(resp, web.HTTPException):
        raise resp
    return resp


@web.middleware
async def authenticate(request: web.Request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)
    settings = request.app[SETTINGS]
    header = request.headers.get("Authorization", "")
    scheme, _, init_data = header.partition(" ")
    if scheme.lower() != "tma":
        raise ApiError(401, "auth", "Open the app from Telegram")
    try:
        user = validate_init_data(init_data, settings.bot_token)
    except AuthError as e:
        log.info("Rejected init data: %s", e)
        raise ApiError(401, "auth", "Session expired, reopen the app") from e
    if not settings.allows(user["id"]):
        raise ApiError(403, "private", "This bot is private")
    request[USER_ID] = user["id"]
    _touch(request.app, user)
    return await handler(request)


def _touch(app: web.Application, user: dict) -> None:
    touched = app[TOUCHED]
    now = time.monotonic()
    if now - touched.get(user["id"], -TOUCH_EVERY) < TOUCH_EVERY:
        return
    if len(touched) > 10_000:
        touched.clear()
    touched[user["id"]] = now
    app[STORE].touch_user(user, "app")


def _event(request: web.Request, kind: str, n: int = 1) -> None:
    request.app[STORE].log_event(request[USER_ID], kind, n)


# -- handlers -------------------------------------------------------------------

async def index(request: web.Request) -> web.Response:
    return web.Response(text=request.app[INDEX_HTML], content_type="text/html")


async def portfolio(request: web.Request) -> web.Response:
    if request.query.get("open") == "1":
        _event(request, "open")
    return web.json_response(_portfolio_json(request))


def _portfolio_json(request: web.Request) -> dict:
    store = request.app[STORE]
    settings = request.app[SETTINGS]
    holdings = store.holdings(request[USER_ID])
    stamps = [h.price_updated for h in holdings if h.price_updated is not None]
    return {
        "is_admin": request[USER_ID] in settings.admins,
        "currency": store.currency,
        "version": request.app[VERSION],  # an open app reloads itself when this changes
        "price_kind": settings.price,
        "updated_at": min(stamps) if stamps else None,
        "items": [_holding_json(h) for h in holdings],
        "watching": [_watch_json(w) for w in store.watching(request[USER_ID])],
        "can_notify": bool(store.prefs(request[USER_ID])["write_access"]),
        "offer_digest": store.offer_digest(request[USER_ID]),
        "realized": _realized_json(store.realized(request[USER_ID])),
        "sync": store.pending_sync(request[USER_ID]),
    }


async def portfolio_history(request: web.Request) -> web.Response:
    period = request.query.get("period", "30d")
    if period not in ("7d", "30d", "all"):
        raise ApiError(400, "invalid", "Unknown history period")
    days = {"7d": 7, "30d": 30, "all": 0}[period]
    return web.json_response(request.app[STORE].portfolio_history(request[USER_ID], days))


async def search(request: web.Request) -> web.Response:
    query = request.query.get("q", "").strip()
    if not 2 <= len(query) <= 100:
        raise ApiError(400, "invalid", "Type at least 2 characters")
    _event(request, "search")
    try:
        results = await request.app[PRICES].search(query, _charge(request))
    except SteamBusy as e:
        raise ApiError(503, "busy", "Steam is busy, try again") from e
    except SteamError as e:
        log.warning("Search %r failed: %s", query, e)
        raise ApiError(503, "steam", "Steam is not responding, try again") from e
    held = {h.hash_name for h in request.app[STORE].holdings(request[USER_ID])}
    return web.json_response({"results": [
        {"hash_name": r.hash_name, "name": r.name, "icon": r.icon_url, "held": r.hash_name in held}
        for r in results[:15]
    ]})


async def quote(request: web.Request) -> web.Response:
    hash_name = _hash_name(request.query.get("hash_name"))
    store = request.app[STORE]
    try:
        cents = await request.app[PRICES].quote(hash_name, _charge(request))
    except ItemNotFound as e:
        raise ApiError(404, "not_found", "Item not found on the Steam market") from e
    except SteamError as e:
        # The form still works without a price; fall back to the last known one.
        log.warning("Quote %r failed: %s", hash_name, e)
        cached = store.price(hash_name)
        cents = None if cached is None else cached[0]
    holding = store.holding(request[USER_ID], hash_name)
    return web.json_response({
        "price": _money(cents),
        "holding": _holding_json(holding) if holding else None,
        "liquidity": _liquidity_json(*store.liquidity(hash_name)),
    })


async def save_holding(request: web.Request) -> web.Response:
    body = await _json_body(request)
    hash_name = _hash_name(body.get("hash_name"))
    qty = _qty(body.get("qty"))
    buy_cents = _price_cents(body.get("buy_price"), optional=True)
    mode = body.get("mode")
    user_id = request[USER_ID]
    store = request.app[STORE]
    request.app[WRITE_LIMITER].check(user_id)

    if mode == "set":
        if not store.set_holding(user_id, hash_name, qty, buy_cents):
            raise ApiError(404, "gone", "This item is no longer in your portfolio")
        _event(request, "edit")
    elif mode == "add":
        if store.holding(user_id, hash_name) is None:
            _check_room(request)
            await _ensure_known(request, hash_name)
            _check_room(request)  # again: other adds may have landed while Steam answered
        try:
            store.add_lot(user_id, hash_name, qty, buy_cents)
        except QuantityLimit as e:
            raise ApiError(400, "too_many", f"At most {MAX_QTY:,} of one item") from e
        _event(request, "add")
    else:
        raise ApiError(400, "invalid", "mode must be 'add' or 'set'")
    return await portfolio(request)


async def delete_holding(request: web.Request) -> web.Response:
    """Removes one item ({"hash_name"}) or several at once ({"hash_names": [...]})."""
    body = await _json_body(request)
    request.app[WRITE_LIMITER].check(request[USER_ID])
    names = body.get("hash_names")
    if names is None:
        names = [body.get("hash_name")]
    if not isinstance(names, list) or not 1 <= len(names) <= MAX_BULK:
        raise ApiError(400, "invalid", "Choose items to remove")
    removed = request.app[STORE].delete_holdings(request[USER_ID], list({_hash_name(n) for n in names}))
    if removed:
        _event(request, "remove", removed)
    return await portfolio(request)


async def inventory(request: web.Request) -> web.Response:
    profile = parse_profile(request.query.get("profile", "")[:300])
    if profile is None:
        raise ApiError(400, "not_profile", "Paste a Steam profile or trade link")
    user_id = request[USER_ID]
    limits = [(request.app[INVENTORY_LIMITER], 0),  # shared: the server has one IP
              (request.app[USER_INVENTORY_LIMITER], user_id),
              (request.app[LIMITER], user_id)]

    def charge(cost: int) -> None:
        # Check all first, so a refused request costs nobody anything.
        for limiter, key in limits:
            if not limiter.allows(key, cost):
                raise ApiError(429, limiter.code, "Too many inventory requests, wait a minute")
        for limiter, key in limits:
            limiter.take(key, cost)

    try:
        steamid, items = await request.app[INVENTORY].load(profile, charge)
    except ProfileNotFound as e:
        raise ApiError(404, "profile_not_found", "No Steam profile at this link") from e
    except PrivateInventory as e:
        raise ApiError(403, "inventory_private", "This inventory is private") from e
    except RateLimited as e:
        log.warning("Steam rate-limited an inventory lookup: %s", e)
        raise ApiError(429, "steam_rate", "Steam is limiting inventory requests, try later") from e
    except SteamBusy as e:
        raise ApiError(503, "busy", "Steam is busy, try again") from e
    except SteamError as e:
        log.warning("Inventory %s failed: %s", profile, e)
        raise ApiError(503, "steam", "Steam is not responding, try again") from e

    store = request.app[STORE]
    _event(request, "inventory")
    store.remember_items([(i.hash_name, i.name, i.icon_url) for i in items])
    previews = request.app[PREVIEWS]
    if len(previews) >= 1000:
        previews.pop(next(iter(previews)))
    previews.pop(user_id, None)
    previews[user_id] = (time.monotonic(), steamid, {i.hash_name: i.qty for i in items})
    held = {h.hash_name for h in store.holdings(user_id)}
    return web.json_response({
        "steamid": steamid,
        "room": max(0, request.app[SETTINGS].max_items - len(held)),
        "items": [{"hash_name": i.hash_name, "name": i.name, "icon": i.icon_url, "qty": i.qty,
                   "container": i.container, "held": i.hash_name in held,
                   "price": _money((store.price(i.hash_name) or (None,))[0])} for i in items],
    })


async def import_items(request: web.Request) -> web.Response:
    body = await _json_body(request)
    user_id = request[USER_ID]
    request.app[WRITE_LIMITER].check(user_id)
    preview = request.app[PREVIEWS].get(user_id)
    if (preview is None or time.monotonic() - preview[0] > IMPORT_TTL
            or body.get("steamid") != preview[1]):
        raise ApiError(409, "import_expired", "Open the inventory again")
    mode = body.get("price_mode")
    if mode not in ("none", "market", "manual"):
        raise ApiError(400, "invalid", "price_mode must be none, market or manual")
    chosen = body.get("items")
    if not isinstance(chosen, list) or not chosen or len(chosen) > len(preview[2]):
        raise ApiError(400, "invalid", "Choose items to import")

    store = request.app[STORE]
    prices = request.app[PRICES]
    held = {h.hash_name for h in store.holdings(user_id)}
    rows: dict[str, tuple[str, int, int | None, bool]] = {}
    for entry in chosen:
        if not isinstance(entry, dict):
            raise ApiError(400, "invalid", "Choose items to import")
        name = _hash_name(entry.get("hash_name"))
        if name not in preview[2]:
            raise ApiError(400, "invalid", "Item is not in this inventory")
        if name in held:
            continue
        buy = None
        if mode == "manual":
            buy = _price_cents(entry.get("buy_price"), optional=True)
        elif mode == "market":
            fresh = prices.fresh_price(name)
            buy = net_cents(fresh[0]) if fresh and fresh[0] is not None else None
        rows[name] = (name, preview[2][name], buy, mode == "market")
    if len(held) + len(rows) > request.app[SETTINGS].max_items:
        raise ApiError(400, "full", "Portfolio is full")
    added = store.import_items(user_id, list(rows.values()))
    # Remembered for the daily check: what is in this inventory now isn't "new" later.
    store.remember_inventory(user_id, preview[1], preview[2])
    if added:
        _event(request, "import", added)
    prices.wake()
    return await portfolio(request)


async def sell(request: web.Request) -> web.Response:
    """{"hash_name", "qty", "price"}: `price` is what one item brought, after fees."""
    body = await _json_body(request)
    hash_name = _hash_name(body.get("hash_name"))
    qty = _qty(body.get("qty"))
    price_cents = _price_cents(body.get("price"))
    user_id = request[USER_ID]
    store = request.app[STORE]
    request.app[WRITE_LIMITER].check(user_id)
    if store.sell(user_id, hash_name, qty, price_cents) is None:
        if store.holding(user_id, hash_name) is None:
            raise ApiError(404, "gone", "This item is no longer in your portfolio")
        raise ApiError(400, "sell_qty", "You don't hold that many")
    _event(request, "sell")
    return await portfolio(request)


async def list_sales(request: web.Request) -> web.Response:
    store = request.app[STORE]
    return web.json_response({"sales": [_sale_json(s) for s in store.sales(request[USER_ID])],
                              "realized": _realized_json(store.realized(request[USER_ID]))})


async def undo_sale(request: web.Request) -> web.Response:
    """{"id"}: deletes a sale and returns its items to the portfolio at the price paid."""
    body = await _json_body(request)
    sale_id = _row_id(body.get("id"), "Unknown sale")
    user_id = request[USER_ID]
    store = request.app[STORE]
    request.app[WRITE_LIMITER].check(user_id)
    sale = store.sale(user_id, sale_id)
    if sale is None:
        raise ApiError(404, "gone", "This sale no longer exists")
    if store.holding(user_id, sale["hash_name"]) is None:
        _check_room(request)
    try:
        store.undo_sale(user_id, sale_id)
    except QuantityLimit as e:
        raise ApiError(400, "too_many", f"At most {MAX_QTY:,} of one item") from e
    return web.json_response({"sales": [_sale_json(s) for s in store.sales(user_id)],
                              "portfolio": _portfolio_json(request)})


async def dismiss_sync(request: web.Request) -> web.Response:
    """Hides what the inventory check found until it finds something again."""
    request.app[WRITE_LIMITER].check(request[USER_ID])
    request.app[STORE].dismiss_sync(request[USER_ID])
    return await portfolio(request)


async def watch(request: web.Request) -> web.Response:
    """{"hash_name", "on": true | false}: follow an item's price without owning it."""
    body = await _json_body(request)
    hash_name = _hash_name(body.get("hash_name"))
    store = request.app[STORE]
    request.app[WRITE_LIMITER].check(request[USER_ID])
    if body.get("on") is True:
        await _ensure_known(request, hash_name)
        if not store.watch(request[USER_ID], hash_name):
            raise ApiError(400, "watch_full", "The watchlist is full")
        request.app[PRICES].wake()  # its price is wanted soon
        _event(request, "watch")
    elif body.get("on") is False:
        store.unwatch(request[USER_ID], hash_name)
    else:
        raise ApiError(400, "invalid", "on must be true or false")
    return await portfolio(request)


async def list_alerts(request: web.Request) -> web.Response:
    return web.json_response({"alerts": [_alert_json(a) for a in request.app[STORE].alerts(request[USER_ID])]})


async def save_alert(request: web.Request) -> web.Response:
    """Creates ({hash_name | null, metric, above, threshold}) or, with "id", edits an alert."""
    body = await _json_body(request)
    user_id = request[USER_ID]
    store = request.app[STORE]
    request.app[WRITE_LIMITER].check(user_id)
    alert_id = None if body.get("id") is None else _row_id(body.get("id"), "Unknown alert")
    hash_name = None if body.get("hash_name") is None else _hash_name(body.get("hash_name"))
    metric = body.get("metric")
    above = body.get("above")
    if metric not in (PORTFOLIO_METRICS if hash_name is None else ITEM_METRICS) or not isinstance(above, bool):
        raise ApiError(400, "invalid", "Unknown alert type")
    threshold = _alert_threshold(metric, above, body.get("threshold"))
    if metric == "profit":
        holding = store.holding(user_id, hash_name)
        if holding is None or not holding.buy_cents:
            raise ApiError(400, "no_buy_price", "Enter the price you paid first")
    elif hash_name is not None:
        await _ensure_known(request, hash_name)
    saved = store.save_alert(user_id, hash_name, metric, above, threshold, alert_id=alert_id)
    if saved is None:
        if alert_id is not None:
            raise ApiError(404, "gone", "This alert no longer exists")
        raise ApiError(400, "alerts_full", "Too many alerts")
    if body.get("write_access") is True:
        store.set_prefs(user_id, write_access=1)
    if alert_id is None:
        _event(request, "alert")
    request.app[PRICES].wake()  # an alert on an item nobody tracked yet needs its price
    return await list_alerts(request)


async def delete_alert(request: web.Request) -> web.Response:
    body = await _json_body(request)
    request.app[WRITE_LIMITER].check(request[USER_ID])
    request.app[STORE].delete_alert(request[USER_ID], _row_id(body.get("id"), "Unknown alert"))
    return await list_alerts(request)


async def get_prefs(request: web.Request) -> web.Response:
    store = request.app[STORE]
    prefs = store.prefs(request[USER_ID])
    return web.json_response({"digest": prefs["digest"], "digest_hour": prefs["digest_hour"],
                              "tz": prefs["tz"], "can_notify": bool(prefs["write_access"]),
                              "sync": store.sync_settings(request[USER_ID])})


async def save_prefs(request: web.Request) -> web.Response:
    """Any of {digest, digest_hour, tz, digest_offered, write_access, sync_enabled}."""
    body = await _json_body(request)
    user_id = request[USER_ID]
    store = request.app[STORE]
    request.app[WRITE_LIMITER].check(user_id)
    changes: dict = {}
    if "digest" in body:
        if body["digest"] not in DIGESTS:
            raise ApiError(400, "invalid", "digest must be off, daily or weekly")
        changes["digest"] = body["digest"]
        changes["digest_offered"] = 1
    if "digest_hour" in body:
        hour = body["digest_hour"]
        if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
            raise ApiError(400, "invalid", "digest_hour must be 0-23")
        changes["digest_hour"] = hour
    if "tz" in body:
        if not isinstance(body["tz"], str) or not valid_zone(body["tz"]):
            raise ApiError(400, "invalid", "Unknown time zone")
        changes["tz"] = body["tz"]
    for key in ("digest_offered", "write_access"):
        if key in body:
            if not isinstance(body[key], bool):
                raise ApiError(400, "invalid", f"{key} must be true or false")
            changes[key] = int(body[key])
    if "sync_enabled" in body:
        if not isinstance(body["sync_enabled"], bool):
            raise ApiError(400, "invalid", "sync_enabled must be true or false")
        if not store.set_sync_enabled(user_id, body["sync_enabled"]):
            raise ApiError(400, "no_sync", "Import your inventory first")
    if {"digest", "digest_hour", "tz"} & changes.keys():
        # A slot that already passed today waits for tomorrow, not "right now".
        # Never clear it: a digest already sent today must not come twice.
        prefs = store.prefs(user_id) | changes
        local = datetime.now(zone(prefs["tz"]))
        today = local.date().isoformat()
        if prefs["digest_sent"] != today and local.hour >= prefs["digest_hour"]:
            changes["digest_sent"] = today
    store.set_prefs(user_id, **changes)
    if changes.get("digest") in ("daily", "weekly"):
        _event(request, "digest")
    return await get_prefs(request)


async def admin_stats(request: web.Request) -> web.Response:
    if request[USER_ID] not in request.app[SETTINGS].admins:
        raise ApiError(403, "forbidden", "Admins only")
    return web.json_response(request.app[STORE].stats())


async def _ensure_known(request: web.Request, hash_name: str) -> None:
    """Only items Steam knows may be added, so the refresh never chases typos."""
    store = request.app[STORE]
    if store.item(hash_name) is not None or store.price(hash_name) is not None:
        return
    try:
        await request.app[PRICES].quote(hash_name, _charge(request))
    except ItemNotFound as e:
        raise ApiError(404, "not_found", "Item not found on the Steam market") from e
    except SteamBusy as e:
        raise ApiError(503, "busy", "Steam is busy, try again") from e
    except SteamError as e:
        raise ApiError(503, "steam", "Steam is not responding, try again") from e


def _check_room(request: web.Request) -> None:
    if request.app[STORE].count(request[USER_ID]) >= request.app[SETTINGS].max_items:
        raise ApiError(400, "full", "Portfolio is full")


def _charge(request: web.Request):
    """Bills the user's Steam budget, only when Steam is actually asked."""
    limiter, user_id = request.app[LIMITER], request[USER_ID]
    return lambda cost: limiter.check(user_id, cost)


# -- validation -----------------------------------------------------------------

async def _json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except ValueError as e:
        raise ApiError(400, "invalid", "Invalid JSON") from e
    if not isinstance(body, dict):
        raise ApiError(400, "invalid", "Invalid JSON")
    return body


def _hash_name(value) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_NAME:
        raise ApiError(400, "invalid", "Unknown item")
    return value.strip()


def _qty(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_QTY:
        raise ApiError(400, "invalid", f"Quantity must be a whole number from 1 to {MAX_QTY:,}")
    return value


def _price_cents(value, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    value = _number(value)
    if value is None or not 0 <= value <= MAX_PRICE:
        raise ApiError(400, "invalid", "Enter the price you paid for one item")
    return int(round(value * 100))


def _money(cents: int | None) -> float | None:
    return None if cents is None else cents / 100


def _liquidity_json(bid: int | None, ask: int | None,
                    buy_orders: int | None, sell_listings: int | None) -> dict | None:
    if bid is None and ask is None and buy_orders is None and sell_listings is None:
        return None
    spread = (2 * (ask - bid) / (ask + bid)
              if bid is not None and ask is not None and 0 < bid <= ask else None)
    return {"buy_orders": buy_orders, "sell_listings": sell_listings, "spread": spread}


def _number(value) -> float | None:
    """A JSON number as a float; None for anything else, including huge integers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, int):
        return float(value) if abs(value) <= 10**15 else None
    return value if math.isfinite(value) else None


def _row_id(value, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 2**63:
        raise ApiError(400, "invalid", message)
    return value


def _alert_threshold(metric: str, above: bool, raw) -> int:
    """Validates a threshold from the app (money or a ratio) into cents / basis points.

    Checked after rounding: a value that rounds to zero would fire on no move at all.
    """
    value = _number(raw)
    if value is None:
        raise ApiError(400, "invalid", "Enter a number")
    if metric == "price":
        cents = int(round(value * 100)) if 0 < value <= MAX_PRICE else 0
        if cents < 1:
            raise ApiError(400, "invalid", "Enter a price above zero")
        return cents
    if metric == "profit":
        if not -1 < value <= 100:
            raise ApiError(400, "invalid", "Enter a change from -100 % to +10000 %")
        return int(round(value * 10000))
    # Portfolio alerts are a move from now: up means above zero, down below.
    if metric == "value_pct":
        if not -1 < value <= 100:
            raise ApiError(400, "invalid", "Enter a change from -100 % to +10000 %")
        scaled = int(round(value * 10000))
    else:
        if abs(value) > MAX_PRICE:
            raise ApiError(400, "invalid", "Enter a smaller amount")
        scaled = int(round(value * 100))
    if scaled == 0 or (scaled > 0) != above:
        raise ApiError(400, "invalid", "Enter a move up or down")
    return scaled


def _alert_json(a: dict) -> dict:
    money_metric = a["metric"] in ("price", "value_amount")
    scale = (lambda v: None if v is None else v / 100) if money_metric else (lambda v: None if v is None else v / 10000)
    return {
        "id": a["id"], "hash_name": a["hash_name"], "name": a["name"], "icon": a["icon"],
        "metric": a["metric"], "above": bool(a["above"]), "threshold": scale(a["threshold"]),
        "current": scale(current_value(a)), "armed": bool(a["armed"]),
        "fired_at": a["fired_at"], "created_at": a["created_at"], "baseline": _money(a["baseline"]),
        "price": _money(a["price_cents"]),
    }


def _sale_json(s: dict) -> dict:
    return {
        "id": s["id"], "hash_name": s["hash_name"], "name": s["name"], "icon": s["icon"], "qty": s["qty"],
        "price": _money(s["price_cents"]), "buy_price": _money(s["buy_cents"]), "sold_at": s["sold_at"],
        "profit": None if s["buy_cents"] is None else _money(s["qty"] * (s["price_cents"] - s["buy_cents"])),
    }


def _realized_json(r: dict) -> dict:
    return {"count": r["count"], "proceeds": _money(r["proceeds"]), "cost": _money(r["cost"]),
            "profit": _money(r["profit"]), "unknown": r["unknown"]}


def _watch_json(w: dict) -> dict:
    return {
        "hash_name": w["hash_name"], "name": w["name"], "icon": w["icon"],
        "price": _money(w["price_cents"]), "pending": w["price_checked"] is None,
        "change_24h": (w["price_cents"] / w["yesterday_cents"] - 1
                       if w["price_cents"] is not None and w["yesterday_cents"] else None),
    }


def _holding_json(h) -> dict:
    return {
        "hash_name": h.hash_name,
        "name": h.name,
        "icon": h.icon,
        "qty": h.qty,
        "buy_price": _money(h.buy_cents),
        "price": _money(h.price_cents),
        "pending": h.price_checked is None,
        "change_24h": (h.price_cents / h.yesterday_cents - 1
                       if h.price_cents is not None and h.yesterday_cents else None),
        "change_7d": (h.price_cents / h.week_ago_cents - 1
                      if h.price_cents is not None and h.week_ago_cents else None),
        "liquidity": _liquidity_json(h.buy_order_cents, h.sell_order_cents,
                                     h.buy_orders, h.sell_listings),
    }


def create_app(settings: AppSettings, store: Store, prices: PriceService,
               inventories: InventoryService | None = None) -> web.Application:
    app = web.Application(middlewares=[security_headers, authenticate], client_max_size=MAX_BODY)
    app[SETTINGS] = settings
    # Versioned asset URLs: Telegram's WebView caches hard, a new URL cannot be stale.
    app[VERSION] = version = static_version()
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    app[INDEX_HTML] = (html.replace("/static/app.js", f"/static/app.js?v={version}")
                           .replace("/static/style.css", f"/static/style.css?v={version}")
                           .replace("{{version}}", version))
    app[STORE] = store
    app[PRICES] = prices
    app[LIMITER] = RateLimiter(STEAM_CALLS_PER_MINUTE)
    app[WRITE_LIMITER] = RateLimiter(WRITES_PER_MINUTE)
    app[INVENTORY] = inventories or InventoryService(prices.market)
    app[INVENTORY_LIMITER] = RateLimiter(INVENTORY_LOOKUPS_PER_MINUTE, code="inventory_busy")
    app[USER_INVENTORY_LIMITER] = RateLimiter(*INVENTORY_LOOKUPS_PER_USER)
    app[PREVIEWS] = {}
    app[TOUCHED] = {}
    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC_DIR)
    app.router.add_get("/api/portfolio", portfolio)
    app.router.add_get("/api/portfolio/history", portfolio_history)
    app.router.add_get("/api/search", search)
    app.router.add_get("/api/quote", quote)
    app.router.add_post("/api/holdings", save_holding)
    app.router.add_post("/api/holdings/delete", delete_holding)
    app.router.add_get("/api/inventory", inventory)
    app.router.add_post("/api/import", import_items)
    app.router.add_get("/api/admin/stats", admin_stats)
    app.router.add_post("/api/watch", watch)
    app.router.add_post("/api/sales", sell)
    app.router.add_get("/api/sales", list_sales)
    app.router.add_post("/api/sales/delete", undo_sale)
    app.router.add_post("/api/sync/dismiss", dismiss_sync)
    app.router.add_get("/api/alerts", list_alerts)
    app.router.add_post("/api/alerts", save_alert)
    app.router.add_post("/api/alerts/delete", delete_alert)
    app.router.add_get("/api/prefs", get_prefs)
    app.router.add_post("/api/prefs", save_prefs)
    return app
