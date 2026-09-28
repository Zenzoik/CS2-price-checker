"""SQLite storage: per-user holdings plus a shared item/price cache.

Money is stored as integer minor units (cents/kopecks) in the one currency the
database was created with.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items (
    hash_name TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    icon      TEXT
);
CREATE TABLE IF NOT EXISTS holdings (
    user_id    INTEGER NOT NULL,
    hash_name  TEXT NOT NULL,
    qty        INTEGER NOT NULL CHECK (qty > 0),
    buy_cents  INTEGER NOT NULL CHECK (buy_cents >= 0),
    added_at   REAL NOT NULL,
    PRIMARY KEY (user_id, hash_name)
);
CREATE TABLE IF NOT EXISTS prices (
    hash_name  TEXT PRIMARY KEY,
    cents      INTEGER,          -- NULL: no listings / buy orders, or never fetched
    updated_at REAL,             -- when `cents` was fetched
    checked_at REAL NOT NULL     -- last attempt, successful or not
);
"""


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
    buy_cents: int
    price_cents: int | None
    price_updated: float | None


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
        elif row["value"] != currency:
            raise StoreError(
                f"{path} holds prices in {row['value']}, but the configured currency is "
                f"{currency}. Use a new database file or set CS2BOT_CURRENCY={row['value']}."
            )
        self.currency = currency

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
                      p.cents AS price_cents, p.updated_at AS price_updated
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

    def add_lot(self, user_id: int, hash_name: str, qty: int, buy_cents: int) -> None:
        """Adds a purchase; an existing position gets the weighted average price."""
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO holdings VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, hash_name) DO UPDATE SET
                     buy_cents = (qty * buy_cents + excluded.qty * excluded.buy_cents
                                  + (qty + excluded.qty) / 2) / (qty + excluded.qty),
                     qty = qty + excluded.qty
                   WHERE qty + excluded.qty <= ?""",
                (user_id, hash_name, qty, buy_cents, time.time(), MAX_QTY),
            )
        if cur.rowcount == 0:
            raise QuantityLimit(f"at most {MAX_QTY} of one item")

    def set_holding(self, user_id: int, hash_name: str, qty: int, buy_cents: int) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE holdings SET qty = ?, buy_cents = ? WHERE user_id = ? AND hash_name = ?",
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

    def mark_checked(self, hash_name: str, at: float | None = None) -> None:
        """Records a failed attempt without touching the last known price."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO prices VALUES (?, NULL, NULL, ?) ON CONFLICT(hash_name) DO UPDATE SET "
                "checked_at = excluded.checked_at",
                (hash_name, time.time() if at is None else at),
            )
