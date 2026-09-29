"""Every CS2 item on the market, searchable in memory: the source of search results.

Steam's own search (`/market/search/render`) answers this server's IP with 429
most of the time, and each attempt held the Steam lock through its back-off,
so searching by Steam was slow and often empty. Instead:

* Names: `prices.csgotrader.app/latest/steam.json` (about 34,000 market hash
  names with Steam median prices in USD, 0.5 MB), once a day. The USD median
  and whether the item sold in the last 24 h only rank the results; they are
  never shown as a price.
* Pictures: ByMykel/CSGO-API, weekly. One picture per skin covers all its wears
  and StatTrak / Souvenir versions.

Both are third-party and best-effort: when they fail, the last copy in the
database stays, and Steam's search remains the fallback for names we lack.
"""

from __future__ import annotations

import asyncio
import bisect
import gzip
import json
import logging
import math
import re
import statistics
import sqlite3
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import aiohttp

from .db import Store

log = logging.getLogger(__name__)

NAMES_URL = "https://prices.csgotrader.app/latest/steam.json"
PICTURES_URL = "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/{}.json"
# Skins are grouped (one picture for every wear); the rest carry market hash names.
PICTURE_SOURCES = ("skins", "crates", "stickers", "keys", "agents", "collectibles", "music_kits", "patches",
                   "keychains", "graffiti", "highlights")
NAMES_EVERY = 86400
PICTURES_EVERY = 7 * 86400
RETRY_FAILED = 3600
CHECK_EVERY = 3600
HOLDERS_EVERY = 300
# An estimate compares an item with this many items of a similar price that were
# priced both ways within the last days (see `estimate`).
NEIGHBOURS = 7
RATE_WINDOW = 3 * 86400

WEARS = re.compile(r" \((Factory New|Minimal Wear|Field-Tested|Well-Worn|Battle-Scarred)\)$")
ICON = re.compile(r"/economy/image/([^/?\"#]+)")
WORD = re.compile(r"[0-9a-zа-яёіїєґ]+")


def normalize(text: str) -> list[str]:
    """Lower-case words; "AK-47" and "ak47" become the same word."""
    return WORD.findall(text.lower().replace("-", "").replace("™", " ").replace("★", " "))


# What players type for what the market calls it (typed word -> words in names).
ALIASES = {
    "navi": "natus vincere", "vp": "virtuspro", "fn": "factory new", "mw": "minimal wear", "ft": "fieldtested",
    "ww": "wellworn", "bs": "battlescarred", "st": "stattrak", "deagle": "desert eagle", "dl": "dragon lore",
    "p2k": "p2000", "kniv": "knife", "gloves": "gloves",
    # Common Russian and Ukrainian words; item names themselves are English only.
    "кейс": "case", "кейсы": "case", "кейси": "case", "капсула": "capsule", "наклейка": "sticker",
    "наліпка": "sticker", "стикер": "sticker", "перчатки": "gloves", "рукавички": "gloves", "нож": "knife",
    "ніж": "knife", "брелок": "charm", "граффити": "graffiti", "графіті": "graffiti", "ключ": "key",
    "нашивка": "patch", "авп": "awp", "калаш": "ak47", "дигл": "desert eagle", "керамбит": "karambit",
    "бабочка": "butterfly", "метелик": "butterfly", "штык": "bayonet", "фейд": "fade", "допплер": "doppler",
    "доплер": "doppler", "азимов": "asiimov", "драгон": "dragon", "лор": "lore", "редлайн": "redline",
    "вулкан": "vulcan", "хаул": "howl", "принтстрим": "printstream", "тигр": "tiger", "градиент": "gradient",
    "медуза": "medusa", "кейсхарден": "case hardened", "хайпер": "hyper", "бист": "beast",
}

# Stickers, patches, charms, graffiti and music: after weapons, knives, gloves and
# cases (what a name like "dragon" or "awp" usually means), unless named in the query.
MINOR = ("Sticker |", "Sticker Slab |", "Patch |", "Charm |", "Souvenir Charm |", "Sealed Graffiti |",
         "Graffiti |", "Music Kit |", "StatTrak™ Music Kit |")
MINOR_WORDS = {"sticker", "slab", "patch", "charm", "graffiti", "music", "kit"}


def query_words(text: str) -> list[str]:
    words = []
    for word in normalize(text):
        words += ALIASES.get(word, word).split()
    return words


def base_skin(name: str) -> str:
    """ "StatTrak™ AWP | Asiimov (Field-Tested)" -> "AWP | Asiimov" (as grouped skins are named)."""
    name = name.replace("★ StatTrak™ ", "★ ")
    for prefix in ("StatTrak™ ", "Souvenir "):
        name = name.removeprefix(prefix)
    return WEARS.sub("", name)


@dataclass(frozen=True)
class Entry:
    hash_name: str
    text: str                 # normalized words joined by spaces
    words: tuple[str, ...]
    variant: bool             # StatTrak™ / Souvenir: ranked after the plain item
    minor: bool               # a sticker, patch, charm, graffiti or music kit
    traded: bool              # sold on Steam in the last 24 h
    usd_cents: int


def _entry(hash_name: str, words: tuple[str, ...], traded: bool, usd_cents: int) -> Entry:
    return Entry(hash_name, " ".join(words), words, hash_name.startswith(("StatTrak™", "Souvenir", "★ StatTrak™")),
                 hash_name.startswith(MINOR), traded, usd_cents)


class Catalog:
    def __init__(self, store: Store, fetch: Callable[[str], Awaitable[bytes]] | None = None,
                 clock: Callable[[], float] = time.time):
        self.store = store
        self.fetch = fetch or _fetch
        self.clock = clock
        self.entries: list[Entry] = []
        self._names: set[str] = set()
        self._holders: dict[str, int] = {}
        self._holders_at = -HOLDERS_EVERY
        self._usd: dict[str, int] = {}
        self._samples: list[tuple[float, float]] = []  # (log USD median, log of our price ÷ it), by price
        self.load()

    # -- search ------------------------------------------------------------------

    def load(self) -> None:
        """Rebuilds the in-memory index from the database (catalogue plus every item seen)."""
        rows = self.store.catalog_rows()
        entries = []
        for hash_name, usd_cents, traded in rows:
            words = tuple(normalize(hash_name))
            if words:
                entries.append(_entry(hash_name, words, bool(traded), usd_cents or 0))
        self.entries = entries
        self._names = {e.hash_name for e in entries}
        self._usd = {e.hash_name: e.usd_cents for e in entries if e.usd_cents}
        log.info("Item catalogue: %d names", len(entries))

    def remember(self, names: list[str]) -> None:
        """Items first met elsewhere (a Steam search, an inventory) become searchable at once."""
        for name in names:
            words = tuple(normalize(name))
            if name not in self._names and words:
                self._names.add(name)
                self.entries.append(_entry(name, words, False, 0))

    def _holders_now(self) -> dict[str, int]:
        self._refresh_stats()
        return self._holders

    def _refresh_stats(self) -> None:
        now = time.monotonic()
        if now - self._holders_at > HOLDERS_EVERY:
            self._holders = self.store.holder_counts()
            self._samples = sorted((math.log(usd), math.log(cents / usd))
                                   for usd, cents in self.store.price_samples(self.clock() - RATE_WINDOW))
            self._holders_at = now

    def estimate(self, hash_name: str) -> int | None:
        """An approximate price in our currency (cents) from Steam's USD sale median, or None.

        The median converts at the ratio seen on the items of a similar price we
        priced exactly (the median over the `NEIGHBOURS` nearest in log price), so
        no exchange-rate source is needed. The ratio depends on the price: a
        $0.03 sticker lists at about 100 UAH per USD of median, a $30 skin at
        about 36. One ratio for all overestimated skins twofold; the nearest
        neighbours bring the typical error on items above $1 from 81 % to 7 %
        (measured leave-one-out on production prices). Shown only with "≈".
        """
        self._refresh_stats()
        usd = self._usd.get(hash_name)
        samples = self._samples
        if not usd or len(samples) < NEIGHBOURS:
            return None
        x = math.log(usd)
        i = bisect.bisect_left(samples, (x,))
        lo, hi = max(0, i - NEIGHBOURS), min(len(samples), i + NEIGHBOURS)
        near = sorted(samples[lo:hi], key=lambda s: abs(s[0] - x))[:NEIGHBOURS]
        return round(usd * math.exp(statistics.median(ratio for _, ratio in near)))

    def search(self, query: str, limit: int = 20) -> list[str]:
        """Names with a word starting with each typed word ("drag" -> "Dragon Lore"), best first:

        the exact name; weapons, knives, gloves and cases before stickers and
        the like; names ending in the last word ("case" -> cases); plain items
        before StatTrak / Souvenir (unless asked for); what users here hold;
        what sells on Steam today; the more valuable (what people look up).
        Then one of each skin before its other wears.
        """
        words = query_words(query)
        if not words:
            return []
        phrase = " ".join(words)
        wants_variant = any(w in ("stattrak", "souvenir") for w in words)
        wants_minor = any(w in MINOR_WORDS for w in words)
        holders = self._holders_now()
        found = []
        for e in self.entries:
            # Each typed word starts a word of the name: "red" finds Redline, not
            # "Battle-Scarred". The substring test first: it is what makes 34,000 names fast.
            if all(w in e.text for w in words) and all(any(x.startswith(w) for x in e.words) for w in words):
                found.append(((e.text != phrase, e.minor and not wants_minor,
                               # "case" -> "Kilowatt Case", not a knife's "Case Hardened"
                               not e.words[-1].startswith(words[-1]),
                               e.variant and not wants_variant,
                               -holders.get(e.hash_name, 0), not e.traded, -e.usd_cents, len(e.text)), e.hash_name))
        found.sort()
        # One of each skin first (its best wear), then the other wears: twenty
        # results of five skins beat twenty of one.
        seen: dict[str, int] = {}
        ranked = []
        for i, (key, name) in enumerate(found):
            base = base_skin(name)
            seen[base] = seen.get(base, -1) + 1
            ranked.append((key[:2], seen[base], i, name))  # within the exact / minor tiers
        ranked.sort()
        return [name for *_, name in ranked[:limit]]

    def __len__(self) -> int:
        return len(self.entries)

    # -- refresh -----------------------------------------------------------------

    async def run(self) -> None:
        while True:
            await self.refresh_if_due()
            await asyncio.sleep(CHECK_EVERY)

    async def refresh_if_due(self) -> None:
        now = self.clock()
        names_at, pictures_at, retry_at = self.store.catalog_times()
        pictures = now - (pictures_at or 0) > PICTURES_EVERY
        if (now - (names_at or 0) <= NAMES_EVERY and not pictures) or now < (retry_at or 0):
            return
        try:
            names = _parse_names(await self.fetch(NAMES_URL))
            icons = {}
            if pictures:
                for source in PICTURE_SOURCES:  # one at a time: the biggest is about 20 MB of JSON
                    icons.update(await asyncio.to_thread(_parse_pictures, await self.fetch(PICTURES_URL.format(source))))
        except Exception as e:  # keep the copy we have; try again in an hour
            log.warning("Item catalogue refresh failed: %s", e)
            self.store.set_catalog_retry(now + RETRY_FAILED)
            return
        await asyncio.to_thread(save_catalog, self.store.path, names, icons, now, pictures)
        self.load()


def _parse_names(data: bytes) -> dict[str, tuple[int, bool]]:
    """{hash name: (USD cents of the latest median, sold in the last 24 h)}."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    raw = json.loads(data)
    if not isinstance(raw, dict) or len(raw) < 1000:
        raise ValueError(f"unexpected names file ({type(raw).__name__}, {len(raw)} entries)")
    out = {}
    for name, prices in raw.items():
        if not isinstance(name, str) or not name or len(name) > 256:
            continue
        prices = prices if isinstance(prices, dict) else {}
        # The last day's median first: it predicted our prices best (leave-one-out).
        median = next((prices[k] for k in ("last_24h", "last_7d", "last_30d", "last_90d")
                       if isinstance(prices.get(k), (int, float))), 0)
        out[name] = (round(median * 100), isinstance(prices.get("last_24h"), (int, float)))
    return out


def _parse_pictures(data: bytes) -> dict[str, str]:
    """{market hash name, or a grouped skin's base name: Steam icon path}."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    out = {}
    for item in json.loads(data):
        if not isinstance(item, dict):
            continue
        m = ICON.search(str(item.get("image") or ""))
        name = item.get("market_hash_name") or item.get("name")
        if m and isinstance(name, str):
            out.setdefault(name, m.group(1))
    return out


def save_catalog(db_path, names: dict[str, tuple[int, bool]], icons: dict[str, str], now: float,
                 pictures: bool) -> None:
    """Writes a refresh on a connection of its own (it runs in a worker thread).

    A picture from Steam (search results, inventories) is kept over the
    catalogue's: it is the one Steam serves for that exact item.
    """
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        with conn:
            conn.execute("DELETE FROM catalog")
            conn.executemany("INSERT INTO catalog VALUES (?, ?, ?)",
                             [(n, usd, int(traded)) for n, (usd, traded) in names.items()])
            conn.executemany(
                "INSERT INTO items (hash_name, name, icon) VALUES (?, ?, ?) "
                "ON CONFLICT(hash_name) DO UPDATE SET icon = COALESCE(items.icon, excluded.icon)",
                [(n, n, icons.get(n) or icons.get(base_skin(n))
                  # A slab shows its sticker: the sticker's picture will do.
                  or icons.get(n.replace("Sticker Slab | ", "Sticker | ", 1))) for n in names],
            )
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('catalog_names_at', ?)", (str(now),))
            if pictures:
                conn.execute("INSERT OR REPLACE INTO meta VALUES ('catalog_pictures_at', ?)", (str(now),))
    finally:
        conn.close()


async def _fetch(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers={"Accept-Encoding": "gzip"}) as resp:
            resp.raise_for_status()
            return await resp.read()
