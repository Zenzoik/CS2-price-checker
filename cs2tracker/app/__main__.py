"""Runs the Telegram Mini App: web server, bot, and background price refresh."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
import sqlite3
import sys

import aiohttp
from aiohttp import web

from ..steam import SteamMarket
from .backup import BackupError, backup_database
from .db import SCHEMA_VERSION, Store, StoreError, stored_schema
from .monitor import AdminAlerts, HealthMonitor
from .notify import Notifier
from .prices import InventoryService, PriceService
from .server import INVENTORY_LIMITER, create_app
from .settings import SettingsError, load_app_settings
from .sync import InventorySync
from .telegram import TelegramBot

log = logging.getLogger("cs2tracker")


async def supervise(name: str, job, alerts: AdminAlerts | None = None) -> None:
    """Keeps one background job alive; its failure never takes the web server down."""
    while True:
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("%s crashed, restarting in 30s", name)
            if alerts is not None:
                await alerts.event("job_crashed", job=name, error=f"{type(e).__name__}: {e}")
        await asyncio.sleep(30)


async def serve() -> None:
    settings = load_app_settings()
    backup_before_upgrade(settings)
    store = Store(settings.db_path, settings.currency)
    store.prune_events(time.time() - 180 * 86400)  # keep half a year of usage history
    # A user waits on these calls, so give up quickly instead of CLI-style backoff.
    market = SteamMarket(request_delay=settings.request_delay, max_retries=2, server_retries=1,
                         backoff=5.0, max_backoff=20.0, timeout=10.0)
    # A second client for priceoverview: its own Steam limit (about 20/min), used
    # alongside the order book so large imports get priced faster.
    overview = SteamMarket(request_delay=settings.request_delay, max_retries=1, server_retries=1,
                           backoff=5.0, max_backoff=20.0, timeout=10.0)
    prices = PriceService(store, market, currency=settings.currency, kind=settings.price,
                          refresh_minutes=settings.refresh_minutes, overview_market=overview)
    # Inventories get their own client: separate throttle, lock and Steam limits.
    inventories = InventoryService(SteamMarket(request_delay=settings.request_delay, max_retries=0,
                                               server_retries=0, timeout=15.0))
    app = create_app(settings, store, prices, inventories)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    # Left behind only if the process died without cleaning up: a crash. Written
    # only once we own the port, with our PID, so a second instance that fails to
    # start can neither raise a false alarm nor delete the running one's marker.
    marker = settings.db_path.with_name(settings.db_path.name + ".running")
    own_marker = False
    try:
        await web.TCPSite(runner, settings.host, settings.port).start()
        crashed_before = marker.exists()
        marker.write_text(str(os.getpid()))
        own_marker = True
        log.info("Mini App on http://%s:%d (public: %s)", settings.host, settings.port, settings.public_url)
        async with aiohttp.ClientSession() as session:
            bot = TelegramBot(settings, session, store)
            alerts = AdminAlerts(bot.notify_admins)
            monitor = HealthMonitor(prices, alerts, db_path=settings.db_path,
                                    backup_dir=settings.backups, backup_keep=settings.backup_keep)
            notifier = Notifier(store, bot.message_user, currency=settings.currency)
            # Shares the inventory budget with users' lookups, so it can't starve them.
            syncer = InventorySync(store, inventories, app[INVENTORY_LIMITER], bot.message_user)
            if crashed_before:
                await alerts.event("restarted")
            await asyncio.gather(
                supervise("Price refresh", prices.run, alerts),
                supervise("Telegram bot", bot.run, alerts),
                supervise("Health monitor", monitor.run, alerts),
                supervise("Notifications", lambda: notifier.run(prices.passed), alerts),
                supervise("Inventory sync", syncer.run, alerts),
            )
    finally:
        await runner.cleanup()
        store.close()
        if own_marker and _read(marker) == str(os.getpid()):
            marker.unlink(missing_ok=True)


def backup_before_upgrade(settings) -> None:
    """A schema migration only runs with a fresh copy to go back to.

    An older release cannot use an upgraded database, so rolling back a deploy
    means restoring this copy.
    """
    version = stored_schema(settings.db_path)
    if version is None or version >= SCHEMA_VERSION:
        return
    try:
        path = backup_database(settings.db_path, settings.backups, settings.backup_keep)
    except BackupError as e:
        raise StoreError(f"Not upgrading the database schema without a backup: {e}") from e
    log.info("Upgrading the database from schema v%d to v%d; backup: %s", version, SCHEMA_VERSION, path)


def _read(path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


async def run_until_stopped() -> None:
    task = asyncio.ensure_future(serve())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, task.cancel)
        except (NotImplementedError, RuntimeError):  # Windows
            pass
    try:
        await task
    except asyncio.CancelledError:
        log.info("Stopped.")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(run_until_stopped())
    except (SettingsError, StoreError) as e:
        log.error("%s", e)
        return 1
    except (OSError, sqlite3.Error) as e:  # port in use, unwritable database path, ...
        log.error("Cannot start: %s", e)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
