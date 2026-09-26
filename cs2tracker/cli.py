"""Command line interface. Without a subcommand it shows the interactive menu."""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from pathlib import Path

import gspread
import requests
from google.auth.exceptions import GoogleAuthError

from .config import ConfigError, Settings, load_settings
from .sheet import PortfolioSheet, SheetSetupError, open_portfolio
from .steam import SearchResult, SteamError, SteamMarket
from .tracker import CurrencyMismatch, PriceFetcher, update_prices

log = logging.getLogger("cs2tracker")

# Errors worth retrying on the next `watch` round rather than exiting.
TRANSIENT_ERRORS = (SteamError, gspread.exceptions.APIError, GoogleAuthError, OSError)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # SUPPRESS defaults: the options may appear before or after the subcommand.
    args.config = getattr(args, "config", None)
    args.verbose = getattr(args, "verbose", False)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        settings = load_settings(args.config)
        market = SteamMarket(request_delay=settings.request_delay)
        if args.command == "search":
            return cmd_search(market, args.query, containers_only=not args.all_items)
        sheet = open_portfolio(
            settings.credentials, settings.spreadsheet, settings.worksheet, settings.history_worksheet,
            name_col=settings.name_col, price_col=settings.price_col,
            dry_run=getattr(args, "dry_run", False),
        )
        if args.command == "add":
            return cmd_add(market, sheet, args.names, containers_only=not args.all_items, assume_yes=args.yes)
        if args.command == "update":
            return cmd_update(market, sheet, settings)
        if args.command == "watch":
            return cmd_watch(market, sheet, settings, args.interval or settings.interval_minutes)
        return menu(market, sheet, settings)
    except (ConfigError, SheetSetupError, CurrencyMismatch) as e:
        log.error("%s", e)
    except SteamError as e:
        log.error("Steam error: %s", e)
    except gspread.exceptions.GSpreadException as e:
        log.error("Google Sheets error: %s", e)
    except GoogleAuthError as e:
        log.error("Google authentication failed (key revoked or no internet?): %s", e)
    except requests.RequestException as e:
        log.error("Network error: %s", e)
    except (KeyboardInterrupt, EOFError):
        print()
        return 130
    return 1


def _interval(value: str) -> float:
    try:
        minutes = float(value)
    except ValueError:
        minutes = math.nan
    if not math.isfinite(minutes) or not 1 <= minutes <= 10080:
        raise argparse.ArgumentTypeError("must be a number of minutes between 1 and 10080")
    return minutes


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # exit code 2 is reserved for "some items failed"
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def _parser() -> argparse.ArgumentParser:
    # Shared options are accepted both before and after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", type=Path, default=argparse.SUPPRESS,
                        help="path to config.toml (default: ./config.toml, then the project folder)")
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)

    p = _Parser(prog="cs2tracker", parents=[common],
                description="Track CS2 Steam Market prices in Google Sheets.")
    sub = p.add_subparsers(dest="command", parser_class=_Parser)

    s = sub.add_parser("search", parents=[common], help="search the market without touching the sheet")
    s.add_argument("query")
    s.add_argument("--all-items", action="store_true", help="search all CS2 items, not only containers")

    a = sub.add_parser("add", parents=[common], help="add items to the sheet (interactive if no names given)")
    a.add_argument("names", nargs="*", help="search queries, e.g. 'breakout case'")
    a.add_argument("--all-items", action="store_true", help="search all CS2 items, not only containers")
    a.add_argument("-y", "--yes", action="store_true", help="take the top search result without asking")

    u = sub.add_parser("update", parents=[common], help="update prices once (for cron / Task Scheduler)")
    u.add_argument("-n", "--dry-run", action="store_true", help="fetch prices but don't write to the sheet")

    w = sub.add_parser("watch", parents=[common], help="update prices in a loop until Ctrl+C")
    w.add_argument("-i", "--interval", type=_interval, help="minutes between updates")
    return p


# -- commands -----------------------------------------------------------------

def cmd_search(market: SteamMarket, query: str, *, containers_only: bool) -> int:
    results = _search(market, query, containers_only)
    if not results:
        print(f"Nothing found for {query!r}.")
        return 1
    _print_results(results)
    return 0


def cmd_add(market: SteamMarket, sheet: PortfolioSheet, queries: list[str], *,
            containers_only: bool, assume_yes: bool) -> int:
    chosen: list[str] = []
    for query in queries or _prompt_queries():
        try:
            results = _search(market, query, containers_only)
        except SteamError as e:
            print(f"Search failed for {query!r}: {e}")
            continue
        if not results:
            print(f"Nothing found for {query!r}.")
            continue
        pick = results[0] if assume_yes else _choose(results)
        if pick:
            chosen.append(pick.hash_name)
            print(f"  + {pick.hash_name}")
    if not chosen:
        print("Nothing to add.")
        return 0
    added = sheet.add_items(chosen)
    skipped = len(set(chosen)) - len(added)
    print(f"Added {len(added)} item(s)" + (f", {skipped} already in the sheet." if skipped else "."))
    return 0


def cmd_update(market: SteamMarket, sheet: PortfolioSheet, settings: Settings) -> int:
    fetcher = PriceFetcher(market, settings.currency, settings.price)
    report = update_prices(sheet, fetcher)
    log.info("Done: %s", report.summary())
    return 0 if not report.failed else 2


def cmd_watch(market: SteamMarket, sheet: PortfolioSheet, settings: Settings, interval_min: float) -> int:
    log.info("Updating every %g min. Press Ctrl+C to stop.", interval_min)
    fetcher = PriceFetcher(market, settings.currency, settings.price)
    try:
        while True:
            started = time.monotonic()
            try:
                report = update_prices(sheet, fetcher)
                log.info("Done: %s", report.summary())
            except CurrencyMismatch as e:
                if not fetcher.auto:
                    raise  # configured currency is unobtainable: a config problem
                log.error("Update skipped: %s", e)  # auto mode: the IP may switch back
            except TRANSIENT_ERRORS as e:
                log.error("Update failed, will retry next round: %s", e)
            time.sleep(max(0.0, interval_min * 60 - (time.monotonic() - started)))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


def menu(market: SteamMarket, sheet: PortfolioSheet, settings: Settings) -> int:
    # 1-3 keep the numbering of the original script.
    actions = {
        "1": ("Add items to the sheet", lambda: cmd_add(market, sheet, [], containers_only=True, assume_yes=False)),
        "2": (f"Start tracking prices (every {settings.interval_minutes:g} min)",
              lambda: cmd_watch(market, sheet, settings, settings.interval_minutes)),
        "3": ("Exit", None),
        "4": ("Update prices once", lambda: cmd_update(market, sheet, settings)),
    }
    while True:
        print()
        for key, (label, _) in actions.items():
            print(f"{key}. {label}")
        try:
            choice = input(f"Choose (1-{len(actions)}): ").strip()
        except EOFError:
            return 0
        if choice not in actions:
            print("Invalid choice.")
            continue
        action = actions[choice][1]
        if action is None:
            return 0
        try:
            action()
        except KeyboardInterrupt:
            print("\nCancelled.")
        except EOFError:
            return 0
        except (CurrencyMismatch, *TRANSIENT_ERRORS) as e:
            log.error("%s", e)


# -- prompts ------------------------------------------------------------------

def _search(market: SteamMarket, query: str, containers_only: bool) -> list[SearchResult]:
    results = market.search(query, containers_only=containers_only)
    if not results and containers_only:
        # Not a case/capsule? Try all items before giving up.
        results = market.search(query, containers_only=False)
    return results


def _prompt_queries():
    print("Type an item name, e.g. 'breakout case'. Empty line or 'exit' to finish.")
    while True:
        try:
            query = input("Search: ").strip()
        except EOFError:
            return
        if not query or query.lower() == "exit":
            return
        yield query


def _print_results(results: list[SearchResult]) -> None:
    for i, r in enumerate(results, 1):
        price = f"${r.sell_price_usd:.2f}" if r.sell_price_usd is not None else "–"
        print(f"  {i:>2}. {r.hash_name}  ({price}, {r.sell_listings} listed)")


def _choose(results: list[SearchResult]) -> SearchResult | None:
    _print_results(results)
    while True:
        answer = input(f"Pick 1-{len(results)} (Enter = 1, s = skip): ").strip().lower()
        if answer == "":
            return results[0]
        if answer in ("s", "n", "skip"):
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(results):
            return results[int(answer) - 1]
        print("Invalid choice.")


if __name__ == "__main__":
    sys.exit(main())
