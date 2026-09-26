from pathlib import Path

import pytest

from cs2tracker.config import ConfigError, Settings, load_settings
from cs2tracker.sheet import PortfolioSheet
from fakes import FakeWorksheet


def test_items_skip_header_and_blanks():
    s = PortfolioSheet(FakeWorksheet(["Hash name", " A Case ", "", "B Case"]))
    assert s.items() == [(2, "A Case"), (4, "B Case")]


def test_add_items_writes_below_last_name_and_dedupes():
    ws = FakeWorksheet(["Hash name", "A Case", "", "B Case"])
    added = PortfolioSheet(ws).add_items(["B Case", "C Case", "C Case", " D Case "])
    assert added == ["C Case", "D Case"]
    [(rng, values, _)] = ws.update_calls
    assert rng == "A5:A6" and values == [["C Case"], ["D Case"]]


def test_add_items_to_empty_sheet_starts_at_row_2():
    ws = FakeWorksheet([])
    PortfolioSheet(ws).add_items(["A Case"])
    assert ws.update_calls[0][0] == "A2:A2"


def test_add_items_grows_sheet_when_full():
    ws = FakeWorksheet(["Hash name", "A Case"], row_count=2)
    PortfolioSheet(ws).add_items(["B Case", "C Case"])
    assert ws.row_count == 4


def test_add_items_never_shrinks_a_grid_grown_in_the_browser():
    ws = FakeWorksheet(["Hash name", "A Case"], row_count=2)
    ws.real_row_count = 1000  # the user added rows after the sheet was opened
    PortfolioSheet(ws).add_items(["B Case"])
    assert ws.row_count == 2  # no resize call at all


def test_add_nothing_new_makes_no_write():
    ws = FakeWorksheet(["Hash name", "A Case"])
    assert PortfolioSheet(ws).add_items(["A Case"]) == []
    assert ws.update_calls == []


def test_defaults_when_no_config(tmp_path):
    assert load_settings(search_dirs=[tmp_path]) == Settings()


def test_config_and_key_found_next_to_main_py_when_run_from_elsewhere(tmp_path):
    project, elsewhere = tmp_path / "project", tmp_path / "cwd"
    project.mkdir(); elsewhere.mkdir()
    (project / "service-account.json").write_text("{}")
    assert load_settings(search_dirs=[elsewhere, project]).credentials == project / "service-account.json"
    (project / "config.toml").write_text('price = "buy"\n')
    s = load_settings(search_dirs=[elsewhere, project])
    assert s.price == "buy" and s.credentials == project / "service-account.json"


def test_config_with_bom_and_default_key_relative_to_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_bytes('\ufeffworksheet = "Кейси"\n'.encode("utf-8"))
    s = load_settings(cfg)
    assert s.worksheet == "Кейси" and s.credentials == tmp_path / "service-account.json"


def test_non_utf8_config_is_a_config_error(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_bytes('worksheet = "Кейси"\n'.encode("cp1251"))
    with pytest.raises(ConfigError, match="UTF-8"):
        load_settings(cfg)


def test_load_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('credentials = "keys/sa.json"\ncurrency = "auto"\nprice = "BUY"\nworksheet = "Cases"\n')
    s = load_settings(cfg)
    assert s.credentials == tmp_path / "keys/sa.json"
    assert (s.currency, s.price, s.worksheet) == (None, "buy", "Cases")


@pytest.mark.parametrize("body, message", [
    ('currenci = "UAH"', "unknown config keys"),
    ('currency = "XYZ"', "unsupported currency"),
    ('price = "median"', "price must be"),
    ("interval_minutes = 0", "interval_minutes"),
    ("worksheet = true", "worksheet"),
    ("worksheet = -1", "worksheet"),
    ("interval_minutes = nan", "interval_minutes"),
    ("request_delay = inf", "request_delay"),
    ("interval_minutes = 1e300", "interval_minutes"),
    ("spreadsheet = 5", "spreadsheet must be a str"),
    ("not toml", "config.toml"),
])
def test_invalid_config(tmp_path, body, message):
    cfg = tmp_path / "config.toml"
    cfg.write_text(body)
    with pytest.raises(ConfigError, match=message):
        load_settings(cfg)


def test_missing_explicit_config_is_an_error(tmp_path):
    with pytest.raises(ConfigError):
        load_settings(Path(tmp_path / "nope.toml"))


def test_config_without_credentials_falls_back_to_key_next_to_main_py(tmp_path, monkeypatch):
    import cs2tracker.config as config
    project, cfg_dir = tmp_path / "project", tmp_path / "cfg"
    project.mkdir(); cfg_dir.mkdir()
    (project / "service-account.json").write_text("{}")
    (cfg_dir / "config.toml").write_text('price = "buy"\n')
    monkeypatch.setattr(config, "PROJECT_DIR", project)
    assert load_settings(cfg_dir / "config.toml").credentials == project / "service-account.json"


def test_custom_columns_and_dry_run():
    ws = FakeWorksheet(["Hash name", "A Case", "B Case"])
    s = PortfolioSheet(ws, name_col=1, price_col=4)
    s.write_prices({"A Case": 1.0})
    assert ws.batch_calls[0][0] == [{"range": "D2", "values": [[1.0]]}]
    dry = PortfolioSheet(FakeWorksheet(["Hash name", "A Case"]), dry_run=True)
    assert dry.write_prices({"A Case": 1.0}) == 1 and dry.ws.batch_calls == []
    assert dry.add_items(["C Case"]) == ["C Case"] and dry.ws.update_calls == []


def test_column_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('name_column = "b"\nprice_column = "D"\n')
    s = load_settings(cfg)
    assert (s.name_col, s.price_col) == (2, 4)
    for bad in ('name_column = "1"', 'price_column = "A"', 'name_column = "Б"'):
        cfg.write_text(bad)
        with pytest.raises(ConfigError):
            load_settings(cfg)
