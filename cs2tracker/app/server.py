"""HTTP side of the Mini App: static front end plus a small JSON API.

Every /api request carries the Mini App's signed init data in
``Authorization: tma <initData>``; the Telegram user id in it scopes all data.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import math
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

from ..steam import ItemNotFound, PrivateInventory, ProfileNotFound, RateLimited, SteamError, parse_profile
from .auth import AuthError, validate_init_data
from .db import (
    DIGESTS, ITEM_METRICS, MAX_FOLDER_NAME, MAX_QTY, PORTFOLIO_METRICS, QuantityLimit, Store, net_cents,
)
from .catalog import Catalog
from .export import holdings_csv, sales_csv
from .friends import VIEWS, display_name, summary
from .friends import texts as friend_texts
from .inline import Cards
from .notify import current_value, language, valid_zone, zone
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
# Exports and share pictures go out through the bot: a few per user per window.
EXPORTS_PER_USER = (3, 600)
SHARES_PER_USER = (10, 600)
# Share pictures live in memory, long enough for Telegram to fetch them.
SHARE_TTL = 86400
MAX_SHARES = 200
# A user's older pictures make room for their newer ones, not for someone else's.
SHARES_KEPT_PER_USER = 3
MAX_BROADCAST = 4000
# Item pictures for the share card, fetched here: Steam's CDN sends no CORS
# header, so the app can't draw them on a canvas it then exports.
ICON_URL = "https://community.fastly.steamstatic.com/economy/image/{icon}/{size}fx{size}f"
ICONS_PER_MINUTE = 60
MAX_ICON = 256 * 1024
MAX_ICONS_CACHED = 500
# Story captions are plain text, so they spell the link out; message captions
# (HTML) hide it behind the words.
SHARE_TEXTS = {
    "en": "My CS2 portfolio. Track yours: {link}",
    "ru": "Мой портфель CS2. Следите за своим: {link}",
    "uk": "Мій портфель CS2. Стежте за своїм: {link}",
}
SHARE_CAPTIONS = {
    "en": 'My CS2 portfolio. <a href="{link}">Track yours</a>',
    "ru": 'Мой портфель CS2. <a href="{link}">Следите за своим</a>',
    "uk": 'Мій портфель CS2. <a href="{link}">Стежте за своїм</a>',
}

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://telegram.org",
    # telegram-web-app.js injects a <style> when running in Telegram's web client.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https://community.fastly.steamstatic.com https://community.akamai.steamstatic.com "
    "https://avatars.akamai.steamstatic.com https://avatars.fastly.steamstatic.com "
    "https://avatars.cloudflare.steamstatic.com https://avatars.steamstatic.com",
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
# the bot, for files and pictures sent to users (None in tests without one)
BOT = web.AppKey("bot", object)
EXPORT_LIMITER = web.AppKey("export_limiter", object)
SHARE_LIMITER = web.AppKey("share_limiter", object)
# picture id -> (time, JPEG bytes, user id, thumbnail JPEG or None)
SHARES = web.AppKey("shares", dict)
ICON_FETCH = web.AppKey("icon_fetch", object)
ICON_LIMITER = web.AppKey("icon_limiter", object)
# icon -> (content type, bytes)
ICONS = web.AppKey("icons", dict)
# inline mode's price cards (see inline.py)
CARDS = web.AppKey("cards", Cards)
# every market item, searchable in memory (see catalog.py)
CATALOG = web.AppKey("catalog", Catalog)
# user id -> monotonic time we last wrote their last_seen (at most once a minute)
TOUCHED = web.AppKey("touched", dict)
TOUCH_EVERY = 60
# messages to users sent in the background (kept so they aren't garbage-collected mid-way)
TASKS = web.AppKey("tasks", set)
INVITE_CODE = re.compile(r"[A-Za-z0-9_-]{8,32}")
# (user id, view, detail) -> (monotonic time, summary): friends' results, reused for a
# minute, since a list of 100 friends is 100 portfolios to add up on the event loop.
SUMMARIES = web.AppKey("summaries", dict)
SUMMARY_TTL = 60
# (inviter, new friend) -> monotonic time the inviter was told: once a day per pair,
# so accepting, removing and accepting again can't be used to spam someone.
TOLD = web.AppKey("told", dict)
TELL_EVERY = 86400
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
    except Exception:
        # Left to aiohttp, a bug would come out as a bare text 500 without these headers.
        log.exception("Unhandled error on %s %s", request.method, request.path)
        resp = (ApiError(500, "generic", "Internal error") if request.path.startswith("/api/")
                else web.HTTPInternalServerError())
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
        "folders": store.folders(request[USER_ID]),
        "bot": getattr(request.app[BOT], "username", None),  # for the share card
        "inline": bool(getattr(request.app[BOT], "supports_inline", False)),  # "@bot item" works
    }


async def portfolio_history(request: web.Request) -> web.Response:
    period = request.query.get("period", "30d")
    if period not in ("7d", "30d", "all"):
        raise ApiError(400, "invalid", "Unknown history period")
    days = {"7d": 7, "30d": 30, "all": 0}[period]
    folder = request.query.get("folder")
    folder_id = None
    if folder:
        # isdigit() would pass "²", which int() refuses.
        folder_id = _row_id(int(folder) if re.fullmatch(r"[0-9]{1,19}", folder) else None, "Unknown folder")
    return web.json_response(request.app[STORE].portfolio_history(request[USER_ID], days, folder_id=folder_id))


async def search(request: web.Request) -> web.Response:
    query = request.query.get("q", "").strip()
    if not 2 <= len(query) <= 100:
        raise ApiError(400, "invalid", "Type at least 2 characters")
    _event(request, "search")
    store = request.app[STORE]
    held = {h.hash_name for h in store.holdings(request[USER_ID])}
    # The catalogue answers at once; Steam's search (often 429 here) only for names it lacks.
    names = request.app[CATALOG].search(query, 15)
    if names:
        found = [(n, *(store.item(n) or (n, None))) for n in names]
    else:
        try:
            results = await request.app[PRICES].search(query, _charge(request))
        except SteamBusy as e:
            raise ApiError(503, "busy", "Steam is busy, try again") from e
        except SteamError as e:
            log.warning("Search %r failed: %s", query, e)
            raise ApiError(503, "steam", "Steam is not responding, try again") from e
        request.app[CATALOG].remember([r.hash_name for r in results])
        found = [(r.hash_name, r.name, r.icon_url) for r in results[:15]]
    return web.json_response({"results": [
        {"hash_name": n, "name": name, "icon": icon, "held": n in held} for n, name, icon in found
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
    name, icon = store.item(hash_name) or (hash_name, None)
    return web.json_response({
        "name": name, "icon": icon,  # for an item opened by a link, with nothing else known
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
            store.add_lot(user_id, hash_name, qty, buy_cents, _folder_id(body.get("folder")))
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
    info = request.app[INVENTORY].known_profile(steamid)  # a custom URL's lookup brought its name
    store.remember_profile(user_id, steamid, len(items), name=info.name if info else None,
                           vanity=profile[1] if profile[0] == "vanity" else None,
                           avatar=info.avatar if info else None)
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


async def recent_profiles(request: web.Request) -> web.Response:
    return web.json_response({"profiles": request.app[STORE].recent_profiles(request[USER_ID])})


async def forget_profile(request: web.Request) -> web.Response:
    body = await _json_body(request)
    steamid = body.get("steamid")
    if not isinstance(steamid, str) or not re.fullmatch(r"7656119\d{10}", steamid):
        raise ApiError(400, "invalid", "Unknown profile")
    request.app[WRITE_LIMITER].check(request[USER_ID])
    request.app[STORE].forget_profile(request[USER_ID], steamid)
    return await recent_profiles(request)


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
    added = store.import_items(user_id, list(rows.values()), _folder_id(body.get("folder")))
    # Remembered for the daily check: what is in this inventory now isn't "new" later.
    store.remember_inventory(user_id, preview[1], preview[2])
    if added:
        _event(request, "import", added)
    prices.wake()
    return await portfolio(request)


async def save_folder(request: web.Request) -> web.Response:
    """{"name"} creates a folder, {"id", "name"} renames one."""
    body = await _json_body(request)
    user_id = request[USER_ID]
    request.app[WRITE_LIMITER].check(user_id)
    name = body.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= MAX_FOLDER_NAME \
            or any(ord(c) < 32 for c in name):
        raise ApiError(400, "invalid", f"A folder name has 1 to {MAX_FOLDER_NAME} characters")
    folder_id = None if body.get("id") is None else _row_id(body.get("id"), "Unknown folder")
    store = request.app[STORE]
    if store.save_folder(user_id, " ".join(name.split()), folder_id) is None:
        folders = store.folders(user_id)
        if folder_id is not None and all(f["id"] != folder_id for f in folders):
            raise ApiError(404, "gone", "This folder no longer exists")
        if any(f["name"] == " ".join(name.split()) for f in folders):
            raise ApiError(400, "folder_exists", "There is a folder with this name")
        raise ApiError(400, "folders_full", "Too many folders")
    return await portfolio(request)


async def delete_folder(request: web.Request) -> web.Response:
    body = await _json_body(request)
    request.app[WRITE_LIMITER].check(request[USER_ID])
    request.app[STORE].delete_folder(request[USER_ID], _row_id(body.get("id"), "Unknown folder"))
    return await portfolio(request)


async def move_holdings(request: web.Request) -> web.Response:
    """{"hash_names": [...], "folder": id | null}."""
    body = await _json_body(request)
    request.app[WRITE_LIMITER].check(request[USER_ID])
    names = body.get("hash_names")
    if not isinstance(names, list) or not 1 <= len(names) <= MAX_BULK:
        raise ApiError(400, "invalid", "Choose items to move")
    folder = body.get("folder")
    folder_id = None if folder is None else _row_id(folder, "Unknown folder")
    if request.app[STORE].move_holdings(request[USER_ID], list({_hash_name(n) for n in names}), folder_id) is None:
        raise ApiError(404, "gone", "This folder no longer exists")
    return await portfolio(request)


async def export(request: web.Request) -> web.Response:
    """Sends the positions (and sales, if any) as CSV files to the user's chat with the bot."""
    body = await _json_body(request)
    user_id = request[USER_ID]
    store = request.app[STORE]
    bot = request.app[BOT]
    if bot is None:
        raise ApiError(503, "unavailable", "The bot is not running")
    if body.get("write_access") is True:
        store.set_prefs(user_id, write_access=1)
    if not store.prefs(user_id)["write_access"]:
        raise ApiError(400, "no_write_access", "Allow the bot to message you first")
    request.app[EXPORT_LIMITER].check(user_id)
    lang = store.user_language(user_id)
    day = time.strftime("%Y-%m-%d")
    files = [(f"cs2-portfolio-{day}.csv", holdings_csv(store, user_id, lang))]
    sales = sales_csv(store, user_id, lang)
    if sales is not None:
        files.append((f"cs2-sales-{day}.csv", sales))
    try:
        for name, data in files:
            await bot.send_document(user_id, name, data)
    except Exception as e:
        log.warning("Export to %s failed: %s", user_id, e)
        if "forbidden" in str(e).lower():
            store.set_prefs(user_id, write_access=0)
        raise ApiError(502, "send_failed", "The bot could not send the file") from e
    _event(request, "export")
    return web.json_response({"sent": len(files)})


async def share(request: web.Request) -> web.Response:
    """Takes the share card (a JPEG the app drew) and makes it shareable.

    ?mode=story: a public URL for shareToStory. mode=message: a prepared
    message for shareMessage, or, when Telegram refuses that, the picture sent
    to the user's chat to forward (then "sent" is true). mode=chat: that
    picture straight away, for clients without shareMessage.

    The body is the JPEG, or a form with "photo" and a small "thumb" (see
    `_share_thumbnail`).
    """
    mode = request.query.get("mode")
    if mode not in ("story", "message", "chat"):
        raise ApiError(400, "invalid", "mode must be story, message or chat")
    user_id = request[USER_ID]
    data, thumb = await _share_upload(request)
    # Served publicly from our domain, and a cut-off file would reach the chat
    # with a grey band: each must be a whole JPEG.
    size = _jpeg_size(data)
    if size is None or (thumb is not None and _jpeg_size(thumb) is None):
        raise ApiError(400, "invalid", "Send a JPEG picture")
    request.app[SHARE_LIMITER].check(user_id)
    shares = request.app[SHARES]
    now = time.monotonic()
    for key in [k for k, (at, *_) in shares.items() if now - at > SHARE_TTL]:
        del shares[key]
    mine = [k for k, entry in shares.items() if entry[2] == user_id]
    for key in mine[:max(0, len(mine) - SHARES_KEPT_PER_USER + 1)]:
        del shares[key]
    while len(shares) >= MAX_SHARES:
        shares.pop(next(iter(shares)))
    key = secrets.token_urlsafe(18)
    shares[key] = (now, data, user_id, thumb)
    base = f"{request.app[SETTINGS].public_url.rstrip('/')}/share/{key}"
    url = f"{base}.jpg"
    _event(request, "share")
    if mode == "story":
        return web.json_response({"url": url, "text": _share_text(request)})
    bot = request.app[BOT]
    if bot is None:
        raise ApiError(503, "unavailable", "The bot is not running")
    if mode == "message":
        try:
            # Without its size the client guesses a square and crops the wide card.
            return web.json_response({"prepared": await bot.prepare_share(
                user_id, url, _share_text(request, True), size=size,
                thumb_url=f"{base}.thumb.jpg" if thumb is not None else None)})
        except Exception as e:  # e.g. an older Bot API or inline sharing disabled
            log.info("Prepared share for %s failed, sending the picture instead: %s", user_id, e)
    try:
        await bot.send_photo(user_id, data, _share_text(request, True))
    except Exception as e:
        log.warning("Share picture to %s failed: %s", user_id, e)
        raise ApiError(502, "send_failed", "The bot could not send the picture") from e
    return web.json_response({"sent": True})


async def _share_upload(request: web.Request) -> tuple[bytes, bytes | None]:
    """(photo, thumbnail or None) from a raw JPEG body or a form with "photo" and "thumb".

    Why a thumbnail of its own: Telegram for iOS draws the sender's copy of a
    shared photo from `thumbnail_url` alone, and when the send is confirmed it
    moves that download, finished or not, into the real photo; the rest then
    comes from Telegram's re-encoded file and decodes as a grey band. A small
    thumbnail is complete long before the user picks a chat.
    """
    if request.content_type != "multipart/form-data":
        return await request.read(), None
    form = await request.post()  # bounded by client_max_size, like read()

    def field(name: str) -> bytes | None:
        value = form.get(name)
        return value.file.read() if isinstance(value, web.FileField) else None
    return field("photo") or b"", field("thumb")


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) of a complete JPEG, or None for anything else.

    Walks the marker segments up to the frame header; the file must also end
    with the end-of-image marker, so an upload cut short is refused.
    """
    if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        return None
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        length = int.from_bytes(data[i + 2:i + 4], "big")
        if length < 2:
            return None
        # SOF0-SOF15, except DHT (C4), JPG (C8) and DAC (CC), carry the frame size.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 9 > len(data):
                return None
            height = int.from_bytes(data[i + 5:i + 7], "big")
            width = int.from_bytes(data[i + 7:i + 9], "big")
            return (width, height) if width and height else None
        if marker == 0xDA:  # image data before any frame header
            return None
        i += 2 + length
    return None


async def icon(request: web.Request) -> web.Response:
    """An item's picture from Steam, served from our origin. Only icons of known items."""
    name = request.query.get("name", "")
    if not 0 < len(name) <= 2048 or not request.app[STORE].known_icon(name):
        raise ApiError(404, "not_found", "Unknown picture")
    cache = request.app[ICONS]
    hit = cache.get(name)
    if hit is None:
        request.app[ICON_LIMITER].check(request[USER_ID])
        try:
            hit = await request.app[ICON_FETCH](name)
        except Exception as e:
            log.info("Icon fetch failed: %s", e)
            raise ApiError(502, "steam", "Steam is not responding") from e
        if len(cache) >= MAX_ICONS_CACHED:
            cache.pop(next(iter(cache)))
        cache[name] = hit
    # Private: the request is authenticated, so no shared cache may keep it.
    return web.Response(body=hit[1], content_type=hit[0], headers={"Cache-Control": "private, max-age=86400"})


async def fetch_icon(name: str, size: int = 96) -> tuple[str, bytes]:
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(ICON_URL.format(icon=name, size=size)) as resp:
            kind = resp.headers.get("Content-Type", "").split(";")[0]
            if resp.status != 200 or kind not in ("image/png", "image/jpeg", "image/webp"):
                raise ValueError(f"HTTP {resp.status} {kind}")
            # content.read(n) returns what has arrived so far, not the whole body.
            data = bytearray()
            async for chunk in resp.content.iter_chunked(65536):
                data += chunk
                if len(data) > MAX_ICON:
                    raise ValueError("too big")
            return kind, bytes(data)


def _share_text(request: web.Request, as_html: bool = False) -> str:
    """The caption: plain text for stories, HTML with a text link for messages."""
    bot = request.app[BOT]
    username = getattr(bot, "username", None)
    link = f"https://t.me/{username}" if username else request.app[SETTINGS].public_url
    lang = language(request.app[STORE].user_language(request[USER_ID]))
    if as_html:
        return SHARE_CAPTIONS[lang].format(link=html.escape(link, quote=True))
    return SHARE_TEXTS[lang].format(link=link)


async def shared_picture(request: web.Request) -> web.Response:
    """Public: Telegram's servers fetch it. The name is an unguessable token."""
    name = request.match_info["name"]
    thumb = name.endswith(".thumb.jpg")  # tokens never contain a dot
    hit = request.app[SHARES].get(name.removesuffix(".jpg").removesuffix(".thumb"))
    if not name.endswith(".jpg") or hit is None or time.monotonic() - hit[0] > SHARE_TTL:
        raise web.HTTPNotFound()
    body = hit[3] if thumb else hit[1]
    if body is None:
        raise web.HTTPNotFound()
    return web.Response(body=body, content_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


async def admin_broadcast(request: web.Request) -> web.Response:
    _admin_only(request)
    store = request.app[STORE]
    return web.json_response({"audience": store.broadcast_audience(), "last": store.last_broadcast()})


async def start_broadcast(request: web.Request) -> web.Response:
    """{"text"}: goes to everyone the bot may write to, paced by the broadcast job."""
    _admin_only(request)
    body = await _json_body(request)
    text = body.get("text")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_BROADCAST:
        raise ApiError(400, "invalid", f"Write 1 to {MAX_BROADCAST} characters")
    if request.app[STORE].start_broadcast(request[USER_ID], text.strip()) is None:
        raise ApiError(409, "broadcast_running", "A broadcast is still going out")
    log.info("Admin %s started a broadcast", request[USER_ID])
    return await admin_broadcast(request)


async def cancel_broadcast(request: web.Request) -> web.Response:
    _admin_only(request)
    running = request.app[STORE].running_broadcast()
    if running is not None:
        request.app[STORE].finish_broadcast(running["id"], cancelled=True)
    return await admin_broadcast(request)


def _admin_only(request: web.Request) -> None:
    if request[USER_ID] not in request.app[SETTINGS].admins:
        raise ApiError(403, "forbidden", "Admins only")


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
                              "friends_view": prefs["friends_view"],
                              "sync": store.sync_settings(request[USER_ID])})


async def save_prefs(request: web.Request) -> web.Response:
    """Any of {digest, digest_hour, tz, digest_offered, write_access, friends_view, sync_enabled}."""
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
    if "friends_view" in body:
        if body["friends_view"] not in VIEWS:
            raise ApiError(400, "invalid", "friends_view must be percent or full")
        changes["friends_view"] = body["friends_view"]
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


# -- friends ----------------------------------------------------------------------
# Friends see each other's results (friends.py says what); a friendship starts from
# an invite link, t.me/<bot>?start=fr_<code>, which the bot answers with the app.

def _invite_link(request: web.Request, renew: bool = False) -> str | None:
    bot = getattr(request.app[BOT], "username", None)
    if not bot:
        return None
    return f"https://t.me/{bot}?start=fr_{request.app[STORE].invite_code(request[USER_ID], renew)}"


def _invite_owner(request: web.Request, code) -> int:
    owner = (request.app[STORE].invite_owner(code)
             if isinstance(code, str) and INVITE_CODE.fullmatch(code) else None)
    if owner is None:
        raise ApiError(404, "invite_invalid", "This invite link is no longer valid")
    return owner


async def _summaries(app: web.Application, keys: list[tuple[int, str, bool]]) -> list[dict]:
    """summary() for each (user id, view, detail), from the minute's cache or else worked
    out in a thread on a read-only connection: a list of friends is a month of history
    each, seconds' work that must not hold up everyone else's requests."""
    cache, now = app[SUMMARIES], time.monotonic()
    missing = [key for key in dict.fromkeys(keys) if not (key in cache and now - cache[key][0] < SUMMARY_TTL)]
    if missing:
        def work() -> list[dict]:
            with app[STORE].reader() as store:
                return [summary(store, user_id, view=view, detail=detail) for user_id, view, detail in missing]
        results = await asyncio.to_thread(work)
        if len(cache) > 5000:
            cache.clear()
        for key, result in zip(missing, results):
            cache[key] = (now, result)
    return [cache[key][1] for key in keys]


async def friends_list(request: web.Request) -> web.Response:
    """Your own results next to your friends' (in what each of them chose to show)."""
    store, user_id = request.app[STORE], request[USER_ID]
    lang = store.user_language(user_id)
    view = store.prefs(user_id)["friends_view"]
    rows = store.friends(user_id)
    results = await _summaries(request.app, [(f["user_id"], f["view"], False) for f in rows])
    friends = [{"id": f["user_id"], "name": display_name(f, lang), "since": f["since"], **result}
               for f, result in zip(rows, results)]
    return web.json_response({
        "link": _invite_link(request),
        "view": view,
        # You as your friends see you, so both sides rank you the same.
        "me": summary(store, user_id, view=view),
        "friends": friends,
    })


async def friend_detail(request: web.Request) -> web.Response:
    raw = request.match_info["id"]
    friend_id = _row_id(int(raw) if re.fullmatch(r"[0-9]{1,19}", raw) else None, "Unknown friend")
    store = request.app[STORE]
    if not store.are_friends(request[USER_ID], friend_id):
        raise ApiError(404, "not_friend", "Not in your friends")
    return web.json_response({
        "id": friend_id, "name": display_name(store.user_names(friend_id), store.user_language(request[USER_ID])),
        **(await _summaries(request.app, [(friend_id, store.prefs(friend_id)["friends_view"], True)]))[0],
    })


async def invite_info(request: web.Request) -> web.Response:
    """Whose link this is, before the user says yes to it."""
    request.app[WRITE_LIMITER].check(request[USER_ID])  # no cheap guessing of codes
    owner = _invite_owner(request, request.query.get("code"))
    store, user_id = request.app[STORE], request[USER_ID]
    status = "self" if owner == user_id else "friend" if store.are_friends(user_id, owner) else "new"
    return web.json_response({"id": owner, "status": status, "name": display_name(
        store.user_names(owner), store.user_language(user_id), handle=status == "new")})


async def accept_invite(request: web.Request) -> web.Response:
    body = await _json_body(request)
    user_id = request[USER_ID]
    request.app[WRITE_LIMITER].check(user_id)
    owner = _invite_owner(request, body.get("code"))
    result = request.app[STORE].add_friend(user_id, owner)
    if result == "self":
        raise ApiError(400, "own_invite", "This is your own invite link")
    if result == "full":
        raise ApiError(400, "friends_full", "Too many friends")
    if result == "added":
        _event(request, "friend")
        _tell_new_friend(request.app, owner, user_id)
    return web.json_response({"id": owner, "status": result})


def _tell_new_friend(app: web.Application, owner: int, friend_id: int) -> None:
    """The inviter hears that their link worked, if the bot may write to them."""
    bot, store, told, now = app[BOT], app[STORE], app[TOLD], time.monotonic()
    if bot is None or not store.prefs(owner)["write_access"] or now - told.get((owner, friend_id), -TELL_EVERY) < TELL_EVERY:
        return
    if len(told) > 10_000:
        told.clear()
    told[(owner, friend_id)] = now
    lang = store.user_language(owner)
    text = friend_texts(lang)["accepted"].format(name=display_name(store.user_names(friend_id), lang))

    async def send() -> None:
        try:
            await bot.message_user(owner, text)
        except Exception as e:  # the friendship stands either way
            log.warning("Could not tell %s about a new friend: %s", owner, e)
    task = asyncio.ensure_future(send())
    app[TASKS].add(task)
    task.add_done_callback(app[TASKS].discard)


async def remove_friend(request: web.Request) -> web.Response:
    """Ends it for both, and retires the remover's link: the other side may still have it."""
    body = await _json_body(request)
    store, user_id = request.app[STORE], request[USER_ID]
    request.app[WRITE_LIMITER].check(user_id)
    removed = store.remove_friend(user_id, _row_id(body.get("id"), "Unknown friend"))
    if removed and store.invite_owner_code(user_id) is not None:
        store.invite_code(user_id, renew=True)
    return web.json_response({"removed": removed, "link": _invite_link(request) if removed else None})


async def renew_invite(request: web.Request) -> web.Response:
    """A new link; whoever has the old one can no longer use it (friends stay friends)."""
    request.app[WRITE_LIMITER].check(request[USER_ID])
    link = _invite_link(request, renew=True)
    if link is None:
        raise ApiError(503, "unavailable", "The bot is not running")
    return web.json_response({"link": link})


async def admin_stats(request: web.Request) -> web.Response:
    _admin_only(request)
    # Aggregates over months of events: off the event loop, which serves everyone else.
    return web.json_response(await asyncio.to_thread(request.app[STORE].stats_readonly))


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


def _folder_id(value) -> int | None:
    """An optional folder for new positions; one that isn't the user's is ignored by the store."""
    return None if value is None else _row_id(value, "Unknown folder")


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
        # A folder's summary says how old its own prices are. "Failing": the last
        # check got no price (delisted, or Steam erred), so the old one is kept.
        "price_at": h.price_updated,
        "price_failing": (h.price_checked is not None and h.price_updated is not None
                          and h.price_checked > h.price_updated),
        "change_24h": (h.price_cents / h.yesterday_cents - 1
                       if h.price_cents is not None and h.yesterday_cents else None),
        "change_7d": (h.price_cents / h.week_ago_cents - 1
                      if h.price_cents is not None and h.week_ago_cents else None),
        "liquidity": _liquidity_json(h.buy_order_cents, h.sell_order_cents,
                                     h.buy_orders, h.sell_listings),
        "folder": h.folder_id,
    }


def create_app(settings: AppSettings, store: Store, prices: PriceService,
               inventories: InventoryService | None = None, bot=None, icon_fetch=None,
               catalog: Catalog | None = None) -> web.Application:
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
    app[TASKS] = set()
    app[SUMMARIES] = {}
    app[TOLD] = {}

    async def cancel_tasks(app: web.Application) -> None:
        for task in list(app[TASKS]):
            task.cancel()
    app.on_cleanup.append(cancel_tasks)
    app[BOT] = bot
    app[EXPORT_LIMITER] = RateLimiter(*EXPORTS_PER_USER)
    app[SHARE_LIMITER] = RateLimiter(*SHARES_PER_USER)
    app[SHARES] = {}
    app[ICON_FETCH] = icon_fetch or fetch_icon
    app[ICON_LIMITER] = RateLimiter(ICONS_PER_MINUTE)
    app[ICONS] = {}
    app[CATALOG] = catalog if catalog is not None else Catalog(store)
    app[CARDS] = Cards(settings, store, prices, icon_fetch=app[ICON_FETCH], bot=bot, catalog=app[CATALOG])
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
    app.router.add_get("/api/profiles", recent_profiles)
    app.router.add_post("/api/profiles/delete", forget_profile)
    app.router.add_get("/api/admin/stats", admin_stats)
    app.router.add_post("/api/watch", watch)
    app.router.add_post("/api/sales", sell)
    app.router.add_get("/api/sales", list_sales)
    app.router.add_post("/api/sales/delete", undo_sale)
    app.router.add_post("/api/sync/dismiss", dismiss_sync)
    app.router.add_post("/api/folders", save_folder)
    app.router.add_post("/api/folders/delete", delete_folder)
    app.router.add_post("/api/holdings/folder", move_holdings)
    app.router.add_post("/api/export", export)
    app.router.add_post("/api/share", share)
    app.router.add_get("/share/{name}", shared_picture)
    app.router.add_get("/card/{kind}/{name}", app[CARDS].handle)
    app.router.add_get("/api/icon", icon)
    app.router.add_get("/api/admin/broadcast", admin_broadcast)
    app.router.add_post("/api/admin/broadcast", start_broadcast)
    app.router.add_post("/api/admin/broadcast/cancel", cancel_broadcast)
    app.router.add_get("/api/alerts", list_alerts)
    app.router.add_post("/api/alerts", save_alert)
    app.router.add_post("/api/alerts/delete", delete_alert)
    app.router.add_get("/api/prefs", get_prefs)
    app.router.add_get("/api/friends", friends_list)
    app.router.add_get("/api/friends/invite", invite_info)
    app.router.add_post("/api/friends/accept", accept_invite)
    app.router.add_post("/api/friends/delete", remove_friend)
    app.router.add_post("/api/friends/link", renew_invite)
    app.router.add_get("/api/friends/{id}", friend_detail)
    app.router.add_post("/api/prefs", save_prefs)
    return app
