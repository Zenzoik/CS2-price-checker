"""Mini App settings, read from environment variables (e.g. a systemd EnvironmentFile)."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..steam import ISO_TO_STEAM

PRICE_KINDS = ("buy", "sell")


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class AppSettings:
    bot_token: str
    # Public HTTPS address the Mini App is served from, e.g. https://cs.example.com/
    public_url: str
    db_path: Path = Path("cs2tracker.db")
    host: str = "127.0.0.1"
    port: int = 8080
    currency: str = "UAH"
    # "sell": lowest listing (market "Starting at"); "buy": highest buy order.
    price: str = "sell"
    refresh_minutes: float = 10.0
    request_delay: float = 1.5
    # Telegram user ids allowed to use the app; empty = everyone.
    allowed_users: frozenset[int] = frozenset()
    max_items: int = 200

    def allows(self, user_id: int) -> bool:
        return not self.allowed_users or user_id in self.allowed_users


def load_app_settings(env: Mapping[str, str] | None = None) -> AppSettings:
    env = os.environ if env is None else env

    def get(key: str, default: str | None = None) -> str | None:
        value = env.get(f"CS2BOT_{key}", "").strip()
        return value or default

    token = get("TOKEN")
    if not token:
        raise SettingsError("CS2BOT_TOKEN is not set (the bot token from @BotFather)")
    url = get("URL")
    if not url or not url.startswith("https://"):
        raise SettingsError("CS2BOT_URL must be the public https:// address of the Mini App")

    currency = get("CURRENCY", "UAH").upper()
    if currency not in ISO_TO_STEAM:
        raise SettingsError(f"CS2BOT_CURRENCY: unsupported currency {currency!r}")
    price = get("PRICE", "sell").lower()
    if price not in PRICE_KINDS:
        raise SettingsError(f"CS2BOT_PRICE must be one of {PRICE_KINDS}")

    try:
        allowed = frozenset(int(x) for x in get("ALLOWED_USERS", "").replace(" ", "").split(",") if x)
    except ValueError as e:
        raise SettingsError("CS2BOT_ALLOWED_USERS must be comma-separated Telegram user ids") from e

    return AppSettings(
        bot_token=token,
        public_url=url,
        db_path=Path(get("DB", "cs2tracker.db")).expanduser(),
        host=get("HOST", "127.0.0.1"),
        port=int(_number(get, "PORT", 8080, 1, 65535)),
        currency=currency,
        price=price,
        refresh_minutes=_number(get, "REFRESH_MINUTES", 10, 1, 1440),
        request_delay=_number(get, "REQUEST_DELAY", 1.5, 0.5, 600),
        allowed_users=allowed,
        max_items=int(_number(get, "MAX_ITEMS", 200, 1, 10000)),
    )


def _number(get, key: str, default: float, lo: float, hi: float) -> float:
    raw = get(key)
    if raw is None:
        return float(default)
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or not lo <= value <= hi:
        raise SettingsError(f"CS2BOT_{key} must be a number between {lo:g} and {hi:g}")
    return value
