"""Runs the Telegram Mini App: web server, bot, and background price refresh."""

from __future__ import annotations

import asyncio
import logging
import signal
import sqlite3
import sys

import aiohttp
from aiohttp import web

from ..steam import SteamMarket
from .db import Store, StoreError
from .prices import PriceService
from .server import create_app
from .settings import SettingsError, load_app_settings
from .telegram import TelegramBot

log = logging.getLogger("cs2tracker")


async def supervise(name: str, job) -> None:
    """Keeps one background job alive; its failure never takes the web server down."""
    while True:
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s crashed, restarting in 30s", name)
        await asyncio.sleep(30)


async def serve() -> None:
    settings = load_app_settings()
    store = Store(settings.db_path, settings.currency)
    # A user waits on these calls, so give up quickly instead of CLI-style backoff.
    market = SteamMarket(request_delay=settings.request_delay, max_retries=2, server_retries=1,
                         backoff=5.0, max_backoff=20.0, timeout=10.0)
    prices = PriceService(store, market, currency=settings.currency, kind=settings.price,
                          refresh_minutes=settings.refresh_minutes)
    runner = web.AppRunner(create_app(settings, store, prices), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, settings.host, settings.port).start()
        log.info("Mini App on http://%s:%d (public: %s)", settings.host, settings.port, settings.public_url)
        async with aiohttp.ClientSession() as session:
            bot = TelegramBot(settings, session)
            await asyncio.gather(supervise("Price refresh", prices.run), supervise("Telegram bot", bot.run))
    finally:
        await runner.cleanup()
        store.close()


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
