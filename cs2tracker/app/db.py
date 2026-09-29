"""SQLite storage: per-user holdings plus a shared item/price cache.

Money is stored as integer minor units (cents/kopecks) in the one currency the
database was created with.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
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
    folder_id     INTEGER,  -- NULL: in no folder
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
-- Per-user lookups (the digest offer, "only pressed /start" in the statistics)
-- would otherwise scan every event, on the event loop.
CREATE INDEX IF NOT EXISTS events_user ON events (user_id, kind);
CREATE TABLE IF NOT EXISTS prices (
    hash_name  TEXT PRIMARY KEY,
    cents      INTEGER,          -- NULL: no listings / buy orders, or never fetched
    updated_at REAL,             -- when `cents` was fetched
    checked_at REAL NOT NULL,    -- last attempt, successful or not
    buy_order_cents INTEGER,
    sell_order_cents INTEGER,
    buy_orders INTEGER,
    sell_listings INTEGER
);
-- "Notify me when ...": one condition each, delivered by the bot.
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    hash_name   TEXT,              -- NULL: the whole portfolio
    metric      TEXT NOT NULL,     -- price | profit (items), value_pct | value_amount (portfolio)
    above       INTEGER NOT NULL,  -- 1: met at or above the threshold, 0: at or below
    threshold   INTEGER NOT NULL,  -- signed: cents (price, value_amount) or basis points (profit, value_pct)
    baseline    INTEGER,           -- value_*: portfolio value (cents) it is measured from
    armed       INTEGER NOT NULL DEFAULT 1,  -- 0 once sent, until the value moves back
    created_at  REAL NOT NULL,
    fired_at    REAL,
    fired_value INTEGER
);
CREATE INDEX IF NOT EXISTS alerts_user ON alerts (user_id);
-- Items a user follows without owning them; refreshed like holdings.
CREATE TABLE IF NOT EXISTS watchlist (
    user_id   INTEGER NOT NULL,
    hash_name TEXT NOT NULL,
    added_at  REAL NOT NULL,
    PRIMARY KEY (user_id, hash_name)
);
CREATE TABLE IF NOT EXISTS prefs (
    user_id        INTEGER PRIMARY KEY,
    digest         TEXT NOT NULL DEFAULT 'off',  -- off | daily | weekly (Mondays)
    digest_hour    INTEGER NOT NULL DEFAULT 10,  -- local hour, in `tz`
    tz             TEXT,                          -- IANA zone from the app; NULL: UTC
    digest_sent    TEXT,                          -- local date of the last digest slot handled
    digest_offered INTEGER NOT NULL DEFAULT 0,
    write_access   INTEGER NOT NULL DEFAULT 0    -- the bot may message this user
);
-- Sold items, against the average price paid at the time (the basis of unrealized profit too).
CREATE TABLE IF NOT EXISTS sales (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    hash_name   TEXT NOT NULL,
    qty         INTEGER NOT NULL CHECK (qty > 0),
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),  -- received per item, after fees
    buy_cents   INTEGER CHECK (buy_cents >= 0),             -- NULL: price paid unknown
    sold_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sales_user ON sales (user_id, sold_at);
-- The Steam inventory a user last imported from, re-read once a day.
CREATE TABLE IF NOT EXISTS inventory_sync (
    user_id      INTEGER PRIMARY KEY,
    steamid      TEXT NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,
    snapshot     TEXT NOT NULL,              -- JSON {{hash name: qty}} as last seen
    pending_new  TEXT NOT NULL DEFAULT '[]', -- JSON [hash name]: appeared since, not reviewed yet
    pending_gone TEXT NOT NULL DEFAULT '{{}}', -- JSON {{hash name: qty}}: held items that left it
    checked_at   REAL NOT NULL,
    next_at      REAL NOT NULL,
    error        TEXT                        -- private | not_found; NULL when the last read worked
);
-- A user's own grouping of positions ("Main", "Alt", "Long-term"); one folder per position.
CREATE TABLE IF NOT EXISTS folders (
    id      INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name    TEXT NOT NULL,
    UNIQUE (user_id, name)
);
-- An admin's message to everyone the bot may write to; `cursor` makes it resumable.
CREATE TABLE IF NOT EXISTS broadcasts (
    id          INTEGER PRIMARY KEY,
    admin_id    INTEGER NOT NULL,
    text        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    total       INTEGER NOT NULL,
    cursor      INTEGER NOT NULL DEFAULT 0,  -- the last user id handled
    sent        INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    finished_at REAL,
    cancelled   INTEGER NOT NULL DEFAULT 0
);
""".format(holdings=HOLDINGS_DDL)

PRICE_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS price_history (
    hash_name TEXT NOT NULL,
    day       TEXT NOT NULL,  -- UTC calendar day, YYYY-MM-DD
    cents     INTEGER CHECK (cents >= 0),  -- NULL: fetched, but no listings / buy orders
    PRIMARY KEY (hash_name, day)
);
"""

HOLDING_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS holding_history (
    user_id   INTEGER NOT NULL,
    hash_name TEXT NOT NULL,
    day       TEXT NOT NULL,  -- UTC calendar day, YYYY-MM-DD
    qty       INTEGER NOT NULL CHECK (qty >= 0),
    changed   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, hash_name, day)
);
"""
HOLDING_HISTORY_INDEX = "CREATE INDEX IF NOT EXISTS holding_history_user_day ON holding_history (user_id, day)"

SCHEMA_VERSION = 9

# A CS2 seller gets the buyer's price minus 5% Steam + 10% game fee.
STEAM_FEE_PERCENT = 15


def net_cents(cents: int) -> int:
    """What selling at `cents` actually brings."""
    return cents * 100 // (100 + STEAM_FEE_PERCENT)


# Keeps qty * buy_cents far below SQLite's int64 limit (1e6 * 1e10 = 1e16).
MAX_QTY = 1_000_000
MAX_ALERTS = 20
MAX_WATCHED = 50
ITEM_METRICS = ("price", "profit")
PORTFOLIO_METRICS = ("value_pct", "value_amount")
DIGESTS = ("off", "daily", "weekly")
# An inventory is re-read at most this often per user.
SYNC_EVERY = 86400
MAX_FOLDERS = 10
MAX_FOLDER_NAME = 32


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
    yesterday_cents: int | None = None
    week_ago_cents: int | None = None
    buy_order_cents: int | None = None
    sell_order_cents: int | None = None
    buy_orders: int | None = None
    sell_listings: int | None = None
    folder_id: int | None = None


def stored_schema(path: Path | str) -> int | None:
    """Schema version of an existing database, or None when there is none yet."""
    if not Path(path).is_file():
        return None
    conn = sqlite3.connect(str(path))
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'").fetchone():
            return None
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    finally:
        conn.close()
    return int(meta.get("schema", 1)) if "currency" in meta else None


class Store:
    def __init__(self, path: Path | str, currency: str):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'currency'").fetchone()
        if row is None:
            with self.conn:
                self.conn.execute(PRICE_HISTORY_DDL)
                self.conn.execute(HOLDING_HISTORY_DDL)
                self.conn.execute(HOLDING_HISTORY_INDEX)
                self.conn.execute("INSERT INTO meta VALUES ('currency', ?)", (currency,))
                self.conn.execute("INSERT INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
        elif row["value"] != currency:
            raise StoreError(
                f"{path} holds prices in {row['value']}, but the configured currency is "
                f"{currency}. Use a new database file or set CS2BOT_CURRENCY={row['value']}."
            )
        else:
            self._migrate()
            self._reconcile_holding_history()
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
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '2')")
        if version < 3:
            with self.conn:
                self.conn.execute(PRICE_HISTORY_DDL)
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '3')")
        if version < 4:
            with self.conn:
                self.conn.execute(HOLDING_HISTORY_DDL)
                self.conn.execute(HOLDING_HISTORY_INDEX)
                day = self._utc_day()
                self.conn.execute(
                    "INSERT OR IGNORE INTO holding_history (user_id, hash_name, day, qty) "
                    "SELECT user_id, hash_name, ?, qty FROM holdings", (day,),
                )
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '4')")
        if version < 5:
            with self.conn:
                existing = {row["name"] for row in self.conn.execute("PRAGMA table_info(prices)")}
                for column in ("buy_order_cents", "sell_order_cents", "buy_orders", "sell_listings"):
                    if column not in existing:
                        self.conn.execute(f"ALTER TABLE prices ADD COLUMN {column} INTEGER")
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '5')")
        if version < 6:
            # v5 kept only prices that were found; SQLite cannot drop NOT NULL in place.
            with self.conn:
                self.conn.execute("BEGIN")
                self.conn.execute("ALTER TABLE price_history RENAME TO price_history_v5")
                self.conn.execute(PRICE_HISTORY_DDL)
                self.conn.execute("INSERT INTO price_history SELECT hash_name, day, cents FROM price_history_v5")
                self.conn.execute("DROP TABLE price_history_v5")
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '6')")
        if version < 7:
            # The tables come from SCHEMA. Whoever wrote to the bot can be written to.
            with self.conn:
                self.conn.execute(
                    "INSERT OR IGNORE INTO prefs (user_id, write_access) "
                    "SELECT user_id, 1 FROM users WHERE via = 'bot' "
                    "UNION SELECT DISTINCT user_id, 1 FROM events WHERE kind = 'bot'"
                )
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '7')")
        if version < 8:
            # sales and inventory_sync come from SCHEMA.
            with self.conn:
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '8')")
        if version < 9:
            # folders and broadcasts come from SCHEMA; positions get a folder.
            with self.conn:
                if "folder_id" not in {r["name"] for r in self.conn.execute("PRAGMA table_info(holdings)")}:
                    self.conn.execute("ALTER TABLE holdings ADD COLUMN folder_id INTEGER")
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '9')")

    def _reconcile_holding_history(self) -> None:
        """Records quantities changed without this code (an older release, manual SQL).

        Otherwise the chart would keep counting items that are gone, forever.
        """
        rows = self.conn.execute(
            """WITH latest AS (  -- SQLite takes `qty` from the MAX(day) row
                   SELECT user_id, hash_name, MAX(day) AS day, qty FROM holding_history
                   GROUP BY user_id, hash_name)
               SELECT h.user_id, h.hash_name, h.qty FROM holdings h
               LEFT JOIN latest l ON l.user_id = h.user_id AND l.hash_name = h.hash_name
               WHERE l.qty IS NULL OR l.qty != h.qty
               UNION ALL
               SELECT l.user_id, l.hash_name, 0 FROM latest l
               LEFT JOIN holdings h ON h.user_id = l.user_id AND h.hash_name = l.hash_name
               WHERE h.user_id IS NULL AND l.qty > 0"""
        ).fetchall()
        if rows:
            with self.conn:
                for user_id, hash_name, qty in rows:
                    self._record_holding(user_id, hash_name, qty)

    @staticmethod
    def _utc_day(at: float | None = None) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(time.time() if at is None else at))

    def _quantity_changed(self, user_id: int, hash_name: str, old: int, new: int, at: float | None = None) -> None:
        """Called in the same transaction as a quantity change.

        Portfolio alerts measure what prices did, so what was added or removed
        moves their baseline too. A bought item leaves the watchlist.
        """
        self._record_holding(user_id, hash_name, new, at)
        cents = self.conn.execute("SELECT cents FROM prices WHERE hash_name = ?", (hash_name,)).fetchone()
        if cents is not None and cents[0] is not None and new != old:
            self.conn.execute(
                "UPDATE alerts SET baseline = baseline + ? WHERE user_id = ? AND hash_name IS NULL",
                ((new - old) * net_cents(cents[0]), user_id),
            )
        if new > 0:
            self.conn.execute("DELETE FROM watchlist WHERE user_id = ? AND hash_name = ?", (user_id, hash_name))
        else:  # profit needs a position: its alerts go with it (price alerts stay)
            self.conn.execute("DELETE FROM alerts WHERE user_id = ? AND hash_name = ? AND metric = 'profit'",
                              (user_id, hash_name))

    def _record_holding(self, user_id: int, hash_name: str, qty: int, at: float | None = None) -> None:
        """Called in the same transaction as a quantity change."""
        self.conn.execute(
            "INSERT INTO holding_history (user_id, hash_name, day, qty, changed) VALUES (?, ?, ?, ?, 1) "
            "ON CONFLICT(user_id, hash_name, day) DO UPDATE SET qty = excluded.qty, changed = 1",
            (user_id, hash_name, self._utc_day(at), qty),
        )

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

    def user_language(self, user_id: int) -> str | None:
        row = self.conn.execute("SELECT language FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row[0] if row else None

    def log_event(self, user_id: int, kind: str, n: int = 1, at: float | None = None) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO events VALUES (?, ?, ?, ?)",
                              (time.time() if at is None else at, user_id, kind, n))

    def prune_events(self, before: float) -> int:
        with self.conn:
            return self.conn.execute("DELETE FROM events WHERE ts < ?", (before,)).rowcount

    def stats_readonly(self, now: float | None = None) -> dict:
        """`stats` on a connection of its own, so it can run in a worker thread.

        Its aggregates read months of events: on the event loop they would stall
        every user's request, the bot and the price refresh meanwhile.
        """
        conn = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            return self.stats(now, conn=conn)
        finally:
            conn.close()

    def stats(self, now: float | None = None, days: int = 14, conn: sqlite3.Connection | None = None) -> dict:
        """Usage numbers for the admin screen. Days are UTC calendar days."""
        now = time.time() if now is None else now
        conn = self.conn if conn is None else conn
        one = lambda sql, *args: conn.execute(sql, args).fetchone()[0]  # noqa: E731
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
        active = dict(conn.execute(
            "SELECT CAST(ts / 86400 AS INTEGER), COUNT(DISTINCT user_id) FROM events "
            "WHERE ts >= ? GROUP BY 1", (start,)).fetchall())
        new = dict(conn.execute(
            "SELECT CAST(first_seen / 86400 AS INTEGER), COUNT(*) FROM users WHERE first_seen >= ? GROUP BY 1",
            (start,)).fetchall())
        daily = [{"day": d * day, "active": active.get(d, 0), "new": new.get(d, 0)}
                 for d in range(int(start // day), int(now // day) + 1)]
        actions = dict(conn.execute(
            "SELECT kind, SUM(n) FROM events WHERE ts >= ? GROUP BY kind", (now - 7 * day,)).fetchall())
        top = [
            {"hash_name": r[0], "name": r[1], "holders": r[2], "qty": r[3]}
            for r in conn.execute(
                """SELECT h.hash_name, COALESCE(i.name, h.hash_name), COUNT(*), SUM(h.qty)
                   FROM holdings h LEFT JOIN items i ON i.hash_name = h.hash_name
                   GROUP BY h.hash_name ORDER BY 3 DESC, 4 DESC LIMIT 5""")
        ]
        recent = [
            {"id": r[0], "username": r[1], "first_name": r[2], "first_seen": r[3], "last_seen": r[4], "items": r[5]}
            for r in conn.execute(
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

    def known_icon(self, icon: str) -> bool:
        return self.conn.execute("SELECT 1 FROM items WHERE icon = ? LIMIT 1", (icon,)).fetchone() is not None

    def item(self, hash_name: str) -> tuple[str, str | None] | None:
        row = self.conn.execute("SELECT name, icon FROM items WHERE hash_name = ?", (hash_name,)).fetchone()
        return (row["name"], row["icon"]) if row else None

    # -- holdings ------------------------------------------------------------

    def holdings(self, user_id: int, now: float | None = None) -> list[Holding]:
        as_of = time.time() if now is None else now
        rows = self.conn.execute(
            """SELECT h.hash_name, COALESCE(i.name, h.hash_name) AS name, i.icon, h.qty, h.buy_cents,
                      p.cents AS price_cents, p.updated_at AS price_updated, p.checked_at AS price_checked,
                      ph.cents AS yesterday_cents, ph7.cents AS week_ago_cents,
                      p.buy_order_cents, p.sell_order_cents, p.buy_orders, p.sell_listings, h.folder_id
               FROM holdings h
               LEFT JOIN items i ON i.hash_name = h.hash_name
               LEFT JOIN prices p ON p.hash_name = h.hash_name
               LEFT JOIN price_history ph ON ph.hash_name = h.hash_name AND ph.day = ?
               LEFT JOIN price_history ph7 ON ph7.hash_name = h.hash_name AND ph7.day = ?
               WHERE h.user_id = ?
               ORDER BY h.added_at""",
            (self._utc_day(as_of - 86400), self._utc_day(as_of - 7 * 86400), user_id),
        ).fetchall()
        return [Holding(**dict(r)) for r in rows]

    def holding(self, user_id: int, hash_name: str) -> Holding | None:
        return next((h for h in self.holdings(user_id) if h.hash_name == hash_name), None)

    def count(self, user_id: int) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM holdings WHERE user_id = ?", (user_id,)).fetchone()[0]

    def add_lot(self, user_id: int, hash_name: str, qty: int, buy_cents: int | None,
                folder_id: int | None = None) -> None:
        """Adds a purchase; an existing position gets the weighted average price.

        A lot whose price is unknown keeps the position's average, so a known
        price paid is never lost; a position whose price is unknown stays unknown.
        `folder_id` places a new position; an existing one stays where it is.
        """
        with self.conn:
            self._add_lot(user_id, hash_name, qty, buy_cents, self._own_folder(user_id, folder_id))

    def _add_lot(self, user_id: int, hash_name: str, qty: int, buy_cents: int | None,
                 folder_id: int | None = None) -> None:
        """add_lot inside the caller's transaction."""
        old = self._qty(user_id, hash_name)
        cur = self.conn.execute(
            """INSERT INTO holdings (user_id, hash_name, qty, buy_cents, added_at, folder_id) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, hash_name) DO UPDATE SET
                 buy_cents = CASE WHEN excluded.buy_cents IS NULL THEN buy_cents
                              ELSE (qty * buy_cents + excluded.qty * excluded.buy_cents
                                    + (qty + excluded.qty) / 2) / (qty + excluded.qty) END,
                 qty = qty + excluded.qty,
                 buy_at_market = 0
               WHERE qty + excluded.qty <= ?""",
            (user_id, hash_name, qty, buy_cents, time.time(), folder_id, MAX_QTY),
        )
        if cur.rowcount == 0:
            raise QuantityLimit(f"at most {MAX_QTY} of one item")
        self._quantity_changed(user_id, hash_name, old, self._qty(user_id, hash_name))

    def import_items(self, user_id: int, rows: list[tuple[str, int, int | None, bool]],
                     folder_id: int | None = None) -> int:
        """(hash name, qty, buy cents or None, take first market price); held items are skipped."""
        now = time.time()
        added = 0
        with self.conn:
            folder_id = self._own_folder(user_id, folder_id)
            for name, qty, buy, at_market in rows:
                qty = min(qty, MAX_QTY)
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO holdings (user_id, hash_name, qty, buy_cents, added_at, buy_at_market, "
                    "folder_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user_id, name, qty, buy, now, int(at_market and buy is None), folder_id),
                )
                if cur.rowcount:
                    added += 1
                    self._quantity_changed(user_id, name, 0, qty, now)
        return added

    def set_holding(self, user_id: int, hash_name: str, qty: int, buy_cents: int | None) -> bool:
        with self.conn:
            old = self.conn.execute(
                "SELECT qty FROM holdings WHERE user_id = ? AND hash_name = ?", (user_id, hash_name)
            ).fetchone()
            cur = self.conn.execute(
                "UPDATE holdings SET qty = ?, buy_cents = ?, buy_at_market = 0 WHERE user_id = ? AND hash_name = ?",
                (qty, buy_cents, user_id, hash_name),
            )
            if cur.rowcount and old[0] != qty:
                self._quantity_changed(user_id, hash_name, old[0], qty)
        return cur.rowcount > 0

    def delete_holdings(self, user_id: int, hash_names: list[str]) -> int:
        removed = 0
        with self.conn:
            for name in hash_names:
                old = self._qty(user_id, name)
                cur = self.conn.execute("DELETE FROM holdings WHERE user_id = ? AND hash_name = ?", (user_id, name))
                if cur.rowcount:
                    removed += 1
                    self._quantity_changed(user_id, name, old, 0)
        return removed

    def delete_holding(self, user_id: int, hash_name: str) -> bool:
        return self.delete_holdings(user_id, [hash_name]) > 0

    def _qty(self, user_id: int, hash_name: str) -> int:
        row = self.conn.execute(
            "SELECT qty FROM holdings WHERE user_id = ? AND hash_name = ?", (user_id, hash_name)
        ).fetchone()
        return row[0] if row else 0

    # -- folders -------------------------------------------------------------

    def folders(self, user_id: int) -> list[dict]:
        """In creation order, with how many positions each holds."""
        rows = self.conn.execute(
            """SELECT f.id, f.name, COUNT(h.hash_name) AS count FROM folders f
               LEFT JOIN holdings h ON h.user_id = f.user_id AND h.folder_id = f.id
               WHERE f.user_id = ? GROUP BY f.id ORDER BY f.id""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _own_folder(self, user_id: int, folder_id: int | None) -> int | None:
        """`folder_id` if it is this user's, else None (no folder)."""
        if folder_id is None:
            return None
        row = self.conn.execute("SELECT 1 FROM folders WHERE id = ? AND user_id = ?", (folder_id, user_id)).fetchone()
        return folder_id if row else None

    def save_folder(self, user_id: int, name: str, folder_id: int | None = None) -> int | None:
        """Creates or renames a folder; None when full, taken or not this user's."""
        with self.conn:
            taken = self.conn.execute(
                "SELECT id FROM folders WHERE user_id = ? AND name = ?", (user_id, name)).fetchone()
            if taken is not None:
                return folder_id if taken[0] == folder_id else None
            if folder_id is not None:
                cur = self.conn.execute("UPDATE folders SET name = ? WHERE id = ? AND user_id = ?",
                                        (name, folder_id, user_id))
                return folder_id if cur.rowcount else None
            count = self.conn.execute("SELECT COUNT(*) FROM folders WHERE user_id = ?", (user_id,)).fetchone()[0]
            if count >= MAX_FOLDERS:
                return None
            return self.conn.execute("INSERT INTO folders (user_id, name) VALUES (?, ?)", (user_id, name)).lastrowid

    def delete_folder(self, user_id: int, folder_id: int) -> bool:
        """Its positions stay, in no folder."""
        with self.conn:
            if not self.conn.execute("DELETE FROM folders WHERE id = ? AND user_id = ?",
                                     (folder_id, user_id)).rowcount:
                return False
            self.conn.execute("UPDATE holdings SET folder_id = NULL WHERE user_id = ? AND folder_id = ?",
                              (user_id, folder_id))
        return True

    def move_holdings(self, user_id: int, hash_names: list[str], folder_id: int | None) -> int | None:
        """Puts positions into a folder (None: out of any); None when the folder isn't this user's."""
        with self.conn:
            if folder_id is not None and self._own_folder(user_id, folder_id) is None:
                return None
            return sum(self.conn.execute(
                "UPDATE holdings SET folder_id = ? WHERE user_id = ? AND hash_name = ?",
                (folder_id, user_id, name)).rowcount for name in hash_names)

    # -- broadcasts ------------------------------------------------------------

    def broadcast_audience(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM prefs WHERE write_access = 1").fetchone()[0]

    def start_broadcast(self, admin_id: int, text: str, at: float | None = None) -> int | None:
        """None while another one is still going out."""
        with self.conn:
            if self.running_broadcast() is not None:
                return None
            return self.conn.execute(
                "INSERT INTO broadcasts (admin_id, text, created_at, total) VALUES (?, ?, ?, ?)",
                (admin_id, text, time.time() if at is None else at, self.broadcast_audience()),
            ).lastrowid

    def running_broadcast(self) -> dict | None:
        row = self.conn.execute("SELECT * FROM broadcasts WHERE finished_at IS NULL ORDER BY id LIMIT 1").fetchone()
        return dict(row) if row else None

    def last_broadcast(self) -> dict | None:
        row = self.conn.execute("SELECT * FROM broadcasts ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def broadcast_recipients(self, broadcast_id: int, limit: int = 50) -> list[int]:
        """The next users, in id order after the cursor, the bot may write to."""
        cursor = self.conn.execute("SELECT cursor FROM broadcasts WHERE id = ?", (broadcast_id,)).fetchone()[0]
        rows = self.conn.execute(
            "SELECT user_id FROM prefs WHERE write_access = 1 AND user_id > ? ORDER BY user_id LIMIT ?",
            (cursor, limit),
        ).fetchall()
        return [r[0] for r in rows]

    def broadcast_progress(self, broadcast_id: int, user_id: int, delivered: bool | None) -> None:
        """`delivered` None: skipped (e.g. not allowed to use a private bot)."""
        with self.conn:
            self.conn.execute(
                "UPDATE broadcasts SET cursor = ?, sent = sent + ?, failed = failed + ? WHERE id = ?",
                (user_id, int(delivered is True), int(delivered is False), broadcast_id),
            )

    def finish_broadcast(self, broadcast_id: int, cancelled: bool = False, at: float | None = None) -> bool:
        with self.conn:
            return self.conn.execute(
                "UPDATE broadcasts SET finished_at = ?, cancelled = ? WHERE id = ? AND finished_at IS NULL",
                (time.time() if at is None else at, int(cancelled), broadcast_id),
            ).rowcount > 0

    # -- sales ---------------------------------------------------------------

    def sell(self, user_id: int, hash_name: str, qty: int, price_cents: int, at: float | None = None) -> int | None:
        """Records a sale and reduces the position; None when fewer than `qty` are held.

        The sale keeps the average price paid, so realized and unrealized profit
        share one basis and add up. The remaining position keeps its average.
        """
        at = time.time() if at is None else at
        with self.conn:
            row = self.conn.execute(
                "SELECT qty, buy_cents FROM holdings WHERE user_id = ? AND hash_name = ?", (user_id, hash_name)
            ).fetchone()
            if row is None or row["qty"] < qty:
                return None
            if row["qty"] == qty:
                self.conn.execute("DELETE FROM holdings WHERE user_id = ? AND hash_name = ?", (user_id, hash_name))
            else:
                self.conn.execute("UPDATE holdings SET qty = qty - ? WHERE user_id = ? AND hash_name = ?",
                                  (qty, user_id, hash_name))
            self._quantity_changed(user_id, hash_name, row["qty"], row["qty"] - qty, at)
            self._settle_gone(user_id, hash_name, qty)
            return self.conn.execute(
                "INSERT INTO sales (user_id, hash_name, qty, price_cents, buy_cents, sold_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, hash_name, qty, price_cents, row["buy_cents"], at),
            ).lastrowid

    def undo_sale(self, user_id: int, sale_id: int) -> bool:
        """Deletes a sale and puts its items back at the price paid; raises QuantityLimit."""
        with self.conn:
            sale = self.conn.execute(
                "SELECT hash_name, qty, buy_cents FROM sales WHERE id = ? AND user_id = ?", (sale_id, user_id)
            ).fetchone()
            if sale is None:
                return False
            self._add_lot(user_id, sale["hash_name"], sale["qty"], sale["buy_cents"])
            self.conn.execute("DELETE FROM sales WHERE id = ?", (sale_id,))
        return True

    def sale(self, user_id: int, sale_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM sales WHERE id = ? AND user_id = ?", (sale_id, user_id)).fetchone()
        return dict(row) if row else None

    def sales(self, user_id: int) -> list[dict]:
        """Newest first."""
        rows = self.conn.execute(
            """SELECT s.id, s.hash_name, COALESCE(i.name, s.hash_name) AS name, i.icon,
                      s.qty, s.price_cents, s.buy_cents, s.sold_at
               FROM sales s LEFT JOIN items i ON i.hash_name = s.hash_name
               WHERE s.user_id = ? ORDER BY s.sold_at DESC, s.id DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def realized(self, user_id: int) -> dict:
        """Realized profit in cents over the sales whose price paid is known."""
        # TOTAL, not SUM: SUM raises on int64 overflow, which enough huge sales would reach.
        row = self.conn.execute(
            """SELECT COUNT(*), TOTAL(qty * price_cents),
                      TOTAL(CASE WHEN buy_cents IS NOT NULL THEN qty * price_cents END),
                      TOTAL(qty * buy_cents), COUNT(buy_cents)
               FROM sales WHERE user_id = ?""",
            (user_id,),
        ).fetchone()
        count, proceeds, known_proceeds, cost, known = row
        return {"count": count, "proceeds": round(proceeds), "unknown": count - known,
                "cost": round(cost) if known else None,
                "profit": round(known_proceeds - cost) if known else None}

    # -- inventory sync --------------------------------------------------------

    def remember_inventory(self, user_id: int, steamid: str, items: dict[str, int], at: float | None = None) -> None:
        """After an import: the profile to re-read, and what it held just now.

        Importing from the same profile counts as reviewing what was new; a
        different profile starts over. Turning the check off survives either.
        """
        at = time.time() if at is None else at
        snapshot = json.dumps(items, ensure_ascii=False, sort_keys=True)
        with self.conn:
            old = self.conn.execute("SELECT steamid FROM inventory_sync WHERE user_id = ?", (user_id,)).fetchone()
            self.conn.execute(
                """INSERT INTO inventory_sync (user_id, steamid, snapshot, checked_at, next_at) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET steamid = excluded.steamid, snapshot = excluded.snapshot,
                     checked_at = excluded.checked_at, next_at = excluded.next_at, error = NULL,
                     pending_new = '[]'""",
                (user_id, steamid, snapshot, at, at + SYNC_EVERY),
            )
            if old is not None and old[0] != steamid:
                self.conn.execute("UPDATE inventory_sync SET pending_gone = '{}' WHERE user_id = ?", (user_id,))

    def sync_settings(self, user_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT steamid, enabled, checked_at, error FROM inventory_sync WHERE user_id = ?", (user_id,)
        ).fetchone()
        return None if row is None else dict(row) | {"enabled": bool(row["enabled"])}

    def set_sync_enabled(self, user_id: int, enabled: bool) -> bool:
        """False when no profile is remembered yet."""
        with self.conn:
            return self.conn.execute(
                "UPDATE inventory_sync SET enabled = ? WHERE user_id = ?", (int(enabled), user_id)
            ).rowcount > 0

    def due_syncs(self, now: float, limit: int = 1) -> list[dict]:
        rows = self.conn.execute(
            "SELECT user_id, steamid FROM inventory_sync WHERE enabled = 1 AND next_at <= ? ORDER BY next_at LIMIT ?",
            (now, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def postpone_sync(self, user_id: int, steamid: str, until: float, error: str | None = None) -> None:
        """A read that failed: try again at `until`. `error` is what the user sees."""
        with self.conn:
            self.conn.execute(
                "UPDATE inventory_sync SET next_at = ?, error = COALESCE(?, error) WHERE user_id = ? AND steamid = ?",
                (until, error, user_id, steamid),
            )

    def record_sync(self, user_id: int, steamid: str, items: dict[str, int],
                    at: float | None = None) -> tuple[list[str], dict[str, int]]:
        """Stores a fresh read; returns what it found (new names, {held name: qty gone}).

        New: marketable items that weren't there last time and aren't held.
        Gone: fewer of a held item than last time, capped at the quantity held.
        Both add to what is waiting for the user until they review it.
        """
        at = time.time() if at is None else at
        with self.conn:
            row = self.conn.execute(
                "SELECT snapshot, pending_new, pending_gone FROM inventory_sync WHERE user_id = ? AND steamid = ?",
                (user_id, steamid),
            ).fetchone()
            if row is None:  # the user imported another profile meanwhile
                return [], {}
            before = json.loads(row["snapshot"])
            held = dict(self.conn.execute(
                "SELECT hash_name, qty FROM holdings WHERE user_id = ?", (user_id,)).fetchall())
            new = [name for name in items if name not in before and name not in held]
            gone = {name: min(before[name] - items.get(name, 0), held[name])
                    for name in before if name in held and items.get(name, 0) < before[name]}
            pending_new = json.loads(row["pending_new"])
            pending_new += [name for name in new if name not in pending_new]
            pending_gone = json.loads(row["pending_gone"])
            for name, qty in gone.items():
                pending_gone[name] = pending_gone.get(name, 0) + qty
            self.conn.execute(
                """UPDATE inventory_sync SET snapshot = ?, pending_new = ?, pending_gone = ?, checked_at = ?,
                     next_at = ?, error = NULL WHERE user_id = ?""",
                (json.dumps(items, ensure_ascii=False, sort_keys=True), json.dumps(pending_new, ensure_ascii=False),
                 json.dumps(pending_gone, ensure_ascii=False), at, at + SYNC_EVERY, user_id),
            )
        return new, gone

    def pending_sync(self, user_id: int) -> dict | None:
        """What the last reads found that still needs a look, or None.

        Checked against the holdings now: an item imported since is no longer new,
        and "gone" never exceeds what is still held.
        """
        row = self.conn.execute(
            "SELECT steamid, snapshot, pending_new, pending_gone FROM inventory_sync WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return None
        held = dict(self.conn.execute("SELECT hash_name, qty FROM holdings WHERE user_id = ?", (user_id,)).fetchall())
        snapshot = json.loads(row["snapshot"])
        new = [(name, snapshot[name]) for name in json.loads(row["pending_new"])
               if name not in held and name in snapshot]
        gone = [(name, min(qty, held[name])) for name, qty in json.loads(row["pending_gone"]).items()
                if name in held and qty > 0]
        if not new and not gone:
            return None
        names = dict((r[0], (r[1], r[2])) for r in self.conn.execute(
            "SELECT hash_name, name, icon FROM items WHERE hash_name IN (%s)" % ",".join("?" * (len(new) + len(gone))),
            [n for n, _ in new + gone]).fetchall())
        entry = lambda name, qty: {"hash_name": name, "name": names.get(name, (name,))[0],  # noqa: E731
                                   "icon": names.get(name, (None, None))[1], "qty": qty}
        return {"steamid": row["steamid"], "new": [entry(*x) for x in new], "gone": [entry(*x) for x in gone]}

    def dismiss_sync(self, user_id: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE inventory_sync SET pending_new = '[]', pending_gone = '{}' WHERE user_id = ?",
                              (user_id,))

    def _settle_gone(self, user_id: int, hash_name: str, qty: int) -> None:
        """A recorded sale answers "gone from the inventory, sold?" for that many."""
        row = self.conn.execute("SELECT pending_gone FROM inventory_sync WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            return
        gone = json.loads(row[0])
        if hash_name not in gone:
            return
        left = gone.pop(hash_name) - qty
        if left > 0:
            gone[hash_name] = left
        self.conn.execute("UPDATE inventory_sync SET pending_gone = ? WHERE user_id = ?",
                          (json.dumps(gone, ensure_ascii=False), user_id))

    def portfolio_history(self, user_id: int, days: int = 30, now: float | None = None,
                          folder_id: int | None = None) -> dict:
        """Daily net value of a portfolio, and how much of its change the market made.

        A day's value is each recorded quantity times the item's last observed
        price: the rule the live price cache follows too (a failed fetch keeps
        the previous price, a fetch that found no price counts as nothing), so a
        past day never changes once it is over. Today's point uses the live
        cache and matches Home. Quantities before the v4 migration are unknown,
        so the series starts at that baseline.

        `days` > 0 returns the last `days` days plus the day before them, which
        a "7 days" change is measured from; 0 returns everything. The market
        change leaves out quantity changes and items that gained or lost a
        price, so adding an item never shows up as a gain.

        `folder_id` limits it to the items in that folder now: the history of
        what the folder holds, as folders themselves have none.
        """
        today = date.fromisoformat(self._utc_day(now))
        changes = self.conn.execute(
            "SELECT hash_name, day, qty, changed FROM holding_history WHERE user_id = ? AND day <= ? ORDER BY day",
            (user_id, today.isoformat()),
        ).fetchall()
        if folder_id is not None:
            members = {r[0] for r in self.conn.execute(
                "SELECT hash_name FROM holdings WHERE user_id = ? AND folder_id = ?", (user_id, folder_id))}
            changes = [row for row in changes if row["hash_name"] in members]
        if not changes:
            return {"points": [], "change": None, "change_ratio": None}
        start = date.fromisoformat(changes[0]["day"])
        if days:
            start = max(start, today - timedelta(days=days))
        held = "SELECT DISTINCT hash_name FROM holding_history WHERE user_id = ?"
        # The last observation before the window (one index lookup per item),
        # then each one inside it; today comes from the live cache.
        quotes = self.conn.execute(
            f"SELECT ph.hash_name, ph.day, ph.cents FROM ({held}) n JOIN price_history ph "
            f"ON ph.hash_name = n.hash_name AND ph.day = (SELECT MAX(day) FROM price_history "
            f"WHERE hash_name = n.hash_name AND day < ?) "
            f"UNION ALL SELECT hash_name, day, cents FROM price_history "
            f"WHERE hash_name IN ({held}) AND day >= ? AND day < ? ORDER BY 2",
            (user_id, start.isoformat(), user_id, start.isoformat(), today.isoformat()),
        ).fetchall()
        live = dict(self.conn.execute(
            f"SELECT hash_name, cents FROM prices WHERE hash_name IN ({held})", (user_id,)
        ).fetchall())

        qty: dict[str, int] = {}
        price: dict[str, int | None] = {}

        def worth(name: str) -> int:
            cents = price.get(name)
            return 0 if cents is None else qty.get(name, 0) * net_cents(cents)

        changes_on: dict[str, list] = defaultdict(list)
        quotes_on: dict[str, list] = defaultdict(list)
        for row in changes:
            if row["day"] < start.isoformat():
                qty[row["hash_name"]] = row["qty"]
            else:
                changes_on[row["day"]].append(row)
        for row in quotes:
            if row["day"] < start.isoformat():
                price[row["hash_name"]] = row["cents"]
            else:
                quotes_on[row["day"]].append(row)

        value = sum(worth(name) for name in qty)  # cents
        market = 0
        growth = 1.0
        points = []
        day = start
        while day <= today:
            key = day.isoformat()
            if day == today:
                updates = [(name, live.get(name)) for name in {row["hash_name"] for row in changes}]
            else:
                updates = [(row["hash_name"], row["cents"]) for row in quotes_on[key]]
            moved = 0
            base = value  # yesterday's value of what is still priced today
            for name, cents in updates:
                old = price.get(name)
                if old == cents:
                    continue
                before = worth(name)
                price[name] = cents
                value += worth(name) - before
                if old is not None and cents is not None:
                    moved += worth(name) - before
                else:
                    base -= before
            changed = False
            for row in changes_on[key]:
                name = row["hash_name"]
                before, old_qty = worth(name), qty.get(name, 0)
                qty[name] = row["qty"]
                value += worth(name) - before
                changed = changed or (bool(row["changed"]) and row["qty"] != old_qty)
            if points:  # the first day is the baseline
                market += moved
                if base > 0:
                    growth *= 1 + moved / base
            points.append({"day": key, "value": value / 100, "changed": changed})
            day += timedelta(days=1)
        return {
            "points": points,
            "change": market / 100 if len(points) > 1 else None,
            "change_ratio": growth - 1 if len(points) > 1 else None,
        }

    # -- inline mode -----------------------------------------------------------

    def search_items(self, words: list[str], limit: int = 20) -> list[str]:
        """Hash names from the catalogue whose name has every word (any case).

        The whole phrase as the name comes first, then names that start with it,
        then what most users hold. No Steam request: this is what an inline
        query shows while the user is still typing.
        """
        words = [w for w in words if w]
        if not words:
            return []
        esc = lambda w: w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")  # noqa: E731
        where = " AND ".join("(i.name LIKE ? ESCAPE '\\' OR i.hash_name LIKE ? ESCAPE '\\')" for _ in words)
        args: list = [f"%{esc(w)}%" for w in words for _ in (0, 1)]
        phrase = " ".join(words).lower()
        rows = self.conn.execute(
            f"""SELECT i.hash_name FROM items i
                LEFT JOIN (SELECT hash_name, COUNT(*) AS holders FROM holdings GROUP BY hash_name) h
                  ON h.hash_name = i.hash_name
                WHERE {where}
                ORDER BY lower(i.name) = ? DESC, lower(i.name) LIKE ? ESCAPE '\\' DESC,
                         COALESCE(h.holders, 0) DESC, length(i.name), i.name
                LIMIT ?""",
            args + [phrase, f"{esc(phrase)}%", limit],
        ).fetchall()
        return [r[0] for r in rows]

    def popular_items(self, limit: int = 10) -> list[str]:
        """What most users hold: shown to someone with nothing of their own yet."""
        rows = self.conn.execute(
            "SELECT hash_name FROM holdings GROUP BY hash_name ORDER BY COUNT(*) DESC, SUM(qty) DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [r[0] for r in rows]

    def known_names(self) -> list[str]:
        """Every item name the service has seen (to resolve a short key back to one)."""
        return [r[0] for r in self.conn.execute("SELECT hash_name FROM items UNION SELECT hash_name FROM prices")]

    def market_snapshot(self, hash_name: str, now: float | None = None, days: int = 30) -> dict:
        """An item's price with the UTC-day closes it is compared to, for a card or an inline result.

        `closes` covers the last `days` days (price found only), oldest first;
        today's live price stands in for today's close.
        """
        as_of = time.time() if now is None else now
        row = self.conn.execute(
            """SELECT COALESCE(i.name, n.hash_name) AS name, i.icon, p.cents, p.updated_at, p.checked_at,
                      p.buy_orders, p.sell_listings
               FROM (SELECT ? AS hash_name) n
               LEFT JOIN items i ON i.hash_name = n.hash_name
               LEFT JOIN prices p ON p.hash_name = n.hash_name""",
            (hash_name,),
        ).fetchone()
        today = self._utc_day(as_of)
        history = dict(self.conn.execute(
            "SELECT day, cents FROM price_history WHERE hash_name = ? AND day >= ? AND day < ? ORDER BY day",
            (hash_name, self._utc_day(as_of - days * 86400), today),
        ).fetchall())
        closes = [c for c in history.values() if c is not None]
        if row["cents"] is not None:
            closes.append(row["cents"])

        def change(ago: int) -> float | None:
            before = history.get(self._utc_day(as_of - ago * 86400))
            return row["cents"] / before - 1 if row["cents"] is not None and before else None
        return {
            "hash_name": hash_name, "name": row["name"], "icon": row["icon"], "cents": row["cents"],
            "updated_at": row["updated_at"], "checked_at": row["checked_at"],
            "buy_orders": row["buy_orders"], "sell_listings": row["sell_listings"],
            "change_24h": change(1), "change_7d": change(7), "change_30d": change(30), "closes": closes,
        }

    # -- watchlist -----------------------------------------------------------

    def watching(self, user_id: int, now: float | None = None) -> list[dict]:
        """Watched items with their price and yesterday's close, oldest first."""
        as_of = time.time() if now is None else now
        rows = self.conn.execute(
            """SELECT w.hash_name, COALESCE(i.name, w.hash_name) AS name, i.icon,
                      p.cents AS price_cents, p.checked_at AS price_checked, ph.cents AS yesterday_cents
               FROM watchlist w
               LEFT JOIN items i ON i.hash_name = w.hash_name
               LEFT JOIN prices p ON p.hash_name = w.hash_name
               LEFT JOIN price_history ph ON ph.hash_name = w.hash_name AND ph.day = ?
               WHERE w.user_id = ? ORDER BY w.added_at""",
            (self._utc_day(as_of - 86400), user_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def watch(self, user_id: int, hash_name: str) -> bool:
        """False when the watchlist is full; watching a held item does nothing."""
        with self.conn:
            if self._qty(user_id, hash_name):
                return True
            count = self.conn.execute("SELECT COUNT(*) FROM watchlist WHERE user_id = ?", (user_id,)).fetchone()[0]
            if count >= MAX_WATCHED:
                return False
            self.conn.execute("INSERT OR IGNORE INTO watchlist VALUES (?, ?, ?)", (user_id, hash_name, time.time()))
        return True

    def unwatch(self, user_id: int, hash_name: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM watchlist WHERE user_id = ? AND hash_name = ?", (user_id, hash_name))

    # -- alerts --------------------------------------------------------------

    def portfolio_value(self, user_id: int) -> int:
        """Net value in cents of the priced holdings (what Home shows)."""
        rows = self.conn.execute(
            "SELECT h.qty, p.cents FROM holdings h JOIN prices p ON p.hash_name = h.hash_name "
            "WHERE h.user_id = ? AND p.cents IS NOT NULL", (user_id,),
        ).fetchall()
        return sum(qty * net_cents(cents) for qty, cents in rows)

    def alerts(self, user_id: int | None = None) -> list[dict]:
        """Alerts with what they are measured against right now; all users' when None."""
        rows = self.conn.execute(
            f"""SELECT a.*, COALESCE(i.name, a.hash_name) AS name, i.icon, p.cents AS price_cents,
                       h.qty, h.buy_cents
                FROM alerts a
                LEFT JOIN items i ON i.hash_name = a.hash_name
                LEFT JOIN prices p ON p.hash_name = a.hash_name
                LEFT JOIN holdings h ON h.user_id = a.user_id AND h.hash_name = a.hash_name
                {"WHERE a.user_id = ?" if user_id is not None else ""}
                ORDER BY a.user_id, a.hash_name IS NOT NULL, a.id""",
            () if user_id is None else (user_id,),
        ).fetchall()
        values: dict[int, int] = {}
        result = []
        for r in rows:
            alert = dict(r)
            if alert["hash_name"] is None:
                if alert["user_id"] not in values:
                    values[alert["user_id"]] = self.portfolio_value(alert["user_id"])
                alert["value_cents"] = values[alert["user_id"]]
            result.append(alert)
        return result

    def save_alert(self, user_id: int, hash_name: str | None, metric: str, above: bool, threshold: int,
                   alert_id: int | None = None) -> int | None:
        """Creates or edits an alert and arms it; None when the user has no room or no such alert.

        A portfolio alert is measured from the value when it was created; editing keeps that.
        """
        with self.conn:
            if alert_id is not None:
                cur = self.conn.execute(
                    "UPDATE alerts SET metric = ?, above = ?, threshold = ?, armed = 1, fired_at = NULL, "
                    "fired_value = NULL WHERE id = ? AND user_id = ? AND hash_name IS ?",
                    (metric, int(above), threshold, alert_id, user_id, hash_name),
                )
                return alert_id if cur.rowcount else None
            count = self.conn.execute("SELECT COUNT(*) FROM alerts WHERE user_id = ?", (user_id,)).fetchone()[0]
            if count >= MAX_ALERTS:
                return None
            baseline = self.portfolio_value(user_id) if hash_name is None else None
            cur = self.conn.execute(
                "INSERT INTO alerts (user_id, hash_name, metric, above, threshold, baseline, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, hash_name, metric, int(above), threshold, baseline, time.time()),
            )
            return cur.lastrowid

    def rearm_alerts(self, alert_ids: list[int]) -> None:
        """Undoes a firing whose message could not be delivered: it goes out on a later pass."""
        with self.conn:
            self.conn.executemany("UPDATE alerts SET armed = 1, fired_at = NULL, fired_value = NULL WHERE id = ?",
                                  [(i,) for i in alert_ids])

    def delete_alert(self, user_id: int, alert_id: int) -> bool:
        with self.conn:
            return self.conn.execute(
                "DELETE FROM alerts WHERE id = ? AND user_id = ?", (alert_id, user_id)
            ).rowcount > 0

    def alerts_fired(self, fired: list[tuple[int, int]], rearmed: list[int], at: float | None = None) -> None:
        """(alert id, the value it fired at) are disarmed; `rearmed` ids are armed again."""
        at = time.time() if at is None else at
        with self.conn:
            self.conn.executemany(
                "UPDATE alerts SET armed = 0, fired_at = ?, fired_value = ? WHERE id = ?",
                [(at, value, alert_id) for alert_id, value in fired],
            )
            self.conn.executemany("UPDATE alerts SET armed = 1 WHERE id = ?", [(i,) for i in rearmed])

    # -- preferences -----------------------------------------------------------

    PREF_DEFAULTS = {"digest": "off", "digest_hour": 10, "tz": None, "digest_sent": None,
                     "digest_offered": 0, "write_access": 0}

    def prefs(self, user_id: int) -> dict:
        row = self.conn.execute("SELECT * FROM prefs WHERE user_id = ?", (user_id,)).fetchone()
        prefs = dict(self.PREF_DEFAULTS) | (dict(row) if row else {})
        prefs.pop("user_id", None)
        return prefs

    def set_prefs(self, user_id: int, **values) -> None:
        unknown = set(values) - set(self.PREF_DEFAULTS)
        if unknown:
            raise ValueError(f"unknown preference(s): {sorted(unknown)}")
        if not values:
            return
        columns = ", ".join(values)
        with self.conn:
            self.conn.execute(
                f"INSERT INTO prefs (user_id, {columns}) VALUES (?{', ?' * len(values)}) "
                f"ON CONFLICT(user_id) DO UPDATE SET " + ", ".join(f"{k} = excluded.{k}" for k in values),
                (user_id, *values.values()),
            )

    def digest_users(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM prefs WHERE digest != 'off'").fetchall()
        return [dict(r) for r in rows]

    def offer_digest(self, user_id: int) -> bool:
        """Offered once, after the first import or the third item (see the roadmap)."""
        prefs = self.prefs(user_id)
        if prefs["digest_offered"] or prefs["digest"] != "off":
            return False
        return self.count(user_id) >= 3 or self.conn.execute(
            "SELECT 1 FROM events WHERE user_id = ? AND kind = 'import' LIMIT 1", (user_id,)
        ).fetchone() is not None

    # -- prices --------------------------------------------------------------

    def tracked(self) -> list[tuple[str, float | None]]:
        """Every held, watched or alerted item with its last price check (None = never), oldest first."""
        rows = self.conn.execute(
            """SELECT n.hash_name, p.checked_at
               FROM (SELECT hash_name FROM holdings UNION SELECT hash_name FROM watchlist
                     UNION SELECT hash_name FROM alerts WHERE hash_name IS NOT NULL) n
               LEFT JOIN prices p ON p.hash_name = n.hash_name
               ORDER BY p.checked_at IS NOT NULL, p.checked_at"""
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def price(self, hash_name: str) -> tuple[int | None, float | None] | None:
        row = self.conn.execute(
            "SELECT cents, updated_at FROM prices WHERE hash_name = ?", (hash_name,)
        ).fetchone()
        return (row["cents"], row["updated_at"]) if row else None

    def liquidity(self, hash_name: str) -> tuple[int | None, int | None, int | None, int | None]:
        row = self.conn.execute(
            "SELECT buy_order_cents, sell_order_cents, buy_orders, sell_listings "
            "FROM prices WHERE hash_name = ?", (hash_name,)
        ).fetchone()
        return tuple(row) if row else (None, None, None, None)

    def set_price(self, hash_name: str, cents: int | None, at: float | None = None, *,
                  buy_order_cents: int | None = None, sell_order_cents: int | None = None,
                  buy_orders: int | None = None, sell_listings: int | None = None) -> None:
        at = time.time() if at is None else at
        with self.conn:
            before = self.conn.execute("SELECT cents FROM prices WHERE hash_name = ?", (hash_name,)).fetchone()
            old = before[0] if before else None
            if (old is None) != (cents is None):
                # A price that appears (a fresh import) or vanishes changes the value
                # without the market moving: portfolio alerts shift with it.
                self.conn.execute(
                    """UPDATE alerts SET baseline = baseline + ? * (
                           SELECT h.qty FROM holdings h WHERE h.user_id = alerts.user_id AND h.hash_name = ?)
                       WHERE hash_name IS NULL AND user_id IN (SELECT user_id FROM holdings WHERE hash_name = ?)""",
                    (net_cents(cents or 0) - net_cents(old or 0), hash_name, hash_name),
                )
            self.conn.execute(
                "INSERT INTO prices (hash_name, cents, updated_at, checked_at, buy_order_cents, "
                "sell_order_cents, buy_orders, sell_listings) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(hash_name) DO UPDATE SET cents = excluded.cents, "
                "updated_at = excluded.updated_at, checked_at = excluded.checked_at, "
                "buy_order_cents = excluded.buy_order_cents, sell_order_cents = excluded.sell_order_cents, "
                "buy_orders = excluded.buy_orders, sell_listings = excluded.sell_listings",
                (hash_name, cents, at, at, buy_order_cents, sell_order_cents, buy_orders, sell_listings),
            )
            # Each successful fetch replaces that UTC day's previous observation, so
            # the last one before midnight is the day's close. "No price" is kept
            # too, as NULL: the chart must drop the item then, as Home does.
            self.conn.execute(
                "INSERT INTO price_history (hash_name, day, cents) VALUES (?, ?, ?) "
                "ON CONFLICT(hash_name, day) DO UPDATE SET cents = excluded.cents",
                (hash_name, self._utc_day(at), cents),
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
                "INSERT INTO prices (hash_name, cents, updated_at, checked_at) "
                "VALUES (?, NULL, NULL, ?) ON CONFLICT(hash_name) DO UPDATE SET "
                "checked_at = excluded.checked_at",
                (hash_name, time.time() if at is None else at),
            )
