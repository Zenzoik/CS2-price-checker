"""Settings from an optional TOML file; defaults match the original script."""

from __future__ import annotations

import logging
import math
import sys
from dataclasses import dataclass, fields, replace
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from gspread.utils import a1_to_rowcol

from .steam import ISO_TO_STEAM

log = logging.getLogger(__name__)

CONFIG_NAME = "config.toml"
# The folder with main.py – so double-clicking it or running it from cron without
# `cd` still finds config.toml / service-account.json next to it.
PROJECT_DIR = Path(__file__).resolve().parent.parent
PRICE_KINDS = ("buy", "sell")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    credentials: Path = Path("service-account.json")
    spreadsheet: str = "Template_CS_2_cases"
    worksheet: str | int = 0
    # ISO code, or None for "whatever Steam uses for this IP" (config value "auto").
    currency: str | None = "UAH"
    # "buy": highest buy order (what you get selling right now);
    # "sell": lowest listing (the "Starting at" price on the market page).
    price: str = "sell"
    interval_minutes: float = 5.0
    request_delay: float = 1.5
    history_worksheet: str | None = None
    # Column letters: where the hash names are read from and prices written to.
    name_column: str = "A"
    price_column: str = "C"

    @property
    def name_col(self) -> int:
        return a1_to_rowcol(f"{self.name_column}1")[1]

    @property
    def price_col(self) -> int:
        return a1_to_rowcol(f"{self.price_column}1")[1]


def load_settings(path: Path | None = None, search_dirs: list[Path] | None = None) -> Settings:
    """Loads `path`, or the first config.toml in the current dir / project dir.

    Relative paths inside the config are relative to the config file. Without any
    config file, service-account.json is looked up in the same places.
    """
    dirs = search_dirs if search_dirs is not None else _unique([Path.cwd(), PROJECT_DIR])
    if path is not None:
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
    else:
        path = next((d / CONFIG_NAME for d in dirs if (d / CONFIG_NAME).is_file()), None)
    if path is None:
        default = Settings().credentials
        found = next((d / default for d in dirs if (d / default).is_file()), None)
        return Settings(credentials=found or default)
    log.debug("Using config %s", path)
    try:
        text = path.read_text(encoding="utf-8-sig")  # tolerate a BOM from Notepad/PowerShell
        data = tomllib.loads(text)
    except UnicodeDecodeError as e:
        raise ConfigError(f"{path}: save the file as UTF-8 ({e})") from e
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    return _from_dict(data, base_dir=path.parent)


def _unique(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.resolve() not in [o.resolve() for o in out]:
            out.append(p)
    return out


def _from_dict(data: dict, base_dir: Path) -> Settings:
    known = {f.name for f in fields(Settings)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"unknown config keys: {', '.join(unknown)}")
    s = Settings()
    if "credentials" in data:
        cred = Path(_expect(data, "credentials", str)).expanduser()
        cred = cred if cred.is_absolute() else base_dir / cred
    else:
        # Default key: next to the config, else next to main.py.
        cred = base_dir / s.credentials
        if not cred.is_file() and (PROJECT_DIR / s.credentials).is_file():
            cred = PROJECT_DIR / s.credentials
    changes: dict = {"credentials": cred}

    if "spreadsheet" in data:
        changes["spreadsheet"] = _expect(data, "spreadsheet", str).strip()
        if not changes["spreadsheet"]:
            raise ConfigError("spreadsheet must not be empty")
    if "worksheet" in data:
        ws = data["worksheet"]
        if isinstance(ws, bool) or not isinstance(ws, (int, str)) or (isinstance(ws, int) and ws < 0):
            raise ConfigError("worksheet must be a sheet index (0 = first) or a tab name")
        changes["worksheet"] = ws
    if "currency" in data:
        cur = _expect(data, "currency", str).strip().upper()
        if cur == "AUTO":
            changes["currency"] = None
        elif cur in ISO_TO_STEAM:
            changes["currency"] = cur
        else:
            raise ConfigError(f"unsupported currency {cur!r}; use 'auto' or one of: {', '.join(sorted(ISO_TO_STEAM))}")
    if "price" in data:
        kind = _expect(data, "price", str).strip().lower()
        if kind not in PRICE_KINDS:
            raise ConfigError(f"price must be one of {PRICE_KINDS}, got {kind!r}")
        changes["price"] = kind
    for key, minimum, maximum in (("interval_minutes", 1.0, 10080.0), ("request_delay", 0.5, 600.0)):
        if key in data:
            value = data[key]
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not minimum <= value <= maximum):
                raise ConfigError(f"{key} must be a number between {minimum:g} and {maximum:g}")
            changes[key] = float(value)
    for key in ("name_column", "price_column"):
        if key in data:
            col = _expect(data, key, str).strip().upper()
            if not col.isalpha() or not col.isascii() or len(col) > 3:
                raise ConfigError(f"{key} must be a column letter like \"B\", got {col!r}")
            changes[key] = col
    if changes.get("name_column", s.name_column) == changes.get("price_column", s.price_column):
        raise ConfigError("name_column and price_column must differ")
    if "history_worksheet" in data:
        changes["history_worksheet"] = _expect(data, "history_worksheet", str).strip() or None

    return replace(s, **changes)


def _expect(data: dict, key: str, typ: type):
    value = data[key]
    if not isinstance(value, typ):
        raise ConfigError(f"{key} must be a {typ.__name__}, got {type(value).__name__}")
    return value
