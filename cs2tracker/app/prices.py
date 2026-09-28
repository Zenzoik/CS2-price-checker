"""Steam access for the Mini App: search, on-demand quotes, and a background refresh.

SteamMarket is synchronous, throttles itself and is not thread-safe, so every call
runs in a worker thread while holding one lock. The background refresh takes the
lock per item, so a user's search or quote cuts in after at most one Steam call;
a user request that cannot get the lock quickly fails with SteamBusy instead of
queueing without bound.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Callable

from ..steam import (
    InventoryItem, ItemNotFound, PrivateInventory, ProfileNotFound, RateLimited, SearchResult, SteamError,
    SteamMarket,
)
from ..tracker import CurrencyMismatch, PriceFetcher
from .db import Store

log = logging.getLogger(__name__)

SEARCH_TTL = 600
SEARCH_CACHE_SIZE = 500
MAX_CONSECUTIVE_ERRORS = 3
# How long a user request may wait for Steam before giving up.
INTERACTIVE_WAIT = 10.0


class SteamBusy(SteamError):
    """Steam access is saturated right now; the user should retry shortly."""


class SteamGate:
    """One lock around a synchronous SteamMarket; see the module docstring."""

    def __init__(self):
        self.lock = asyncio.Lock()

    async def call(self, fn, *args, wait: float | None = None, cached: Callable[[], object] | None = None,
                    on_result: Callable[[object], None] | None = None):
        """Runs fn in a thread under the lock; the lock is held until the thread ends.

        `on_result` stores the result before the lock is released, and `cached` is
        checked once the lock is held, so a request that queued behind an identical
        one reuses its result instead of asking Steam again. Cancelling the caller
        (client went away, shutdown) does not release the lock early, so two
        threads never use SteamMarket at once.
        """
        try:
            await asyncio.wait_for(self.lock.acquire(), wait)
        except asyncio.TimeoutError as e:
            raise SteamBusy("Steam is busy") from e
        try:
            hit = cached() if cached else None
            if hit is not None:
                self.lock.release()
                return hit
            future = asyncio.ensure_future(asyncio.to_thread(fn, *args))
        except BaseException:
            self.lock.release()
            raise

        def done(f: asyncio.Future) -> None:
            try:
                if on_result and not f.cancelled() and f.exception() is None:
                    on_result(f.result())
            except Exception:
                log.exception("Could not store a Steam result")
            finally:
                self.lock.release()
        future.add_done_callback(done)
        return await asyncio.shield(future)


def to_cents(price: float | None) -> int | None:
    return None if price is None else int(round(price * 100))


class PriceService:
    def __init__(self, store: Store, market: SteamMarket, *, currency: str, kind: str,
                 refresh_minutes: float, clock: Callable[[], float] = time.time,
                 interactive_wait: float = INTERACTIVE_WAIT, overview_market: SteamMarket | None = None):
        self.store = store
        self.market = market
        self.fetcher = PriceFetcher(market, currency, kind)
        # priceoverview only knows the lowest listing, so it can only help "sell".
        self._overview = overview_market if kind == "sell" else None
        self._overview_gate = SteamGate()
        self.max_age = refresh_minutes * 60
        self.interactive_wait = interactive_wait
        self._clock = clock
        self._gate = SteamGate()
        self._lock = self._gate.lock
        self._searches: dict[str, tuple[float, list[SearchResult]]] = {}
        self._wake = asyncio.Event()

    async def _call(self, fn, *args, **kwargs):
        return await self._gate.call(fn, *args, **kwargs)

    # -- interactive ---------------------------------------------------------

    def _cached_search(self, key: str) -> list[SearchResult] | None:
        cached = self._searches.get(key)
        return cached[1] if cached and self._clock() - cached[0] < SEARCH_TTL else None

    async def search(self, query: str, charge: Callable[[int], None] = lambda n: None) -> list[SearchResult]:
        """`charge(n)` is called before Steam is asked (n = worst-case Steam calls)."""
        key = " ".join(query.lower().split())
        results = self._cached_search(key)
        if results is not None:
            return results
        charge(2)

        def store(found: list[SearchResult]) -> None:
            self.store.remember_items([(r.hash_name, r.name, r.icon_url) for r in found])
            if len(self._searches) >= SEARCH_CACHE_SIZE:
                self._searches.pop(next(iter(self._searches)))
            self._searches[key] = (self._clock(), found)
        return await self._call(self._search_sync, query, wait=self.interactive_wait,
                                cached=lambda: self._cached_search(key), on_result=store)

    def _search_sync(self, query: str) -> list[SearchResult]:
        # Cases and capsules first: "breakout" should find the case, not stickers.
        results = self.market.search(query, containers_only=True)
        if len(results) < 3:
            seen = {r.hash_name for r in results}
            more = self.market.search(query, containers_only=False)
            results = results + [r for r in more if r.hash_name not in seen]
        return results

    def fresh_price(self, hash_name: str) -> tuple[int | None, float] | None:
        cached = self.store.price(hash_name)
        if cached and cached[1] is not None and self._clock() - cached[1] < self.max_age:
            return cached
        return None

    async def quote(self, hash_name: str, charge: Callable[[int], None] = lambda n: None) -> int | None:
        """Current price in cents (None: nothing listed). Raises ItemNotFound / SteamError."""
        fresh = self.fresh_price(hash_name)
        if fresh:
            return fresh[0]
        charge(1)
        try:
            result = await self._call(self.fetcher.fetch, hash_name, wait=self.interactive_wait,
                                      cached=lambda: self.fresh_price(hash_name),
                                      on_result=lambda q: self._store_quote(hash_name, q))
        except CurrencyMismatch as e:
            raise SteamError(str(e)) from e
        if isinstance(result, tuple):  # fetched by a request that held the lock before us
            return result[0]
        return to_cents(result.price(self.fetcher.kind))

    def _store_quote(self, hash_name: str, quote) -> None:
        self.store.set_price(hash_name, to_cents(quote.price(self.fetcher.kind)), self._clock())

    # -- background ----------------------------------------------------------

    async def refresh(self) -> int:
        """One pass over every held item not checked within the refresh interval.

        Items are visited least recently checked first (never-priced ones, e.g. a
        fresh import, lead), and every attempt, failed or not, counts as a check,
        so items that keep failing move to the back instead of blocking the rest.
        Two workers share the queue when a second source is configured: the order
        book and priceoverview have separate Steam limits, so together they price
        a large import about half again as fast.
        """
        stale_before = self._clock() - self.max_age * 0.9
        due = [name for name, checked in self.store.tracked() if checked is None or checked < stale_before]
        if not due:
            return 0
        self.fetcher.start_pass()
        queue = deque(due)
        workers = [self._work(queue, "order book", self._gate, self.fetcher.fetch)]
        if self._overview is not None:
            workers.append(self._work(queue, "priceoverview", self._overview_gate, self._overview_fetch))
        done = sum(await asyncio.gather(*workers))
        log.info("Refreshed %d/%d price(s)", done, len(due))
        return done

    def _overview_fetch(self, name: str):
        return self._overview.price_overview(name, self.fetcher.currency)

    async def _work(self, queue: deque, source: str, gate: "SteamGate", fetch) -> int:
        """Takes items off the shared queue until it is empty or this source gives out."""
        done = errors = 0
        while queue:
            name = queue.popleft()
            try:
                await gate.call(fetch, name, on_result=lambda q, n=name: self._store_quote(n, q))
            except ItemNotFound:
                # Also what Steam answers under load; keep the last known price.
                log.warning("%s: not found on the Steam market (%s)", name, source)
                self.store.mark_checked(name, self._clock())
                continue
            except (RateLimited, CurrencyMismatch) as e:
                queue.appendleft(name)  # the other worker may still get it
                log.error("Price refresh via %s stopped: %s", source, e)
                break
            except SteamError as e:
                log.warning("%s: %s (%s)", name, e, source)
                self.store.mark_checked(name, self._clock())
                errors += 1
                if errors >= MAX_CONSECUTIVE_ERRORS:
                    log.error("Price refresh via %s paused after %d Steam errors in a row", source, errors)
                    break
                continue
            errors = 0
            done += 1
        return done

    async def run(self) -> None:
        while True:
            self._wake.clear()  # before the pass: a wake() during it triggers the next one
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Price refresh failed")
            try:
                await asyncio.wait_for(self._wake.wait(), max(60.0, self.max_age / 4))
            except asyncio.TimeoutError:
                pass

    def wake(self) -> None:
        """Starts the next refresh pass now, e.g. after an import added items."""
        self._wake.set()


INVENTORY_TTL = 600
# Private / unknown profiles are remembered briefly so retries don't hit Steam,
# but not so long that making the inventory public feels ignored.
INVENTORY_ERROR_TTL = 120


class InventoryService:
    """Public CS2 inventories, behind their own lock and cache.

    Steam limits inventory requests much harder than market ones (a handful per
    minute per IP), so results are cached per profile and never retried.
    """

    def __init__(self, market: SteamMarket, *, clock: Callable[[], float] = time.time,
                 interactive_wait: float = INTERACTIVE_WAIT, ttl: float = INVENTORY_TTL):
        self.market = market
        self._gate = SteamGate()
        self._clock = clock
        self.interactive_wait = interactive_wait
        self.ttl = ttl
        self._steamids: dict[str, tuple[float, object]] = {}
        self._inventories: dict[str, tuple[float, object]] = {}

    def _fresh(self, cache: dict, key: str):
        """Cached value, or raises a cached error; None when nothing fresh is cached."""
        hit = cache.get(key)
        if hit:
            ttl = INVENTORY_ERROR_TTL if isinstance(hit[1], BaseException) else self.ttl
            if self._clock() - hit[0] < ttl:
                if isinstance(hit[1], BaseException):
                    raise hit[1]
                return hit[1]
        cache.pop(key, None)
        return None

    def _put(self, cache: dict, key: str, value) -> None:
        if len(cache) >= SEARCH_CACHE_SIZE:
            cache.pop(next(iter(cache)))
        cache[key] = (self._clock(), value)

    async def _cached_call(self, cache: dict, key: str, fn, arg, charge):
        found = self._fresh(cache, key)
        if found is not None:
            return found
        charge(1)
        try:
            return await self._gate.call(fn, arg, wait=self.interactive_wait,
                                         cached=lambda: self._fresh(cache, key),
                                         on_result=lambda value: self._put(cache, key, value))
        except (PrivateInventory, ProfileNotFound) as e:
            self._put(cache, key, e)
            raise

    async def load(self, profile: tuple[str, str], charge: Callable[[int], None] = lambda n: None
                   ) -> tuple[str, list[InventoryItem]]:
        """(steamid, items) for a parsed profile reference. Raises SteamError subclasses."""
        kind, value = profile
        steamid = value
        if kind == "vanity":
            steamid = await self._cached_call(self._steamids, value.lower(), self.market.resolve_vanity, value, charge)
        items = await self._cached_call(self._inventories, steamid, self.market.inventory, steamid, charge)
        return steamid, items
