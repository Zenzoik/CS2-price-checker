"""One price-update pass: sheet names -> Steam quotes -> sheet prices."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from .steam import ISO_TO_STEAM, ItemNotFound, Quote, RateLimited, SteamError, SteamMarket

log = logging.getLogger(__name__)

# Stop a pass after this many consecutive network/server failures (Steam down,
# no internet) instead of backing off for every remaining item.
MAX_CONSECUTIVE_ERRORS = 3

PRICE_LABELS = {"buy": "highest buy order", "sell": "lowest listing"}


class CurrencyMismatch(Exception):
    """The requested price cannot be obtained in the configured currency."""


class PriceFetcher:
    """Picks the Steam endpoint that can deliver `kind` prices in one currency.

    The order book is preferred: one cheap request with exact integer prices for both
    sides. Its currency is chosen by Steam from the caller's IP, so if that is not the
    target currency, lowest-listing prices fall back to priceoverview (explicit
    currency, slower). Buy-order prices exist only in the order book.

    With currency=None ("auto") the currency of the first order book seen is pinned
    for the lifetime of the fetcher, so a VPN/IP change never mixes currencies in
    the sheet – neither within a pass nor between `watch` cycles.
    """

    def __init__(self, market: SteamMarket, currency: str | None, kind: str):
        self.market = market
        self.currency = currency
        self.auto = currency is None
        self.kind = kind
        self._orderbook_usable = True

    def start_pass(self) -> None:
        # Re-probe the order book each pass: the IP's currency may have changed back.
        self._orderbook_usable = True

    def fetch(self, hash_name: str) -> Quote:
        if self._orderbook_usable:
            quote = self.market.orderbook(hash_name)
            if self.currency is None:
                self.currency = quote.currency
                log.info("Currency: %s (chosen by Steam for this IP)", quote.currency)
                if quote.currency not in ISO_TO_STEAM:
                    log.warning("Steam returned an unknown currency code %s", quote.currency)
            if quote.currency == self.currency:
                return quote
            if self.auto and (self.kind == "buy" or self.currency not in ISO_TO_STEAM):
                raise CurrencyMismatch(
                    f"Steam's currency for your IP changed from {self.currency} to "
                    f"{quote.currency} (VPN?). Restart, or set an explicit currency."
                )
            if self.kind == "buy" or self.currency not in ISO_TO_STEAM:
                raise CurrencyMismatch(
                    f"Steam serves order books in {quote.currency} for your IP, so buy-order "
                    f"prices in {self.currency} are unavailable. Either set currency = "
                    f'"{quote.currency}" (or "auto"), or set price = "sell" to track the '
                    f"lowest listing price in {self.currency}."
                )
            log.info(
                "Steam serves order books in %s for this IP; using priceoverview for %s prices",
                quote.currency, self.currency,
            )
            self._orderbook_usable = False
        return self.market.price_overview(hash_name, self.currency)


@dataclass
class UpdateReport:
    prices: dict[str, float] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    currency: str | None = None
    stopped: str | None = None  # why the pass ended early, if it did

    def summary(self) -> str:
        parts = [f"updated {len(self.prices)}"]
        if self.failed:
            parts.append(f"failed {len(self.failed)}")
        if self.stopped:
            parts.append(f"stopped early: {self.stopped}")
        cur = f" [{self.currency}]" if self.currency else ""
        return ", ".join(parts) + cur


def update_prices(sheet, fetcher: PriceFetcher, now: datetime | None = None) -> UpdateReport:
    """Fetches a price for every item and writes all of them in one batch.

    Raises CurrencyMismatch only if nothing could be fetched; otherwise whatever was
    fetched before a problem (or Ctrl+C) is still written.
    """
    report = UpdateReport()
    items = sheet.items()
    if not items:
        log.info("No items in the sheet yet – add some first.")
        return report

    log.info(
        "Updating %d item(s): %s in %s",
        len(items), PRICE_LABELS[fetcher.kind], fetcher.currency or "Steam's currency for this IP",
    )
    fetcher.start_pass()
    try:
        _fetch_all(items, fetcher, report)
    except BaseException:
        # Ctrl+C or a fatal error: still save what was fetched, but never let a
        # failing save hide the original exception.
        try:
            _save(sheet, fetcher, report, now)
        except Exception as e:
            log.error("Could not save the prices fetched so far: %s", e)
        raise
    _save(sheet, fetcher, report, now)
    return report


def _save(sheet, fetcher: PriceFetcher, report: UpdateReport, now: datetime | None) -> None:
    report.currency = fetcher.currency
    if report.prices:
        sheet.write_prices(report.prices)
        stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        sheet.log_history(stamp, report.currency or "", report.prices)


def _fetch_all(items: list[tuple[int, str]], fetcher: PriceFetcher, report: UpdateReport) -> None:
    quotes: dict[str, Quote | None] = {}
    consecutive_errors = 0
    for row, name in items:
        if name not in quotes:
            try:
                quotes[name] = fetcher.fetch(name)
                consecutive_errors = 0
            except ItemNotFound:
                quotes[name] = None
                consecutive_errors = 0
                report.failed[name] = "not found on Steam market (check the exact hash name)"
            except CurrencyMismatch as e:
                if not report.prices:
                    raise
                report.failed[name] = str(e)
                report.stopped = "currency changed mid-run"
            except RateLimited as e:
                report.failed[name] = str(e)
                report.stopped = "Steam rate limit"
            except SteamError as e:
                quotes[name] = None
                report.failed[name] = str(e)
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    report.stopped = f"{consecutive_errors} Steam errors in a row"
            if name in report.failed:
                log.warning("%s: %s", name, report.failed[name])
            if report.stopped:
                break
        quote = quotes[name]
        if quote is None:
            continue
        price = quote.price(fetcher.kind)
        if price is None:
            report.failed[name] = "no buy orders" if fetcher.kind == "buy" else "no listings"
            continue
        if name not in report.prices:
            report.prices[name] = price
            log.info("Row %d: %s -> %.2f %s", row, name, price, quote.currency)
