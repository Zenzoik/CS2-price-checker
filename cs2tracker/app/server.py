"""HTTP side of the Mini App: static front end plus a small JSON API.

Every /api request carries the Mini App's signed init data in
``Authorization: tma <initData>``; the Telegram user id in it scopes all data.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import defaultdict, deque
from pathlib import Path

from aiohttp import web

from ..steam import ItemNotFound, SteamError
from .auth import AuthError, validate_init_data
from .db import MAX_QTY, QuantityLimit, Store
from .prices import PriceService, SteamBusy
from .settings import AppSettings

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
MAX_PRICE = 100_000_000  # in currency units
MAX_NAME = 256
# Steam calls one user may cause per minute (a search counts as 2), and writes.
STEAM_CALLS_PER_MINUTE = 40
WRITES_PER_MINUTE = 60

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

SETTINGS = web.AppKey("settings", AppSettings)
STORE = web.AppKey("store", Store)
PRICES = web.AppKey("prices", PriceService)
LIMITER = web.AppKey("limiter", object)
WRITE_LIMITER = web.AppKey("write_limiter", object)
# RequestKey appeared in aiohttp 3.12; a plain string works everywhere.
USER_ID = web.RequestKey("user_id", int) if hasattr(web, "RequestKey") else "user_id"


class ApiError(web.HTTPException):
    """JSON error; `code` is what the front end translates, `message` is for logs/devs."""

    def __init__(self, status: int, code: str, message: str):
        self.status_code = status
        super().__init__(text=json.dumps({"error": code, "message": message}, ensure_ascii=False),
                         content_type="application/json")


class RateLimiter:
    def __init__(self, per_minute: int, clock=time.monotonic):
        self.per_minute = per_minute
        self.clock = clock
        self.calls: dict[int, deque[float]] = defaultdict(deque)

    def check(self, user_id: int, cost: int = 1) -> None:
        now = self.clock()
        q = self.calls[user_id]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) + cost > self.per_minute:
            raise ApiError(429, "rate", "Too many requests, wait a minute")
        q.extend([now] * cost)
        if len(self.calls) > 10_000:  # forget idle users
            for uid in [u for u, v in self.calls.items() if not v or now - v[-1] > 60]:
                del self.calls[uid]


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
    return await handler(request)


# -- handlers -------------------------------------------------------------------

async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def portfolio(request: web.Request) -> web.Response:
    store = request.app[STORE]
    settings = request.app[SETTINGS]
    holdings = store.holdings(request[USER_ID])
    stamps = [h.price_updated for h in holdings if h.price_updated is not None]
    return web.json_response({
        "currency": store.currency,
        "price_kind": settings.price,
        "updated_at": min(stamps) if stamps else None,
        "items": [_holding_json(h) for h in holdings],
    })


async def search(request: web.Request) -> web.Response:
    query = request.query.get("q", "").strip()
    if not 2 <= len(query) <= 100:
        raise ApiError(400, "invalid", "Type at least 2 characters")
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
    })


async def save_holding(request: web.Request) -> web.Response:
    body = await _json_body(request)
    hash_name = _hash_name(body.get("hash_name"))
    qty = _qty(body.get("qty"))
    buy_cents = _price_cents(body.get("buy_price"))
    mode = body.get("mode")
    user_id = request[USER_ID]
    store = request.app[STORE]
    request.app[WRITE_LIMITER].check(user_id)

    if mode == "set":
        if not store.set_holding(user_id, hash_name, qty, buy_cents):
            raise ApiError(404, "gone", "This item is no longer in your portfolio")
    elif mode == "add":
        if store.holding(user_id, hash_name) is None:
            _check_room(request)
            await _ensure_known(request, hash_name)
            _check_room(request)  # again: other adds may have landed while Steam answered
        try:
            store.add_lot(user_id, hash_name, qty, buy_cents)
        except QuantityLimit as e:
            raise ApiError(400, "too_many", f"At most {MAX_QTY:,} of one item") from e
    else:
        raise ApiError(400, "invalid", "mode must be 'add' or 'set'")
    return await portfolio(request)


async def delete_holding(request: web.Request) -> web.Response:
    body = await _json_body(request)
    request.app[WRITE_LIMITER].check(request[USER_ID])
    request.app[STORE].delete_holding(request[USER_ID], _hash_name(body.get("hash_name")))
    return await portfolio(request)


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


def _price_cents(value) -> int:
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or not 0 <= value <= MAX_PRICE):
        raise ApiError(400, "invalid", "Enter the price you paid for one item")
    return int(round(value * 100))


def _money(cents: int | None) -> float | None:
    return None if cents is None else cents / 100


def _holding_json(h) -> dict:
    return {
        "hash_name": h.hash_name,
        "name": h.name,
        "icon": h.icon,
        "qty": h.qty,
        "buy_price": _money(h.buy_cents),
        "price": _money(h.price_cents),
    }


def create_app(settings: AppSettings, store: Store, prices: PriceService) -> web.Application:
    app = web.Application(middlewares=[security_headers, authenticate], client_max_size=16 * 1024)
    app[SETTINGS] = settings
    app[STORE] = store
    app[PRICES] = prices
    app[LIMITER] = RateLimiter(STEAM_CALLS_PER_MINUTE)
    app[WRITE_LIMITER] = RateLimiter(WRITES_PER_MINUTE)
    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC_DIR)
    app.router.add_get("/api/portfolio", portfolio)
    app.router.add_get("/api/search", search)
    app.router.add_get("/api/quote", quote)
    app.router.add_post("/api/holdings", save_holding)
    app.router.add_post("/api/holdings/delete", delete_holding)
    return app
