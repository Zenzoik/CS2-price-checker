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
from typing import Callable

from ..steam import ItemNotFound, RateLimited, SearchResult, SteamError, SteamMarket
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


def to_cents(price: float | None) -> int | None:
    return None if price is None else int(round(price * 100))


class PriceService:
    def __init__(self, store: Store, market: SteamMarket, *, currency: str, kind: str,
                 refresh_minutes: float, clock: Callable[[], float] = time.time,
                 interactive_wait: float = INTERACTIVE_WAIT):
        self.store = store
        self.market = market
        self.fetcher = PriceFetcher(market, currency, kind)
        self.max_age = refresh_minutes * 60
        self.interactive_wait = interactive_wait
        self._clock = clock
        self._lock = asyncio.Lock()
        self._searches: dict[str, tuple[float, list[SearchResult]]] = {}

    async def _call(self, fn, *args, wait: float | None = None, cached: Callable[[], object] | None = None,
                    on_result: Callable[[object], None] | None = None):
        """Runs fn in a thread under the lock; the lock is held until the thread ends.

        `on_result` stores the result before the lock is released, and `cached` is
        checked once the lock is held, so a request that queued behind an identical
        one reuses its result instead of asking Steam again. Cancelling the caller
        (client went away, shutdown) does not release the lock early, so two
        threads never use SteamMarket at once.
        """
        try:
            await asyncio.wait_for(self._lock.acquire(), wait)
        except asyncio.TimeoutError as e:
            raise SteamBusy("Steam is busy") from e
        try:
            hit = cached() if cached else None
            if hit is not None:
                self._lock.release()
                return hit
            future = asyncio.ensure_future(asyncio.to_thread(fn, *args))
        except BaseException:
            self._lock.release()
            raise

        def done(f: asyncio.Future) -> None:
            try:
                if on_result and not f.cancelled() and f.exception() is None:
                    on_result(f.result())
            except Exception:
                log.exception("Could not store a Steam result")
            finally:
                self._lock.release()
        future.add_done_callback(done)
        return await asyncio.shield(future)

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

        Items are visited least recently checked first, and every attempt (also a
        failed one) counts as a check, so items that keep failing move to the back
        instead of blocking the rest.
        """
        stale_before = self._clock() - self.max_age * 0.9
        due = [name for name, checked in self.store.tracked() if checked is None or checked < stale_before]
        if not due:
            return 0
        self.fetcher.start_pass()
        done = errors = 0
        for name in due:
            try:
                await self._call(self.fetcher.fetch, name, on_result=lambda q, n=name: self._store_quote(n, q))
            except ItemNotFound:
                # Also what Steam answers under load; keep the last known price.
                log.warning("%s: not found on the Steam market", name)
                self.store.mark_checked(name, self._clock())
                continue
            except (RateLimited, CurrencyMismatch) as e:
                log.error("Price refresh stopped: %s", e)
                break
            except SteamError as e:
                log.warning("%s: %s", name, e)
                self.store.mark_checked(name, self._clock())
                errors += 1
                if errors >= MAX_CONSECUTIVE_ERRORS:
                    log.error("Price refresh paused after %d Steam errors in a row", errors)
                    break
                continue
            errors = 0
            done += 1
        log.info("Refreshed %d/%d price(s)", done, len(due))
        return done

    async def run(self) -> None:
        while True:
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Price refresh failed")
            await asyncio.sleep(max(60.0, self.max_age / 4))
