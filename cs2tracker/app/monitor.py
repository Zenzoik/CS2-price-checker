"""Health checks, daily backups and alerts to the admins.

Every few minutes the monitor looks at the price refresh and the backups. A
problem is reported to CS2BOT_ADMINS through the bot at most once an hour while
it lasts, and once more when it is over.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable

from .backup import DAY, BackupError, backup_database, latest_age

log = logging.getLogger(__name__)

CHECK_EVERY = 300
REPEAT_AFTER = 3600
PASS_OVERDUE = 3600      # no refresh pass finished for this long: the loop is stuck
FAILING_FOR = 1800       # items were being fetched, yet no price came back, for this long


class AdminAlerts:
    """De-duplicated problem / recovery messages; `send(key, vars)` does the delivery."""

    def __init__(self, send: Callable[[str, dict], Awaitable[None]], clock: Callable[[], float] = time.time,
                 repeat_after: float = REPEAT_AFTER):
        self.send = send
        self.clock = clock
        self.repeat_after = repeat_after
        self.active: dict[str, float] = {}  # ongoing problem -> when it was last sent
        self.sent_events: dict[str, float] = {}

    async def problem(self, key: str, **values) -> None:
        now = self.clock()
        last = self.active.get(key)
        if last is not None and now - last < self.repeat_after:
            return
        self.active[key] = now
        log.warning("Admin alert: %s %s", key, values)
        await self._deliver(key, values)

    async def resolved(self, key: str, **values) -> None:
        if self.active.pop(key, None) is not None:
            log.info("Admin alert resolved: %s", key)
            await self._deliver(f"{key}_ok", values)

    async def event(self, key: str, **values) -> None:
        """One-off notice with no recovery message, still at most one per hour per key."""
        now = self.clock()
        last = self.sent_events.get(key)
        if last is not None and now - last < self.repeat_after:
            return
        self.sent_events[key] = now
        log.warning("Admin notice: %s %s", key, values)
        await self._deliver(key, values)

    async def _deliver(self, key: str, values: dict) -> None:
        try:
            await self.send(key, values)
        except Exception:  # alerts must never take anything down
            log.exception("Could not deliver admin alert %s", key)


class HealthMonitor:
    def __init__(self, prices, alerts: AdminAlerts, *, db_path: Path, backup_dir: Path,
                 backup_keep: int = 14, clock: Callable[[], float] = time.time):
        self.prices = prices
        self.alerts = alerts
        self.db_path = db_path
        self.backup_dir = backup_dir
        self.backup_keep = backup_keep
        self.clock = clock
        self.started = clock()

    async def check(self) -> None:
        now = self.clock()
        tracked = bool(self.prices.store.tracked())

        # Refresh loop alive? It finishes a pass every few minutes even with nothing to do.
        last_pass = self.prices.last_pass or self.started
        if now - last_pass > PASS_OVERDUE:
            await self.alerts.problem("refresh_stuck", minutes=int((now - last_pass) // 60))
        else:
            await self.alerts.resolved("refresh_stuck")

        # Steam failing for a while: we keep asking, no price comes back. This also
        # catches "not found" for everything, which is how Steam fails under load.
        attempt, success = self.prices.last_attempt, self.prices.last_success
        failing = (tracked and attempt is not None and now - attempt < CHECK_EVERY * 2
                   and now - (success or self.started) > FAILING_FOR)
        if failing:
            await self.alerts.problem("steam_failing", minutes=int((now - (success or self.started)) // 60),
                                      error=self.prices.last_error or "")
        else:
            await self.alerts.resolved("steam_failing")

        await self.backup_if_due(now)

    async def backup_if_due(self, now: float | None = None) -> Path | None:
        now = self.clock() if now is None else now
        age = latest_age(self.backup_dir, now)
        if age is not None and age < DAY:
            return None
        try:
            path = await asyncio.to_thread(backup_database, self.db_path, self.backup_dir, self.backup_keep, now)
        except BackupError as e:
            log.error("%s", e)
            await self.alerts.problem("backup_failed", error=str(e))
            return None
        await self.alerts.resolved("backup_failed")
        return path

    async def run(self) -> None:
        while True:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Health check failed")
            await asyncio.sleep(CHECK_EVERY)
