import gspread
import pytest

from cs2tracker import cli, sheet as sheet_mod
from cs2tracker.sheet import SheetSetupError, _open_book
from cs2tracker.tracker import CurrencyMismatch
from fakes import FakeWorksheet


def test_common_options_work_after_the_subcommand(tmp_path):
    args = cli._parser().parse_args(["update", "-c", "x.toml", "-v"])
    assert (args.command, str(args.config), args.verbose) == ("update", "x.toml", True)
    args = cli._parser().parse_args(["-v", "-c", "y.toml", "watch", "-i", "10"])
    assert (args.verbose, str(args.config), args.interval) == (True, "y.toml", 10.0)
    args = cli._parser().parse_args(["update"])
    assert not hasattr(args, "verbose") and not hasattr(args, "config")


@pytest.mark.parametrize("value", ["nan", "inf", "0.5", "abc"])
def test_bad_interval_is_rejected(value):
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["watch", "-i", value])


class FakeClient:
    def __init__(self, by_key=None, by_title=None, by_url=None):
        self.by_key, self.by_title, self.by_url = by_key, by_title, by_url

    def _get(self, value):
        if isinstance(value, BaseException):
            raise value
        return value

    def open_by_key(self, key):
        return self._get(self.by_key)

    def open(self, title):
        return self._get(self.by_title)

    def open_by_url(self, url):
        return self._get(self.by_url)


def test_long_title_that_looks_like_a_key_falls_back_to_title():
    book = object()
    client = FakeClient(by_key=gspread.SpreadsheetNotFound(), by_title=book)
    assert _open_book(client, "Template_CS_2_cases_backup_2026") is book


def test_unshared_sheet_by_url_gives_sharing_hint():
    client = FakeClient(by_url=PermissionError())
    with pytest.raises(SheetSetupError, match="Share it"):
        _open_book(client, "https://docs.google.com/spreadsheets/d/abc/edit")


def test_missing_key_file_is_reported_without_traceback(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text('credentials = "nope.json"\n')
    assert cli.main(["update"]) == 1
    assert "key not found" in caplog.text


def test_malformed_key_file_is_reported(tmp_path, caplog):
    (tmp_path / "config.toml").write_text('credentials = "sa.json"\n')
    (tmp_path / "sa.json").write_text('{"installed": {}}')
    assert cli.main(["update", "-c", str(tmp_path / "config.toml")]) == 1
    assert "not a service account key" in caplog.text


def _run_menu(monkeypatch, inputs, **commands):
    feed = iter(inputs)

    def fake_input(prompt=""):
        try:
            return next(feed)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr("builtins.input", fake_input)
    for name, fn in commands.items():
        monkeypatch.setattr(cli, name, fn)
    return cli.menu(None, None, cli.Settings())


def test_menu_keeps_old_numbering_3_is_exit(monkeypatch):
    calls = []
    assert _run_menu(monkeypatch, ["3"], cmd_watch=lambda *a: calls.append("watch")) == 0
    assert calls == []


def test_menu_2_starts_tracking_and_eof_exits(monkeypatch):
    calls = []
    assert _run_menu(monkeypatch, ["2"], cmd_watch=lambda *a: calls.append("watch")) == 0
    assert calls == ["watch"]


def test_watch_survives_transient_errors_but_not_config_errors(monkeypatch):
    from google.auth.exceptions import TransportError
    outcomes = iter([TransportError("wifi"), CurrencyMismatch("ip changed"), KeyboardInterrupt()])

    def fake_update(sheet, fetcher):
        raise next(outcomes)

    monkeypatch.setattr(cli, "update_prices", fake_update)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    auto = cli.Settings(currency=None)
    assert cli.cmd_watch(None, None, auto, 5) == 0  # auto mode: ran until Ctrl+C

    # A fixed currency that Steam cannot deliver is a config error, even on a later round.
    outcomes = iter([TransportError("wifi"), CurrencyMismatch("cfg")])
    monkeypatch.setattr(cli, "update_prices", fake_update)
    with pytest.raises(CurrencyMismatch):
        cli.cmd_watch(None, None, cli.Settings(), 5)


def test_ctrl_c_wins_over_a_failing_salvage_write(monkeypatch):
    from cs2tracker.tracker import PriceFetcher, update_prices
    from fakes import FakeResponse, FakeSession, orderbook_body
    from cs2tracker.steam import SteamMarket
    import requests

    class BrokenWs(FakeWorksheet):
        def batch_update(self, data, value_input_option=None):
            raise requests.ConnectionError("offline")

    m = SteamMarket(FakeSession({"/orderbook": [FakeResponse(body=orderbook_body(currency=18)), KeyboardInterrupt()]}),
                    sleep=lambda s: None, clock=lambda: 0.0)
    s = sheet_mod.PortfolioSheet(BrokenWs(["Hash name", "A Case", "B Case"]))
    with pytest.raises(KeyboardInterrupt):
        update_prices(s, PriceFetcher(m, "UAH", "sell"))


def test_usage_errors_exit_1_not_2():
    with pytest.raises(SystemExit) as e:
        cli._parser().parse_args(["updte"])
    assert e.value.code == 1


def test_add_stops_on_exit_and_falls_back_to_all_items(monkeypatch):
    from cs2tracker.steam import SearchResult
    searched = []

    class Market:
        def search(self, query, containers_only=True, **kw):
            searched.append((query, containers_only))
            return [] if containers_only else [SearchResult("AWP | Asiimov (Field-Tested)", "x", 1.0, 5)]

    ws = FakeWorksheet(["Hash name"])
    inputs = iter(["asiimov", "1", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    cli.cmd_add(Market(), sheet_mod.PortfolioSheet(ws), [], containers_only=True, assume_yes=False)
    assert searched == [("asiimov", True), ("asiimov", False)]
    assert ws.update_calls[0][1] == [["AWP | Asiimov (Field-Tested)"]]
