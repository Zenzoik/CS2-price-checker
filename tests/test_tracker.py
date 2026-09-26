from datetime import datetime

import pytest

from cs2tracker.sheet import PortfolioSheet
from cs2tracker.steam import SteamMarket
from cs2tracker.tracker import CurrencyMismatch, PriceFetcher, update_prices
from fakes import FakeResponse, FakeSession, FakeWorksheet, orderbook_body

NOW = datetime(2026, 9, 26, 12, 0, 0)


def market(routes):
    return SteamMarket(FakeSession(routes), sleep=lambda s: None, clock=lambda: 0.0)


def sheet(names, history=False):
    return PortfolioSheet(FakeWorksheet(["Hash name", *names]), FakeWorksheet([]) if history else None)


def written(portfolio):
    [(data, _)] = portfolio.ws.batch_calls
    return {d["range"]: d["values"][0][0] for d in data}


def test_orderbook_in_matching_currency_writes_prices_in_one_batch():
    m = market({"/orderbook": [FakeResponse(body=orderbook_body(buy=42100, sell=46400, currency=18))]})
    s = sheet(["A Case", "", "B Case"], history=True)
    report = update_prices(s, PriceFetcher(m, "UAH", "buy"), now=NOW)
    assert written(s) == {"C2": 421.0, "C4": 421.0}
    assert report.currency == "UAH" and not report.failed
    assert s.history.appended == [["2026-09-26 12:00:00", "A Case", 421.0, "UAH"],
                                  ["2026-09-26 12:00:00", "B Case", 421.0, "UAH"]]


def test_currency_mismatch_falls_back_to_priceoverview_for_sell():
    m = market({
        "/orderbook": [FakeResponse(body=orderbook_body(currency=3))],
        "/priceoverview": [FakeResponse(body={"success": True, "lowest_price": "487₴"})],
    })
    s = sheet(["A Case", "B Case"])
    report = update_prices(s, PriceFetcher(m, "UAH", "sell"))
    assert written(s) == {"C2": 487.0, "C3": 487.0}
    assert report.currency == "UAH"
    # The order book is probed once, then skipped for the rest of the run.
    assert [u for u, _ in m.session.calls].count("https://steamcommunity.com/market/orderbook") == 1


def test_currency_mismatch_for_buy_prices_aborts_without_writing():
    m = market({"/orderbook": [FakeResponse(body=orderbook_body(currency=3))]})
    s = sheet(["A Case"])
    with pytest.raises(CurrencyMismatch):
        update_prices(s, PriceFetcher(m, "UAH", "buy"))
    assert s.ws.batch_calls == []


def test_auto_currency_accepts_whatever_steam_returns():
    m = market({"/orderbook": [FakeResponse(body=orderbook_body(sell=909, currency=3))]})
    s = sheet(["A Case"])
    report = update_prices(s, PriceFetcher(m, None, "sell"))
    assert written(s) == {"C2": 9.09} and report.currency == "EUR"


def test_failures_are_reported_and_other_rows_still_written():
    m = market({"/orderbook": [
        FakeResponse(body={"data": {"success": False}}),
        FakeResponse(body=orderbook_body(buy=0, n_buy=0, currency=18)),
        FakeResponse(body=orderbook_body(buy=500, currency=18)),
    ]})
    s = sheet(["Typo Case", "Dead Case", "Good Case"])
    report = update_prices(s, PriceFetcher(m, "UAH", "buy"))
    assert written(s) == {"C4": 5.0}
    assert set(report.failed) == {"Typo Case", "Dead Case"}


def test_rate_limit_stops_run_but_keeps_what_was_fetched():
    m = SteamMarket(FakeSession({"/orderbook": [
        FakeResponse(body=orderbook_body(sell=100, currency=18)),
        FakeResponse(429, "null"),
    ]}), sleep=lambda s: None, clock=lambda: 0.0, max_retries=1)
    s = sheet(["A Case", "B Case", "C Case"])
    report = update_prices(s, PriceFetcher(m, "UAH", "sell"))
    assert written(s) == {"C2": 1.0}
    assert report.stopped == "Steam rate limit"
    assert "B Case" in report.failed and "C Case" not in report.failed


def test_duplicate_names_are_fetched_once():
    m = market({"/orderbook": [FakeResponse(body=orderbook_body(sell=100, currency=18))]})
    s = sheet(["A Case", "A Case"])
    update_prices(s, PriceFetcher(m, "UAH", "sell"))
    assert len(m.session.calls) == 1
    assert written(s) == {"C2": 1.0, "C3": 1.0}


def test_empty_sheet_does_nothing():
    m = market({})
    s = sheet([])
    report = update_prices(s, PriceFetcher(m, "UAH", "sell"))
    assert report.prices == {} and s.ws.batch_calls == []


def test_auto_currency_is_pinned_so_currencies_never_mix():
    m = market({
        "/orderbook": [FakeResponse(body=orderbook_body(sell=909, currency=3)),
                       FakeResponse(body=orderbook_body(sell=40000, currency=18))],
        "/priceoverview": [FakeResponse(body={"success": True, "lowest_price": "9,50€"})],
    })
    s = sheet(["A Case", "B Case"], history=True)
    fetcher = PriceFetcher(m, None, "sell")
    report = update_prices(s, fetcher, now=NOW)
    assert written(s) == {"C2": 9.09, "C3": 9.5}
    assert report.currency == "EUR"
    assert {row[3] for row in s.history.appended} == {"EUR"}


def test_auto_currency_stays_pinned_across_watch_cycles_and_orderbook_is_reprobed():
    m = market({"/orderbook": [FakeResponse(body=orderbook_body(currency=3)),
                               FakeResponse(body=orderbook_body(currency=18)),
                               FakeResponse(body=orderbook_body(currency=3))],
                "/priceoverview": [FakeResponse(body={"success": True, "lowest_price": "1,00€"})]})
    fetcher = PriceFetcher(m, None, "sell")
    update_prices(sheet(["A Case"]), fetcher)  # pins EUR
    update_prices(sheet(["A Case"]), fetcher)  # IP now UAH -> priceoverview in EUR
    s = sheet(["A Case"])
    update_prices(s, fetcher)                  # IP back to EUR -> order book again
    assert written(s) == {"C2": 9.09}
    sources = [u.rsplit("/", 2)[-2] if u.endswith("/") else u.rsplit("/", 1)[-1] for u, _ in m.session.calls]
    assert sources == ["orderbook", "orderbook", "priceoverview", "orderbook"]


def test_currency_change_mid_run_keeps_what_was_fetched():
    m = market({"/orderbook": [FakeResponse(body=orderbook_body(buy=100, currency=18)),
                               FakeResponse(body=orderbook_body(buy=100, currency=3))]})
    s = sheet(["A Case", "B Case", "C Case"])
    report = update_prices(s, PriceFetcher(m, "UAH", "buy"))
    assert written(s) == {"C2": 1.0}
    assert report.stopped == "currency changed mid-run"


def test_malformed_and_transport_failures_are_per_item():
    import requests
    m = SteamMarket(FakeSession({"/orderbook": [
        FakeResponse(body=orderbook_body(sell=100, currency=18)),
        FakeResponse(body={"data": "maintenance"}),
        requests.exceptions.ChunkedEncodingError("IncompleteRead"),
        FakeResponse(body=orderbook_body(sell=300, currency=18)),
        FakeResponse(body={"data": {"success": True, "data": {"amtMinSellOrder": 1}}}),
    ]}), sleep=lambda s: None, clock=lambda: 0.0, server_retries=0)
    s = sheet(["A Case", "B Case", "C Case", "D Case", "E Case"])
    report = update_prices(s, PriceFetcher(m, "UAH", "sell"))
    assert written(s) == {"C2": 1.0, "C5": 3.0}
    assert set(report.failed) == {"B Case", "C Case", "E Case"}
    assert report.stopped is None


def test_steam_down_stops_the_pass_quickly():
    m = SteamMarket(FakeSession({"/orderbook": [FakeResponse(502, "")]}),
                    sleep=lambda s: None, clock=lambda: 0.0)
    s = sheet([f"Case {i}" for i in range(20)])
    report = update_prices(s, PriceFetcher(m, "UAH", "sell"))
    assert "3 Steam errors in a row" in report.stopped
    assert len(m.session.calls) == 3 * (1 + m.server_retries)
    assert s.ws.batch_calls == []


class SortingWorksheet(FakeWorksheet):
    """Simulates the user re-sorting the sheet while prices are being fetched."""

    def __init__(self, column_a):
        super().__init__(column_a)
        self.reads = 0

    def col_values(self, col):
        self.reads += 1
        if self.reads == 2:
            self.column_a = [self.column_a[0], *reversed(self.column_a[1:])]
        return super().col_values(col)


def test_prices_follow_their_items_when_rows_move_during_a_run():
    m = market({"/orderbook": [FakeResponse(body=orderbook_body(sell=100, currency=18)),
                               FakeResponse(body=orderbook_body(sell=200, currency=18))]})
    s = PortfolioSheet(SortingWorksheet(["Hash name", "A Case", "B Case"]))
    update_prices(s, PriceFetcher(m, "UAH", "sell"))
    assert written(s) == {"C2": 2.0, "C3": 1.0}  # B is now row 2, A row 3


def test_ctrl_c_still_writes_prices_fetched_so_far():
    m = market({"/orderbook": [FakeResponse(body=orderbook_body(sell=100, currency=18)), KeyboardInterrupt()]})
    s = sheet(["A Case", "B Case"])
    with pytest.raises(KeyboardInterrupt):
        update_prices(s, PriceFetcher(m, "UAH", "sell"))
    assert written(s) == {"C2": 1.0}


def test_auto_mode_mismatch_message_does_not_suggest_auto():
    m = market({"/orderbook": [FakeResponse(body=orderbook_body(buy=100, currency=3)),
                               FakeResponse(body=orderbook_body(buy=100, currency=18))]})
    fetcher = PriceFetcher(m, None, "buy")
    update_prices(sheet(["A Case"]), fetcher)
    with pytest.raises(CurrencyMismatch, match="changed from EUR to UAH"):
        update_prices(sheet(["A Case"]), fetcher)


def test_not_found_items_do_not_count_towards_the_error_streak():
    m = SteamMarket(FakeSession({"/orderbook": [
        FakeResponse(502, ""), FakeResponse(body={"data": {"success": False}}),
        FakeResponse(502, ""), FakeResponse(body={"data": {"success": False}}),
        FakeResponse(502, ""), FakeResponse(body=orderbook_body(sell=100, currency=18)),
    ]}), sleep=lambda s: None, clock=lambda: 0.0, server_retries=0)
    s = sheet(["A", "B", "C", "D", "E", "F"])
    report = update_prices(s, PriceFetcher(m, "UAH", "sell"))
    assert report.stopped is None and written(s) == {"C7": 1.0}
