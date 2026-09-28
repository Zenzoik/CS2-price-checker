"""The bot side: a menu button that opens the Mini App and a /start reply.

Long polling via the plain Bot API, so no public webhook endpoint is needed.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from .settings import AppSettings

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT = 50

TEXTS = {
    "en": {
        "welcome": "See what your CS2 items are worth right now.\n\n"
                   "Add what you bought and what you paid — the app tracks Steam prices "
                   "and shows your profit.",
        "open": "Open portfolio",
        "menu": "Portfolio",
        "private": "Sorry, this bot is private.",
    },
    "ru": {
        "welcome": "Сколько сейчас стоят ваши предметы CS2.\n\n"
                   "Добавьте, что купили и за сколько, — приложение следит за ценами Steam "
                   "и показывает прибыль.",
        "open": "Открыть портфель",
        "menu": "Портфель",
        "private": "Извините, это приватный бот.",
    },
    "uk": {
        "welcome": "Скільки зараз коштують ваші предмети CS2.\n\n"
                   "Додайте, що купили і за скільки, — застосунок стежить за цінами Steam "
                   "і показує прибуток.",
        "open": "Відкрити портфель",
        "menu": "Портфель",
        "private": "Вибачте, це приватний бот.",
    },
}


def texts(language_code: str | None) -> dict[str, str]:
    return TEXTS.get((language_code or "")[:2].lower(), TEXTS["en"])


class BotApiError(Exception):
    pass


class TelegramBot:
    def __init__(self, settings: AppSettings, session: aiohttp.ClientSession, store=None):
        self.settings = settings
        self.session = session
        self.store = store  # usage statistics; optional

    async def call(self, method: str, **params):
        """Bot API call; every failure comes out as BotApiError with the token redacted."""
        url = API.format(token=self.settings.bot_token, method=method)
        timeout = aiohttp.ClientTimeout(total=POLL_TIMEOUT + 15)
        try:
            async with self.session.post(url, json=params, timeout=timeout) as resp:
                status = resp.status
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise BotApiError(f"{method}: {self._redact(f'{type(e).__name__}: {e}')}") from None
        if not isinstance(data, dict) or not data.get("ok"):
            description = data.get("description") if isinstance(data, dict) else None
            raise BotApiError(f"{method}: {description or f'HTTP {status}'}")
        return data.get("result")

    def _redact(self, text: str) -> str:
        return text.replace(self.settings.bot_token, "<token>")

    async def setup(self) -> None:
        # getUpdates does not work while a webhook is set.
        await self.call("deleteWebhook")
        await self.call("setChatMenuButton", menu_button={
            "type": "web_app", "text": TEXTS["en"]["menu"], "web_app": {"url": self.settings.public_url},
        })
        for lang, t in TEXTS.items():
            scope = {} if lang == "en" else {"language_code": lang}
            await self.call("setMyCommands", commands=[{"command": "start", "description": t["open"]}], **scope)

    async def run(self) -> None:
        while True:
            try:
                await self.setup()
                break
            except BotApiError as e:
                log.error("Telegram setup failed, retrying in 30s: %s", e)
                await asyncio.sleep(30)
        offset = 0
        while True:
            try:
                updates = await self.call("getUpdates", offset=offset, timeout=POLL_TIMEOUT,
                                          allowed_updates=["message"])
            except BotApiError as e:
                log.warning("getUpdates failed, retrying in 5s: %s", e)
                await asyncio.sleep(5)
                continue
            for update in updates if isinstance(updates, list) else []:
                try:
                    offset = max(offset, int(update["update_id"]) + 1)
                    await self.handle(update)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # one odd update must not stop the bot
                    log.warning("Could not answer an update: %s", self._redact(repr(e)))

    async def handle(self, update: dict) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat") or {}
        user = message.get("from") or {}
        if chat.get("type") != "private" or not isinstance(user.get("id"), int):
            return
        if self.store is not None:
            self.store.touch_user(user, "bot")
            self.store.log_event(user["id"], "bot")
        t = texts(user.get("language_code"))
        if not self.settings.allows(user["id"]):
            await self.call("sendMessage", chat_id=chat["id"], text=t["private"])
            return
        # Any message gets the button: there is nothing else to talk about.
        await self.call("sendMessage", chat_id=chat["id"], text=t["welcome"], reply_markup={
            "inline_keyboard": [[{"text": t["open"], "web_app": {"url": self.settings.public_url}}]],
        })
