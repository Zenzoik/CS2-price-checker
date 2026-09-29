"""Inline mode: "@bot kilowatt" in any chat lists items with their price, and
sends the chosen one as a message with a price card.

Results are articles, not photos: the list then shows the name, price and
moves as text, and the card arrives as the message's large link preview (a
JPEG drawn here, see `cards.py`). That also avoids Telegram for iOS drawing the
sender's copy of a photo result from its thumbnail (see `server._share_upload`).

Typing must feel instant, so a query first searches the items the service
already knows; Steam's search runs only for the rest, within the user's Steam
budget and a few seconds. Prices come from the cache the refresh keeps; a card
for an item nobody tracks asks Steam when Telegram fetches it.

Card URLs are signed, so only this bot's results make the server draw.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import logging
import time
from collections import OrderedDict
from urllib.parse import quote, urlencode

from aiohttp import web

from ..steam import SteamError
from .db import Store, net_cents
from .notify import language, money, percent
from .prices import PriceService

try:  # the cards need Pillow; without it results still work, only without the picture
    from . import cards as render
except ImportError:  # pragma: no cover - depends on the install
    render = None

log = logging.getLogger(__name__)

MARKET_URL = "https://steamcommunity.com/market/listings/730/"
ICON_URL = "https://community.fastly.steamstatic.com/economy/image/{icon}/96fx96f"
MAX_RESULTS = 20
STEAM_SEARCH_WAIT = 4.0     # an inline answer is waited for; better fewer results than none
CARD_QUOTE_WAIT = 6.0       # Telegram fetches the card when the message is sent
CACHE_TIME = 30             # seconds Telegram may reuse an answer: prices move, keep it short
# Steam requests inline mode may make per minute, all users together: the price
# refresh and the app's own users come first.
INLINE_STEAM_PER_MINUTE = 20
PREFETCH = 2                # uncached prices fetched in the background per query
STALE = 3 * 3600            # older prices on a card are asked for again
RENDERED_CACHE = 200
ICONS_CACHE = 300
LOG_EVERY = 600             # one "inline" usage event per user per 10 minutes

TEXTS = {
    "en": {
        "day": "24h", "week": "7d", "price_later": "price on the card", "no_price": "no listings",
        "you_have": "You have {n}", "for_sale": "For sale: {n}", "track": "📈 Track the price",
        "steam": "On Steam", "open_app": "My portfolio", "portfolio": "My CS2 portfolio",
        "portfolio_desc": "{ratio} profit · {n} items · no amounts", "portfolio_items": "{n} items · no amounts",
        "profit": "Profit", "popular": "Popular", "open_item": "Open in the tracker",
        "item_intro": "{name}\n\nOpen it in the tracker to follow the price or add it to your portfolio.",
    },
    "ru": {
        "day": "24 ч", "week": "7 д", "price_later": "цена на карточке", "no_price": "нет предложений",
        "you_have": "У вас {n} шт.", "for_sale": "В продаже: {n}", "track": "📈 Следить за ценой",
        "steam": "В Steam", "open_app": "Мой портфель", "portfolio": "Мой портфель CS2",
        "portfolio_desc": "прибыль {ratio} · предметов: {n} · без сумм", "portfolio_items": "предметов: {n} · без сумм",
        "profit": "Прибыль", "popular": "Популярное", "open_item": "Открыть в трекере",
        "item_intro": "{name}\n\nОткройте в трекере, чтобы следить за ценой или добавить в портфель.",
    },
    "uk": {
        "day": "24 год", "week": "7 д", "price_later": "ціна на картці", "no_price": "немає пропозицій",
        "you_have": "У вас {n} шт.", "for_sale": "У продажу: {n}", "track": "📈 Стежити за ціною",
        "steam": "У Steam", "open_app": "Мій портфель", "portfolio": "Мій портфель CS2",
        "portfolio_desc": "прибуток {ratio} · предметів: {n} · без сум", "portfolio_items": "предметів: {n} · без сум",
        "profit": "Прибуток", "popular": "Популярне", "open_item": "Відкрити в трекері",
        "item_intro": "{name}\n\nВідкрийте в трекері, щоб стежити за ціною або додати до портфеля.",
    },
}


def item_key(hash_name: str) -> str:
    """A short, stable, URL- and deep-link-safe name for an item (16 chars of [A-Za-z0-9_-])."""
    return base64.urlsafe_b64encode(hashlib.sha1(hash_name.encode()).digest()).decode()[:16]


class NoBudget(Exception):
    """Inline mode may not ask Steam right now."""


class Budget:
    """At most `limit` Steam calls per minute for inline mode as a whole."""

    def __init__(self, limit: int = INLINE_STEAM_PER_MINUTE, clock=time.monotonic):
        self.limit, self.clock, self.calls = limit, clock, []

    def charge(self, cost: int) -> None:
        now = self.clock()
        self.calls = [t for t in self.calls if now - t < 60]
        if len(self.calls) + cost > self.limit:
            raise NoBudget
        self.calls += [now] * cost


class Cards:
    """Signed card URLs, and the pictures behind them."""

    def __init__(self, settings, store: Store, prices: PriceService, icon_fetch, bot=None, budget: Budget | None = None):
        self.settings = settings
        self.store = store
        self.prices = prices
        self.icon_fetch = icon_fetch  # (icon, size) -> (content type, bytes)
        self.bot = bot
        self.budget = budget or Budget()
        self._secret = hashlib.sha256(b"cs2tracker cards\0" + settings.bot_token.encode()).digest()
        self._keys: dict[str, str] = {}
        self._rendered: OrderedDict[str, bytes] = OrderedDict()
        self._icons: OrderedDict[tuple[str, int], bytes | None] = OrderedDict()
        self._drawing = asyncio.Semaphore(2)

    @property
    def enabled(self) -> bool:
        return render is not None

    # -- URLs ------------------------------------------------------------------

    def _sign(self, text: str) -> str:
        return hmac.new(self._secret, text.encode(), hashlib.sha256).hexdigest()[:20]

    def _url(self, kind: str, ident: str, lang: str, version: str) -> str:
        query = f"l={lang}&v={version}"
        signed = f"{query}&s={self._sign(f'{kind}/{ident}?{query}')}"
        return f"{self.settings.public_url.rstrip('/')}/card/{kind}/{ident}.jpg?{signed}"

    def item_url(self, hash_name: str, lang: str, cents: int | None) -> str:
        """Changes when the price does, and hourly: Telegram keeps a preview per URL."""
        self._keys[item_key(hash_name)] = hash_name
        return self._url("i", item_key(hash_name), lang, f"{cents or 0}-{int(time.time() // 3600)}")

    def portfolio_url(self, user_id: int, lang: str, version: str) -> str:
        return self._url("p", str(user_id), lang, f"{version}-{int(time.time() // 3600)}")

    def resolve(self, key: str) -> str | None:
        """The item behind an `item_key`, also after a restart."""
        name = self._keys.get(key)
        if name is None:
            self._keys.update({item_key(n): n for n in self.store.known_names()})
            name = self._keys.get(key)
        return name

    # -- pictures --------------------------------------------------------------

    async def handle(self, request: web.Request) -> web.Response:
        """GET /card/{kind}/{ident}.jpg?l=&v=&s= (public: Telegram fetches it)."""
        kind, name = request.match_info["kind"], request.match_info["name"]
        lang, version, sig = (request.query.get(k, "") for k in ("l", "v", "s"))
        if not name.endswith(".jpg") or kind not in ("i", "p") or lang not in TEXTS or not self.enabled:
            raise web.HTTPNotFound()
        ident = name.removesuffix(".jpg")
        if not hmac.compare_digest(sig, self._sign(f"{kind}/{ident}?l={lang}&v={version}")):
            raise web.HTTPNotFound()
        cache_key = f"{kind}/{ident}?{lang}&{version}"
        data = self._rendered.get(cache_key)
        if data is None:
            data = await (self._item_jpeg(ident, lang) if kind == "i" else self._portfolio_jpeg(int(ident), lang))
            if data is None:
                raise web.HTTPNotFound()
            self._rendered[cache_key] = data
            while len(self._rendered) > RENDERED_CACHE:
                self._rendered.popitem(last=False)
        return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})

    async def _icon(self, icon: str | None, size: int) -> bytes | None:
        if not icon:
            return None
        key = (icon, size)
        if key not in self._icons:
            try:
                self._icons[key] = (await self.icon_fetch(icon, size=size))[1]
            except Exception as e:  # the card is drawn without it
                log.info("Card icon fetch failed: %s", e)
                self._icons[key] = None
            while len(self._icons) > ICONS_CACHE:
                self._icons.popitem(last=False)
        return self._icons[key]

    async def _item_jpeg(self, key: str, lang: str) -> bytes | None:
        hash_name = self.resolve(key)
        if hash_name is None:
            return None
        snap = self.store.market_snapshot(hash_name)
        if snap["cents"] is None or snap["updated_at"] is None or time.time() - snap["updated_at"] > STALE:
            # Nobody tracks it (or not lately): ask Steam now, once, within budget.
            try:
                await asyncio.wait_for(self.prices.quote(hash_name, self.budget.charge), CARD_QUOTE_WAIT)
                snap = self.store.market_snapshot(hash_name)
            except (NoBudget, SteamError, asyncio.TimeoutError) as e:
                log.info("Card for %s drawn with the cached price: %r", hash_name, e)
        icon = await self._icon(snap["icon"], 360)
        stamp = snap["updated_at"]
        day = time.gmtime(stamp)[:3] if stamp else None
        card = render.ItemCard(
            name=snap["name"], currency=self.store.currency, kind=self.settings.price, cents=snap["cents"],
            net_cents=None if snap["cents"] is None else net_cents(snap["cents"]),
            changes=[("day", snap["change_24h"]), ("week", snap["change_7d"]), ("month", snap["change_30d"])],
            series=snap["closes"], listings=snap["sell_listings"], orders=snap["buy_orders"], icon=icon, day=day,
        )
        async with self._drawing:
            return await asyncio.to_thread(render.item_card, card, lang, self._bot_name())

    async def _portfolio_jpeg(self, user_id: int, lang: str) -> bytes | None:
        summary = portfolio_summary(self.store, user_id)
        if summary is None:
            return None
        icons = await asyncio.gather(*(self._icon(h.icon, 96) for h in summary["top"]))
        card = render.PortfolioCard(
            ratio=summary["ratio"], month=summary["month"], count=summary["count"],
            top=[(h.name, _item_ratio(h), icon) for h, icon in zip(summary["top"], icons)],
        )
        async with self._drawing:
            return await asyncio.to_thread(render.portfolio_card, card, lang, self._bot_name())

    def _bot_name(self) -> str | None:
        return getattr(self.bot, "username", None)


def _item_ratio(h) -> float | None:
    """Profit on the price paid, else the day's move (as the app's share card)."""
    if h.price_cents is not None and h.buy_cents:
        return net_cents(h.price_cents) / h.buy_cents - 1
    if h.price_cents is not None and h.yesterday_cents:
        return h.price_cents / h.yesterday_cents - 1
    return None


def portfolio_summary(store: Store, user_id: int) -> dict | None:
    """What the portfolio card shows: percent only, never amounts. None without priced holdings."""
    holdings = [h for h in store.holdings(user_id) if h.price_cents is not None]
    if not holdings:
        return None
    known = [h for h in holdings if h.buy_cents is not None]
    cost = sum(h.buy_cents * h.qty for h in known)
    worth = sum(net_cents(h.price_cents) * h.qty for h in known)
    top = sorted(holdings, key=lambda h: net_cents(h.price_cents) * h.qty, reverse=True)[:4]
    return {"ratio": worth / cost - 1 if cost > 0 else None, "count": len(store.holdings(user_id)),
            "month": store.portfolio_history(user_id, 30)["change_ratio"], "top": top}


class InlineMode:
    def __init__(self, settings, store: Store, prices: PriceService, cards: Cards, bot, limiter=None):
        self.settings = settings
        self.store = store
        self.prices = prices
        self.cards = cards
        self.bot = bot
        self.limiter = limiter  # the app's per-user Steam limiter, shared with inline mode
        self._logged: dict[int, float] = {}
        self._background: set[asyncio.Task] = set()

    # -- answering -------------------------------------------------------------

    async def answer(self, query: dict) -> None:
        user = query.get("from") or {}
        user_id = user.get("id")
        if not isinstance(user_id, int):
            return
        lang = language(user.get("language_code"))
        if not self.settings.allows(user_id):
            await self.bot.call("answerInlineQuery", inline_query_id=query["id"], results=[], cache_time=300,
                                is_personal=True)
            return
        self._usage(user)
        text = " ".join(str(query.get("query") or "").split())[:100]
        results = await self.results(user_id, text, lang)
        await self.bot.call(
            "answerInlineQuery", inline_query_id=query["id"], results=results, cache_time=CACHE_TIME,
            is_personal=True, button={"text": TEXTS[lang]["open_app"], "web_app": {"url": self.settings.public_url}},
        )

    def _usage(self, user: dict) -> None:
        now = time.monotonic()
        if now - self._logged.get(user["id"], -LOG_EVERY) < LOG_EVERY:
            return
        if len(self._logged) > 10_000:
            self._logged.clear()
        self._logged[user["id"]] = now
        self.store.touch_user(user, "inline")
        self.store.log_event(user["id"], "inline")

    async def results(self, user_id: int, text: str, lang: str) -> list[dict]:
        held = {h.hash_name: h for h in self.store.holdings(user_id)}
        out: list[dict] = []
        if not text:
            # Nothing typed yet: what the user has, to show off in one tap.
            portfolio = self._portfolio_result(user_id, lang)
            if portfolio:
                out.append(portfolio)
            by_value = sorted(held.values(), key=lambda h: (h.price_cents or 0) * h.qty, reverse=True)
            names = [h.hash_name for h in by_value] + [w["hash_name"] for w in self.store.watching(user_id)]
            if not names:
                names = self.store.popular_items(10)
        else:
            names = await self._search(user_id, text)
        shown = list(dict.fromkeys(names))[:MAX_RESULTS - len(out)]
        out += [self._item_result(name, held.get(name), lang) for name in shown]
        self._prefetch(user_id, shown[:6])
        return out

    async def _search(self, user_id: int, text: str) -> list[str]:
        local = self.store.search_items(text.split(), MAX_RESULTS)
        if len(text) < 3 or len(local) >= 10:
            return local
        try:
            found = await asyncio.wait_for(self.prices.search(text, self._charge(user_id)), STEAM_SEARCH_WAIT)
        except (NoBudget, SteamError, asyncio.TimeoutError, web.HTTPException) as e:
            log.info("Inline search %r without Steam: %r", text, e)
            return local
        # Catalogue hits have every typed word; Steam adds the names we haven't seen.
        return list(dict.fromkeys(local + [r.hash_name for r in found]))

    def _charge(self, user_id: int):
        """Bills both the user's Steam budget (shared with the app) and inline mode's."""
        def charge(cost: int) -> None:
            if self.limiter is not None and not self.limiter.allows(user_id, cost):
                raise NoBudget
            self.cards.budget.charge(cost)
            if self.limiter is not None:
                self.limiter.take(user_id, cost)
        return charge

    def _prefetch(self, user_id: int, names: list[str]) -> None:
        """Prices for the first results nobody tracks, so the next keystroke shows them.

        Billed to inline mode's budget only: a nicety must not use up the
        user's own Steam budget, which the app shares.
        """
        missing = [n for n in names if self.store.price(n) is None][:PREFETCH]
        for name in missing:
            task = asyncio.ensure_future(self._quietly(self.prices.quote(name, self.cards.budget.charge)))
            self._background.add(task)
            task.add_done_callback(self._background.discard)

    @staticmethod
    async def _quietly(coro) -> None:
        try:
            await coro
        except (NoBudget, SteamError, web.HTTPException):
            pass
        except Exception:
            log.exception("Inline price prefetch failed")

    # -- results ---------------------------------------------------------------

    def _item_result(self, hash_name: str, holding, lang: str) -> dict:
        t = TEXTS[lang]
        snap = self.store.market_snapshot(hash_name)
        currency = self.store.currency
        moves = [f"{t[key]} {percent(ratio, lang)}"
                 for key, ratio in (("day", snap["change_24h"]), ("week", snap["change_7d"])) if ratio is not None]
        if snap["cents"] is not None:
            head = [money(snap["cents"], currency, lang)] + moves
        else:
            head = [t["no_price"] if snap["checked_at"] else t["price_later"]]
        extra = (t["you_have"].format(n=holding.qty) if holding
                 else t["for_sale"].format(n=f"{snap['sell_listings']:,}".replace(",", "," if lang == "en" else "\u00a0"))
                 if snap["sell_listings"] else None)
        description = " · ".join(head) + (f"\n{extra}" if extra else "")

        # What the chat sees: never the sender's own quantities.
        lines = [f"<b>{html.escape(snap['name'])}</b>"]
        if snap["cents"] is not None:
            lines.append(html.escape(" · ".join([money(snap["cents"], currency, lang)] + moves)))
        lines.append(f'<a href="{html.escape(MARKET_URL + quote(hash_name), quote=True)}">{t["steam"]}</a>')
        content = {"message_text": "\n".join(lines), "parse_mode": "HTML"}
        if self.cards.enabled:
            content["link_preview_options"] = {
                "url": self.cards.item_url(hash_name, lang, snap["cents"]),
                "prefer_large_media": True, "show_above_text": True,
            }
        else:
            content["link_preview_options"] = {"is_disabled": True}
        result = {"type": "article", "id": item_key(hash_name), "title": snap["name"], "description": description,
                  "input_message_content": content}
        if snap["icon"]:
            result.update(thumbnail_url=ICON_URL.format(icon=snap["icon"]), thumbnail_width=96, thumbnail_height=96)
        link = self._deep_link(f"it_{item_key(hash_name)}")
        if link:
            result["reply_markup"] = {"inline_keyboard": [[{"text": t["track"], "url": link}]]}
        return result

    def _portfolio_result(self, user_id: int, lang: str) -> dict | None:
        summary = portfolio_summary(self.store, user_id)
        if summary is None or not self.cards.enabled:
            return None
        t = TEXTS[lang]
        ratio = summary["ratio"]
        text = [f"<b>{t['portfolio']}</b>"]
        if ratio is not None:
            text.append(f"{t['profit']}: {percent(ratio, lang)}")
            description = t["portfolio_desc"].format(ratio=percent(ratio, lang), n=summary["count"])
        else:
            description = t["portfolio_items"].format(n=summary["count"])
        version = f"{round((ratio or 0) * 10000)}-{summary['count']}"
        result = {
            "type": "article", "id": "portfolio", "title": t["portfolio"], "description": description,
            "input_message_content": {
                "message_text": "\n".join(text), "parse_mode": "HTML",
                "link_preview_options": {"url": self.cards.portfolio_url(user_id, lang, version),
                                         "prefer_large_media": True, "show_above_text": True},
            },
        }
        top = summary["top"][0] if summary["top"] else None
        if top is not None and top.icon:
            result.update(thumbnail_url=ICON_URL.format(icon=top.icon), thumbnail_width=96, thumbnail_height=96)
        link = self._deep_link("inline")
        if link:
            result["reply_markup"] = {"inline_keyboard": [[{"text": t["track"], "url": link}]]}
        return result

    def _deep_link(self, payload: str) -> str | None:
        username = getattr(self.bot, "username", None)
        return f"https://t.me/{username}?{urlencode({'start': payload})}" if username else None

    # -- the bot's side of a card --------------------------------------------------

    def start_payload(self, payload: str, lang: str) -> tuple[str, dict] | None:
        """/start it_<key> (from a card's button): the item, with a button that opens it in the app."""
        if not payload.startswith("it_"):
            return None
        hash_name = self.cards.resolve(payload[3:])
        if hash_name is None:
            return None
        t = TEXTS[language(lang)]
        name = (self.store.item(hash_name) or (hash_name,))[0]
        url = f"{self.settings.public_url}{'&' if '?' in self.settings.public_url else '?'}{urlencode({'item': hash_name})}"
        return t["item_intro"].format(name=name), {
            "inline_keyboard": [[{"text": t["open_item"], "web_app": {"url": url}}]]}
