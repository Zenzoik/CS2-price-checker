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
