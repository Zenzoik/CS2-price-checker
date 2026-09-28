"""SQLite storage: per-user holdings plus a shared item/price cache.

Money is stored as integer minor units (cents/kopecks) in the one currency the
database was created with.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

HOLDINGS_DDL = """
CREATE TABLE IF NOT EXISTS holdings (
    user_id       INTEGER NOT NULL,
    hash_name     TEXT NOT NULL,
    qty           INTEGER NOT NULL CHECK (qty > 0),
    buy_cents     INTEGER CHECK (buy_cents >= 0),  -- NULL: price paid unknown
    added_at      REAL NOT NULL,
    -- 1: take the first market price we get as the price paid (imports)
    buy_at_market INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, hash_name)
);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items (
    hash_name TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    icon      TEXT
);
{holdings}
-- Usage, for the admin's statistics screen.
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT,
    first_name TEXT,
    language   TEXT,
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL,
    via        TEXT NOT NULL      -- where we first met them: 'app' or 'bot'
);
CREATE TABLE IF NOT EXISTS events (
    ts      REAL NOT NULL,
    user_id INTEGER NOT NULL,
    kind    TEXT NOT NULL,        -- open, search, add, edit, remove, inventory, import, bot
    n       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE TABLE IF NOT EXISTS prices (
    hash_name  TEXT PRIMARY KEY,
    cents      INTEGER,          -- NULL: no listings / buy orders, or never fetched
    updated_at REAL,             -- when `cents` was fetched
    checked_at REAL NOT NULL     -- last attempt, successful or not
);
""".format(holdings=HOLDINGS_DDL)


SCHEMA_VERSION = 2

# A CS2 seller gets the buyer's price minus 5% Steam + 10% game fee.
STEAM_FEE_PERCENT = 15


def net_cents(cents: int) -> int:
    """What selling at `cents` actually brings."""
    return cents * 100 // (100 + STEAM_FEE_PERCENT)


# Keeps qty * buy_cents far below SQLite's int64 limit (1e6 * 1e10 = 1e16).
MAX_QTY = 1_000_000


class StoreError(Exception):
    pass


class QuantityLimit(StoreError):
    """The position would exceed MAX_QTY."""


@dataclass(frozen=True)
class Holding:
    hash_name: str
    name: str
    icon: str | None
    qty: int
    buy_cents: int | None
    price_cents: int | None
    price_updated: float | None
    price_checked: float | None = None  # None: never tried yet (e.g. just imported)


class Store:
    def __init__(self, path: Path | str, currency: str):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'currency'").fetchone()
        if row is None:
            with self.conn:
                self.conn.execute("INSERT INTO meta VALUES ('currency', ?)", (currency,))
                self.conn.execute("INSERT INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
        elif row["value"] != currency:
            raise StoreError(
                f"{path} holds prices in {row['value']}, but the configured currency is "
                f"{currency}. Use a new database file or set CS2BOT_CURRENCY={row['value']}."
            )
        else:
            self._migrate()
        self.currency = currency

    def _migrate(self) -> None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
        version = int(row["value"]) if row else 1
        if version < 2:
            # v1 required a price paid; SQLite cannot drop NOT NULL in place.
            # One transaction (no executescript: it would commit halfway).
            with self.conn:
                self.conn.execute("BEGIN")  # sqlite3 would otherwise autocommit the DDL
                self.conn.execute("ALTER TABLE holdings RENAME TO holdings_v1")
                self.conn.execute(HOLDINGS_DDL)
                self.conn.execute(
                    "INSERT INTO holdings (user_id, hash_name, qty, buy_cents, added_at) "
                    "SELECT user_id, hash_name, qty, buy_cents, added_at FROM holdings_v1"
                )
                self.conn.execute("DROP TABLE holdings_v1")
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))

    def close(self) -> None:
        self.conn.close()

    # -- usage ---------------------------------------------------------------

    def touch_user(self, user: dict, via: str, at: float | None = None) -> None:
        """Records a Telegram user (from signed init data or a bot update)."""
        at = time.time() if at is None else at
        text = lambda key: str(user[key])[:64] if user.get(key) else None  # noqa: E731
        with self.conn:
            self.conn.execute(
                """INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     username = COALESCE(excluded.username, username),
                     first_name = COALESCE(excluded.first_name, first_name),
                     language = COALESCE(excluded.language, language),
                     last_seen = MAX(last_seen, excluded.last_seen)""",
                (int(user["id"]), text("username"), text("first_name"), text("language_code"), at, at, via),
            )

    def log_event(self, user_id: int, kind: str, n: int = 1, at: float | None = None) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO events VALUES (?, ?, ?, ?)",
                              (time.time() if at is None else at, user_id, kind, n))

    def prune_events(self, before: float) -> int:
        with self.conn:
            return self.conn.execute("DELETE FROM events WHERE ts < ?", (before,)).rowcount

    def stats(self, now: float | None = None, days: int = 14) -> dict:
        """Usage numbers for the admin screen. Days are UTC calendar days."""
        now = time.time() if now is None else now
        one = lambda sql, *args: self.conn.execute(sql, args).fetchone()[0]  # noqa: E731
        day = 86400
        users = {
            "total": one("SELECT COUNT(*) FROM users"),
            "with_portfolio": one("SELECT COUNT(DISTINCT user_id) FROM holdings"),
            "active_1d": one("SELECT COUNT(*) FROM users WHERE last_seen >= ?", now - day),
            "active_7d": one("SELECT COUNT(*) FROM users WHERE last_seen >= ?", now - 7 * day),
            "active_30d": one("SELECT COUNT(*) FROM users WHERE last_seen >= ?", now - 30 * day),
            "new_7d": one("SELECT COUNT(*) FROM users WHERE first_seen >= ?", now - 7 * day),
            "bot_only": one("SELECT COUNT(*) FROM users u WHERE NOT EXISTS "
                            "(SELECT 1 FROM events e WHERE e.user_id = u.user_id AND e.kind != 'bot')"),
        }
        start = (int(now // day) - days + 1) * day
        active = dict(self.conn.execute(
            "SELECT CAST(ts / 86400 AS INTEGER), COUNT(DISTINCT user_id) FROM events "
            "WHERE ts >= ? GROUP BY 1", (start,)).fetchall())
        new = dict(self.conn.execute(
            "SELECT CAST(first_seen / 86400 AS INTEGER), COUNT(*) FROM users WHERE first_seen >= ? GROUP BY 1",
            (start,)).fetchall())
        daily = [{"day": d * day, "active": active.get(d, 0), "new": new.get(d, 0)}
                 for d in range(int(start // day), int(now // day) + 1)]
        actions = dict(self.conn.execute(
            "SELECT kind, SUM(n) FROM events WHERE ts >= ? GROUP BY kind", (now - 7 * day,)).fetchall())
        top = [
            {"hash_name": r[0], "name": r[1], "holders": r[2], "qty": r[3]}
            for r in self.conn.execute(
                """SELECT h.hash_name, COALESCE(i.name, h.hash_name), COUNT(*), SUM(h.qty)
                   FROM holdings h LEFT JOIN items i ON i.hash_name = h.hash_name
                   GROUP BY h.hash_name ORDER BY 3 DESC, 4 DESC LIMIT 5""")
        ]
        recent = [
            {"id": r[0], "username": r[1], "first_name": r[2], "first_seen": r[3], "last_seen": r[4], "items": r[5]}
            for r in self.conn.execute(
                """SELECT u.user_id, u.username, u.first_name, u.first_seen, u.last_seen,
                          (SELECT COUNT(*) FROM holdings h WHERE h.user_id = u.user_id)
                   FROM users u ORDER BY u.last_seen DESC LIMIT 10""")
        ]
        prices = {
            "tracked": one("SELECT COUNT(DISTINCT hash_name) FROM holdings"),
            "pending": one("SELECT COUNT(DISTINCT h.hash_name) FROM holdings h "
                           "LEFT JOIN prices p ON p.hash_name = h.hash_name WHERE p.checked_at IS NULL"),
            "last_check": one("SELECT MAX(checked_at) FROM prices"),
            "oldest_price": one("SELECT MIN(p.updated_at) FROM holdings h JOIN prices p ON p.hash_name = h.hash_name"),
        }
        return {"users": users, "daily": daily, "actions_7d": actions, "top_items": top,
                "recent_users": recent, "prices": prices,
                "holdings": {"rows": one("SELECT COUNT(*) FROM holdings"),
                             "qty": one("SELECT COALESCE(SUM(qty), 0) FROM holdings")}}

    # -- item catalogue ------------------------------------------------------

    def remember_items(self, items: list[tuple[str, str, str | None]]) -> None:
        """(hash name, display name, icon) from Steam search results."""
        with self.conn:
            self.conn.executemany(
                "INSERT INTO items VALUES (?, ?, ?) ON CONFLICT(hash_name) DO UPDATE SET "
                "name = excluded.name, icon = COALESCE(excluded.icon, items.icon)",
                items,
            )

    def item(self, hash_name: str) -> tuple[str, str | None] | None:
        row = self.conn.execute("SELECT name, icon FROM items WHERE hash_name = ?", (hash_name,)).fetchone()
        return (row["name"], row["icon"]) if row else None

    # -- holdings ------------------------------------------------------------

    def holdings(self, user_id: int) -> list[Holding]:
        rows = self.conn.execute(
            """SELECT h.hash_name, COALESCE(i.name, h.hash_name) AS name, i.icon, h.qty, h.buy_cents,
                      p.cents AS price_cents, p.updated_at AS price_updated, p.checked_at AS price_checked
               FROM holdings h
               LEFT JOIN items i ON i.hash_name = h.hash_name
               LEFT JOIN prices p ON p.hash_name = h.hash_name
               WHERE h.user_id = ?
               ORDER BY h.added_at""",
            (user_id,),
        ).fetchall()
        return [Holding(**dict(r)) for r in rows]

    def holding(self, user_id: int, hash_name: str) -> Holding | None:
        return next((h for h in self.holdings(user_id) if h.hash_name == hash_name), None)

    def count(self, user_id: int) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM holdings WHERE user_id = ?", (user_id,)).fetchone()[0]

    def add_lot(self, user_id: int, hash_name: str, qty: int, buy_cents: int | None) -> None:
        """Adds a purchase; an existing position gets the weighted average price.

        If either side's price is unknown the average is unknown too (NULL).
        """
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO holdings (user_id, hash_name, qty, buy_cents, added_at) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, hash_name) DO UPDATE SET
                     buy_cents = (qty * buy_cents + excluded.qty * excluded.buy_cents
                                  + (qty + excluded.qty) / 2) / (qty + excluded.qty),
                     qty = qty + excluded.qty,
                     buy_at_market = 0
                   WHERE qty + excluded.qty <= ?""",
                (user_id, hash_name, qty, buy_cents, time.time(), MAX_QTY),
            )
        if cur.rowcount == 0:
            raise QuantityLimit(f"at most {MAX_QTY} of one item")

    def import_items(self, user_id: int, rows: list[tuple[str, int, int | None, bool]]) -> int:
        """(hash name, qty, buy cents or None, take first market price); held items are skipped."""
        now = time.time()
        with self.conn:
            cur = self.conn.executemany(
                "INSERT OR IGNORE INTO holdings (user_id, hash_name, qty, buy_cents, added_at, buy_at_market) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(user_id, name, min(qty, MAX_QTY), buy, now, int(at_market and buy is None))
                 for name, qty, buy, at_market in rows],
            )
        return cur.rowcount

    def set_holding(self, user_id: int, hash_name: str, qty: int, buy_cents: int | None) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE holdings SET qty = ?, buy_cents = ?, buy_at_market = 0 WHERE user_id = ? AND hash_name = ?",
                (qty, buy_cents, user_id, hash_name),
            )
        return cur.rowcount > 0

    def delete_holding(self, user_id: int, hash_name: str) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM holdings WHERE user_id = ? AND hash_name = ?", (user_id, hash_name)
            )
        return cur.rowcount > 0

    # -- prices --------------------------------------------------------------

    def tracked(self) -> list[tuple[str, float | None]]:
        """Every held item with its last price check (None = never), oldest first."""
        rows = self.conn.execute(
            """SELECT DISTINCT h.hash_name, p.checked_at FROM holdings h
               LEFT JOIN prices p ON p.hash_name = h.hash_name
               ORDER BY p.checked_at IS NOT NULL, p.checked_at"""
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def price(self, hash_name: str) -> tuple[int | None, float | None] | None:
        row = self.conn.execute(
            "SELECT cents, updated_at FROM prices WHERE hash_name = ?", (hash_name,)
        ).fetchone()
        return (row["cents"], row["updated_at"]) if row else None

    def set_price(self, hash_name: str, cents: int | None, at: float | None = None) -> None:
        at = time.time() if at is None else at
        with self.conn:
            self.conn.execute(
                "INSERT INTO prices VALUES (?, ?, ?, ?) ON CONFLICT(hash_name) DO UPDATE SET "
                "cents = excluded.cents, updated_at = excluded.updated_at, checked_at = excluded.checked_at",
                (hash_name, cents, at, at),
            )
            if cents is not None:
                # Imports that asked for "today's price" as the price paid: net of the
                # fee, so their profit starts at zero, like the values it is compared to.
                self.conn.execute(
                    "UPDATE holdings SET buy_cents = ?, buy_at_market = 0 WHERE hash_name = ? AND buy_at_market = 1",
                    (net_cents(cents), hash_name),
                )

    def mark_checked(self, hash_name: str, at: float | None = None) -> None:
        """Records a failed attempt without touching the last known price."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO prices VALUES (?, NULL, NULL, ?) ON CONFLICT(hash_name) DO UPDATE SET "
                "checked_at = excluded.checked_at",
                (hash_name, time.time() if at is None else at),
            )
