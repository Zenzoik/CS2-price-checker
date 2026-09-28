"""Client for the (unofficial, undocumented) Steam Community Market endpoints.

Endpoints used:

* ``/market/search/render``  – item search, used to resolve a user query to a hash name.
* ``/market/orderbook``      – order book the market page itself uses. Returns exact
  integer prices, but always in the currency Steam picks from the caller's IP.
* ``/market/priceoverview``  – lowest listing / median price in an explicit currency.
  Heavily rate limited and returns locale-formatted strings.
* ``/inventory/<steamid>/730/2`` – a public CS2 inventory (403 when it is private).
* ``/id/<name>/?xml=1``        – resolves a custom profile URL to a SteamID64.

The old ``Market_LoadOrderSpread`` / ``item_nameid`` scraping stopped working when
Steam moved listing pages to server-side rendering (2026).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Callable

import requests

log = logging.getLogger(__name__)

COMMUNITY_URL = "https://steamcommunity.com"
MARKET_URL = f"{COMMUNITY_URL}/market"
CS2_APPID = 730
# Steam's "Container" type: cases, capsules, souvenir packages, etc.
CONTAINER_TAG = "tag_CSGO_Type_WeaponCase"

# Steam ECurrencyCode -> ISO 4217.
STEAM_CURRENCIES: dict[int, str] = {
    1: "USD", 2: "GBP", 3: "EUR", 4: "CHF", 5: "RUB", 6: "PLN", 7: "BRL", 8: "JPY",
    9: "NOK", 10: "IDR", 11: "MYR", 12: "PHP", 13: "SGD", 14: "THB", 15: "VND",
    16: "KRW", 18: "UAH", 19: "MXN", 20: "CAD", 21: "AUD", 22: "NZD",
    23: "CNY", 24: "INR", 25: "CLP", 26: "PEN", 27: "COP", 28: "ZAR", 29: "HKD",
    30: "TWD", 31: "SAR", 32: "AED", 35: "ILS", 37: "KZT", 38: "KWD", 39: "QAR",
    40: "CRC", 41: "UYU",
}
# TRY (17) and ARS (34) are left out on purpose: Steam moved Turkey and Argentina
# to USD in 2023, so asking for them may silently return dollar prices.
ISO_TO_STEAM = {iso: code for code, iso in STEAM_CURRENCIES.items()}


class SteamError(Exception):
    """Any failure talking to the Steam market."""


class ItemNotFound(SteamError):
    """Steam does not know an item with this hash name."""


class RateLimited(SteamError):
    """Steam kept answering 429 after all retries."""


class SteamHTTPError(SteamError):
    """A 4xx answer other than 429."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class ProfileNotFound(SteamError):
    """No Steam profile behind this link or name."""


class PrivateInventory(SteamError):
    """The profile's inventory is not public."""


# SteamID64 = this base + the 32-bit account id (as in trade offer links).
STEAMID64_BASE = 76561197960265728
_STEAMID_RE = re.compile(r"^7656119\d{10}$")
_VANITY_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")


def parse_profile(text: str) -> tuple[str, str] | None:
    """Recognises a Steam profile reference in user input.

    Returns ("steamid", "7656…") or ("vanity", "name"), or None when the text is
    not a profile link (so it can be treated as a search query instead). Accepts
    profile and inventory URLs (/profiles/<id>, /id/<name>), trade offer links
    (?partner=<account id>) and a bare SteamID64.
    """
    s = text.strip()
    if _STEAMID_RE.match(s):
        return ("steamid", s)
    m = re.search(r"steamcommunity\.com/(profiles|id)/([^/?#\s]+)", s, re.I)
    if m:
        kind, value = m.group(1).lower(), m.group(2)
        if kind == "profiles" and _STEAMID_RE.match(value):
            return ("steamid", value)
        if kind == "id" and _VANITY_RE.match(value):
            return ("vanity", value)
        return None
    m = re.search(r"steamcommunity\.com/tradeoffer/new/?\?(?:[^#\s]*&)?partner=(\d{1,10})\b", s, re.I)
    if m and 0 < int(m.group(1)) < 2**32:
        return ("steamid", str(STEAMID64_BASE + int(m.group(1))))
    return None


@dataclass(frozen=True)
class SearchResult:
    hash_name: str
    name: str
    sell_price_usd: float | None
    sell_listings: int
    # Path for https://community.fastly.steamstatic.com/economy/image/<icon_url>
    icon_url: str | None = None


@dataclass(frozen=True)
class InventoryItem:
    hash_name: str
    name: str
    icon_url: str | None
    qty: int
    container: bool  # case, capsule, package: what investors usually hold


@dataclass(frozen=True)
class Quote:
    hash_name: str
    currency: str
    highest_buy: float | None
    lowest_sell: float | None
    source: str

    def price(self, kind: str) -> float | None:
        if kind == "buy":
            return self.highest_buy
        if kind == "sell":
            return self.lowest_sell
        raise ValueError(f"unknown price kind: {kind!r}")


def currency_name(code: int) -> str:
    return STEAM_CURRENCIES.get(code, f"#{code}")


def parse_price(text: str | None) -> float | None:
    """Parses Steam's locale-formatted prices: '$1,234.56', '485,55₴', '10,--€', '1 234 pуб.'."""
    if not text:
        return None
    s = re.sub(r"[^\d.,]", "", text).strip(".,")
    if not any(c.isdigit() for c in s):
        return None
    sep = max(s.rfind("."), s.rfind(","))
    if sep == -1:
        return float(s)
    mark, head, frac = s[sep], s[:sep], s[sep + 1:]
    if "." in s and "," in s:
        # Both separators present: the rightmost one is the decimal mark.
        return float(re.sub(r"[.,]", "", head) + "." + frac)
    if s.count(mark) == 1 and len(frac) in (1, 2):
        return float(head + "." + frac)
    # Only thousands separators, e.g. '1,234' or '1.234.567'.
    return float(s.replace(mark, ""))


def _cents(amount: object, count: object) -> float | None:
    """Order book amounts are integer minor units; 0 orders means no price."""
    if not count or amount is None:
        return None
    return int(amount) / 100


class SteamMarket:
    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        request_delay: float = 1.5,
        overview_delay: float = 3.5,
        max_retries: int = 5,
        server_retries: int = 2,
        backoff: float = 10.0,
        max_backoff: float = 300.0,
        timeout: float = 15.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.session = session or requests.Session()
        self.request_delay = request_delay
        # priceoverview allows roughly 20 requests per minute per IP.
        self.overview_delay = max(overview_delay, request_delay)
        self.max_retries = max_retries
        # 5xx / network failures: retry a little, then let the caller move on.
        self.server_retries = server_retries
        self.backoff = backoff
        self.max_backoff = max_backoff
        self.timeout = timeout
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None

    # -- public API ---------------------------------------------------------

    def search(self, query: str, *, containers_only: bool = True, count: int = 10) -> list[SearchResult]:
        params: dict[str, object] = {
            "query": query,
            "appid": CS2_APPID,
            "start": 0,
            "count": count,
            "norender": 1,
            "search_descriptions": 0,
        }
        if containers_only:
            params[f"category_{CS2_APPID}_Type[]"] = CONTAINER_TAG
        # Interactive: fail fast instead of backing off for minutes.
        data = self._get_json(f"{MARKET_URL}/search/render/", params, max_retries=min(self.max_retries, 2))
        try:
            results = []
            for r in (data or {}).get("results") or []:
                price = r.get("sell_price")
                icon = (r.get("asset_description") or {}).get("icon_url")
                results.append(SearchResult(
                    hash_name=r["hash_name"],
                    name=r.get("name") or r["hash_name"],
                    sell_price_usd=int(price) / 100 if price else None,
                    sell_listings=int(r.get("sell_listings") or 0),
                    icon_url=icon if isinstance(icon, str) and icon else None,
                ))
            return results
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            raise SteamError(f"unexpected search response: {e!r}") from e

    def orderbook(self, hash_name: str) -> Quote:
        """Highest buy / lowest sell order in the currency Steam assigns to this IP."""
        params = {"q": "Load", "qp": json.dumps([CS2_APPID, hash_name])}
        body = self._get_json(f"{MARKET_URL}/orderbook", params)
        payload = body.get("data") if isinstance(body, dict) else None
        if not isinstance(payload, dict):
            raise SteamError("unexpected orderbook response")
        book = payload.get("data")
        if not payload.get("success") or not book:
            raise ItemNotFound(hash_name)
        try:
            return Quote(
                hash_name=hash_name,
                currency=currency_name(int(book["eCurrency"])),
                highest_buy=_cents(book.get("amtMaxBuyOrder"), book.get("cBuyOrders")),
                lowest_sell=_cents(book.get("amtMinSellOrder"), book.get("cSellOrders")),
                source="orderbook",
            )
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            raise SteamError(f"unexpected orderbook response: {e!r}") from e

    def price_overview(self, hash_name: str, currency: str) -> Quote:
        """Lowest listing price in an explicit currency (no buy-order data)."""
        if currency not in ISO_TO_STEAM:
            raise ValueError(f"unsupported currency {currency!r}")
        params = {"appid": CS2_APPID, "currency": ISO_TO_STEAM[currency], "market_hash_name": hash_name}
        # priceoverview answers an unknown item with 500 {"success": false}.
        data = self._get_json(f"{MARKET_URL}/priceoverview/", params, min_interval=self.overview_delay,
                              not_found_on_500=hash_name)
        if not isinstance(data, dict) or not data.get("success"):
            raise ItemNotFound(hash_name)
        lowest = data.get("lowest_price")
        return Quote(
            hash_name=hash_name,
            currency=currency,
            highest_buy=None,
            lowest_sell=parse_price(lowest) if isinstance(lowest, str) else None,
            source="priceoverview",
        )

    def resolve_vanity(self, name: str) -> str:
        """SteamID64 behind steamcommunity.com/id/<name>."""
        if not _VANITY_RE.match(name):
            raise ProfileNotFound(name)
        # Interactive and strictly limited by Steam: no backoff, the user retries.
        resp = self._get(f"{COMMUNITY_URL}/id/{name}/", {"xml": 1}, self.request_delay, 0)
        m = re.search(r"<steamID64>(7656119\d{10})</steamID64>", resp.text)
        if not m:
            raise ProfileNotFound(name)
        return m.group(1)

    def inventory(self, steamid: str, *, max_pages: int = 3, page_size: int = 2000) -> list[InventoryItem]:
        """Marketable items of a public CS2 inventory, one entry per hash name.

        Storage unit contents are not part of the public inventory.
        """
        if not _STEAMID_RE.match(steamid):
            raise ProfileNotFound(steamid)
        url = f"{COMMUNITY_URL}/inventory/{steamid}/{CS2_APPID}/2"
        params: dict[str, object] = {"l": "english", "count": page_size}
        descriptions: dict[tuple[str, str], dict] = {}
        amounts: dict[tuple[str, str], int] = {}
        for _ in range(max_pages):
            try:
                data = self._get_json(url, params, max_retries=0)
            except SteamHTTPError as e:
                if e.status in (401, 403):
                    raise PrivateInventory(steamid) from e
                if e.status in (400, 404):
                    raise ProfileNotFound(steamid) from e
                raise
            if data is None:  # what Steam returns for some private profiles
                raise PrivateInventory(steamid)
            if not isinstance(data, dict) or data.get("success") not in (1, True):
                raise SteamError("unexpected inventory response")
            try:
                for d in data.get("descriptions") or []:
                    descriptions[(str(d["classid"]), str(d.get("instanceid", "0")))] = d
                for a in data.get("assets") or []:
                    key = (str(a["classid"]), str(a.get("instanceid", "0")))
                    amounts[key] = amounts.get(key, 0) + int(a.get("amount") or 1)
            except (KeyError, TypeError, ValueError) as e:
                raise SteamError(f"unexpected inventory response: {e!r}") from e
            if not data.get("more_items") or not data.get("last_assetid"):
                break
            params["start_assetid"] = data["last_assetid"]

        items: dict[str, InventoryItem] = {}
        for key, qty in amounts.items():
            d = descriptions.get(key)
            if not d or not _sellable(d):
                continue
            name = d.get("market_hash_name")
            if not isinstance(name, str) or not name:
                continue
            old = items.get(name)
            icon = d.get("icon_url") if isinstance(d.get("icon_url"), str) else None
            items[name] = InventoryItem(
                hash_name=name,
                name=d.get("market_name") or d.get("name") or name,
                icon_url=icon or (old.icon_url if old else None),
                qty=qty + (old.qty if old else 0),
                container=_is_container(d),
            )
        return sorted(items.values(), key=lambda i: (not i.container, i.name.lower()))

    # -- HTTP plumbing ------------------------------------------------------

    def _get_json(self, url: str, params: dict, *, min_interval: float | None = None,
                  max_retries: int | None = None, not_found_on_500: str | None = None):
        resp = self._get(
            url, params,
            min_interval if min_interval is not None else self.request_delay,
            max_retries if max_retries is not None else self.max_retries,
            not_found_on_500,
        )
        try:
            return resp.json()
        except ValueError as e:
            raise SteamError(f"non-JSON response from {url}") from e

    def _get(self, url: str, params: dict, min_interval: float, max_retries: int,
             not_found_on_500: str | None = None) -> requests.Response:
        delay = self.backoff
        attempt = 0
        while True:
            self._throttle(min_interval)
            rate_limited = False
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                error = f"network error: {e}"
            else:
                if resp.status_code < 400:
                    return resp
                if resp.status_code == 429:
                    rate_limited = True
                    error = "rate limited (429)"
                elif resp.status_code >= 500:
                    if not_found_on_500 is not None and _is_logical_failure(resp):
                        raise ItemNotFound(not_found_on_500)
                    error = f"server error ({resp.status_code})"
                else:
                    raise SteamHTTPError(resp.status_code, f"HTTP {resp.status_code} from {url}")
                retry_after = _retry_after(resp)
                if retry_after is not None:
                    delay = max(delay, retry_after)
            limit = max_retries if rate_limited else min(max_retries, self.server_retries)
            if attempt >= limit:
                if rate_limited:
                    raise RateLimited(f"Steam is rate limiting this IP (gave up after {attempt} retries)")
                raise SteamError(error)
            attempt += 1
            wait = min(delay, self.max_backoff)
            log.warning("Steam %s, retrying in %.0fs (%d/%d)", error, wait, attempt, limit)
            self._sleep(wait)
            delay = min(delay * 2, self.max_backoff)

    def _throttle(self, min_interval: float) -> None:
        now = self._clock()
        if self._last_request is not None:
            wait = self._last_request + min_interval - now
            if wait > 0:
                self._sleep(wait)
                now += wait
        self._last_request = now


def _sellable(d: dict) -> bool:
    """Marketable now, or only held back for a while (trade protection, new items)."""
    if d.get("marketable") in (1, True):
        return True
    # Temporarily restricted items carry an expiry; badges, coins and default
    # music kits are never marketable and have none.
    return bool(d.get("cache_expiration") or d.get("item_expiration"))


def _is_container(d: dict) -> bool:
    for tag in d.get("tags") or []:
        if isinstance(tag, dict) and tag.get("category") == "Type" and tag.get("internal_name") == "CSGO_Type_WeaponCase":
            return True
    return False


def _is_logical_failure(resp: requests.Response) -> bool:
    try:
        body = resp.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("success") is False


def _retry_after(resp: requests.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
