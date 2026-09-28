"""Sends an admin's message to every user the bot may write to.

Paced well under Telegram's limit of about 30 messages a second, and
resumable: each recipient moves a cursor in the database, so a restart
carries on where it stopped instead of starting over or giving up.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from .db import Store

log = logging.getLogger(__name__)

SEND_GAP = 0.05   # 20 messages a second at most
IDLE_CHECK = 5    # seconds between looks for a new broadcast
BATCH = 50


class Broadcaster:
    def __init__(self, store: Store, send: Callable[[int, str], Awaitable[bool]], *,
                 allows: Callable[[int], bool] = lambda user_id: True, gap: float = SEND_GAP):
        self.store = store
        self.send = send
        self.allows = allows  # a private bot writes only to its allowed users
        self.gap = gap

    async def step(self) -> bool:
        """Sends the next batch of the running broadcast; False when there is none."""
        current = self.store.running_broadcast()
        if current is None:
            return False
        recipients = self.store.broadcast_recipients(current["id"], BATCH)
        if not recipients:
            self.store.finish_broadcast(current["id"])
            log.info("Broadcast %d done: %d sent, %d failed", current["id"], current["sent"], current["failed"])
            return True
        for user_id in recipients:
            running = self.store.running_broadcast()
            if running is None or running["id"] != current["id"]:
                return True  # cancelled meanwhile
            delivered = None
            if self.allows(user_id):
                try:
                    delivered = bool(await self.send(user_id, current["text"]))
                except Exception:  # one user must not stop the rest
                    log.exception("Broadcast to %s failed", user_id)
                    delivered = False
                await asyncio.sleep(self.gap)
            self.store.broadcast_progress(current["id"], user_id, delivered)
        return True

    async def run(self) -> None:
        while True:
            if not await self.step():
                await asyncio.sleep(IDLE_CHECK)
