"""Re-reads each user's Steam inventory once a day and tells them what changed.

Only the profile of the last import is read, at most once a day per user, and
only when the server-wide inventory budget (Steam's per-IP limit, shared with
users' own lookups) has a request to spare beyond the one kept for a user who
may be importing right now. New items are offered, items gone are asked
about ("sold?"); nothing is added or removed without the user.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from ..steam import PrivateInventory, ProfileNotFound, RateLimited, SteamError
from .db import Store
from .notify import language
from .prices import InventoryService, SteamBusy

log = logging.getLogger(__name__)

# Inventory requests left for users: the job only runs with this many to spare.
HEADROOM = 1
IDLE_CHECK = 60   # seconds between looks when nothing is due
GAP = 30          # seconds between two reads, so users' imports go first
RETRY_BUSY = 300
RETRY_FAILED = 3600
PAUSE_RATE_LIMITED = 900
LISTED = 3        # names spelled out in a message; the rest are counted

TEXTS = {
    "en": {
        "new": "📦 New in your Steam inventory ({n}): {names}. Add them to the portfolio?",
        "gone": "📤 No longer in your Steam inventory: {names}. Sold? Record the sale to keep its profit.",
        "more": "and {n} more",
    },
    "ru": {
        "new": "📦 Новое в инвентаре Steam ({n}): {names}. Добавить в портфель?",
        "gone": "📤 Пропало из инвентаря Steam: {names}. Продали? Отметьте продажу, чтобы сохранить прибыль.",
        "more": "и ещё {n}",
    },
    "uk": {
        "new": "📦 Нове в інвентарі Steam ({n}): {names}. Додати до портфеля?",
        "gone": "📤 Зникло з інвентарю Steam: {names}. Продали? Позначте продаж, щоб зберегти прибуток.",
        "more": "і ще {n}",
    },
}


class Deferred(Exception):
    """No inventory request to spare right now."""


def listing(names: list[str], lang: str) -> str:
    shown = ", ".join(names[:LISTED])
    return shown if len(names) <= LISTED else f"{shown} {TEXTS[lang]['more'].format(n=len(names) - LISTED)}"


def message(new: list[str], gone: dict[str, int], names: dict[str, str], lang: str) -> str | None:
    t = TEXTS[lang]
    lines = []
    if new:
        lines.append(t["new"].format(n=len(new), names=listing([names.get(n, n) for n in new], lang)))
    if gone:
        lines.append(t["gone"].format(names=listing([f"{names.get(n, n)} ×{q}" for n, q in gone.items()], lang)))
    return "\n\n".join(lines) or None


class InventorySync:
    def __init__(self, store: Store, inventories: InventoryService, limiter,
                 send: Callable[[int, str], Awaitable[bool]], *, clock: Callable[[], float] = time.time):
        self.store = store
        self.inventories = inventories
        self.limiter = limiter  # the server-wide one, keyed 0
        self.send = send
        self.clock = clock
        self.paused_until = 0.0

    def _charge(self, cost: int) -> None:
        if not self.limiter.allows(0, cost + HEADROOM):
            raise Deferred
        self.limiter.take(0, cost)

    async def sync_next(self) -> bool:
        """Reads the most overdue inventory; False when there was nothing to do (or no room)."""
        now = self.clock()
        if now < self.paused_until:
            return False
        due = self.store.due_syncs(now)
        if not due:
            return False
        user_id, steamid = due[0]["user_id"], due[0]["steamid"]
        try:
            _, items = await self.inventories.load(("steamid", steamid), self._charge)
        except Deferred:
            return False
        except PrivateInventory:
            self.store.postpone_sync(user_id, steamid, now + 86400, "private")
            return True
        except ProfileNotFound:
            self.store.postpone_sync(user_id, steamid, now + 86400, "not_found")
            return True
        except RateLimited as e:
            log.warning("Steam rate-limited an inventory sync: %s", e)
            self.paused_until = now + PAUSE_RATE_LIMITED
            self.store.postpone_sync(user_id, steamid, now + RETRY_FAILED)
            return True
        except SteamBusy:
            self.store.postpone_sync(user_id, steamid, now + RETRY_BUSY)
            return True
        except SteamError as e:
            log.warning("Inventory sync for %s failed: %s", user_id, e)
            self.store.postpone_sync(user_id, steamid, now + RETRY_FAILED)
            return True
        self.store.remember_items([(i.hash_name, i.name, i.icon_url) for i in items])
        new, gone = self.store.record_sync(user_id, steamid, {i.hash_name: i.qty for i in items}, now)
        if (new or gone) and self.store.prefs(user_id)["write_access"]:
            names = {i.hash_name: i.name for i in items}
            for name in gone:  # an item gone for good is not in this read
                if name not in names:
                    names[name] = (self.store.item(name) or (name,))[0]
            text = message(new, gone, names, language(self.store.user_language(user_id)))
            try:
                await self.send(user_id, text)
            except Exception:  # the app still shows it on Home
                log.exception("Could not message user %s about their inventory", user_id)
        return True

    async def run(self) -> None:
        while True:
            worked = await self.sync_next()
            await asyncio.sleep(GAP if worked else IDLE_CHECK)
