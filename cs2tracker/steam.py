"""Client for the (unofficial, undocumented) Steam Community Market endpoints.

Endpoints used:

* ``/market/search/render``  – item search, used to resolve a user query to a hash name.
* ``/market/orderbook``      – order book the market page itself uses. Returns exact
  integer prices, but always in the currency Steam picks from the caller's IP.
* ``/market/priceoverview``  – lowest listing / median price in an explicit currency.
  Heavily rate limited and returns locale-formatted strings.

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

MARKET_URL = "https://steamcommunity.com/market"
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


@dataclass(frozen=True)
class SearchResult:
    hash_name: str
    name: str
    sell_price_usd: float | None
    sell_listings: int


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
                results.append(SearchResult(
                    hash_name=r["hash_name"],
                    name=r.get("name") or r["hash_name"],
                    sell_price_usd=int(price) / 100 if price else None,
                    sell_listings=int(r.get("sell_listings") or 0),
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
                    raise SteamError(f"HTTP {resp.status_code} from {url}")
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
