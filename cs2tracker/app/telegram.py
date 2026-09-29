"""The bot side: a menu button that opens the Mini App and a /start reply.

Long polling via the plain Bot API, so no public webhook endpoint is needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

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
        "track": "Track your CS2 items",
        "menu": "Portfolio",
        "private": "Sorry, this bot is private.",
    },
    "ru": {
        "welcome": "Сколько сейчас стоят ваши предметы CS2.\n\n"
                   "Добавьте, что купили и за сколько, — приложение следит за ценами Steam "
                   "и показывает прибыль.",
        "open": "Открыть портфель",
        "track": "Следить за своими предметами CS2",
        "menu": "Портфель",
        "private": "Извините, это приватный бот.",
    },
    "uk": {
        "welcome": "Скільки зараз коштують ваші предмети CS2.\n\n"
                   "Додайте, що купили і за скільки, — застосунок стежить за цінами Steam "
                   "і показує прибуток.",
        "open": "Відкрити портфель",
        "track": "Стежити за своїми предметами CS2",
        "menu": "Портфель",
        "private": "Вибачте, це приватний бот.",
    },
}


def texts(language_code: str | None) -> dict[str, str]:
    return TEXTS.get((language_code or "")[:2].lower(), TEXTS["en"])


# Messages to CS2BOT_ADMINS from the health monitor. `{…}` come from the alert.
ADMIN_TEXTS = {
    "en": {
        "refresh_stuck": "⚠️ Prices haven't been refreshed for {minutes} min. The refresh loop may be stuck.",
        "refresh_stuck_ok": "✅ Price refresh is running again.",
        "steam_failing": "⚠️ Steam has been failing for {minutes} min, prices aren't updating.\nLast error: {error}",
        "steam_failing_ok": "✅ Steam answers again, prices are updating.",
        "backup_failed": "⚠️ Daily database backup failed: {error}",
        "backup_failed_ok": "✅ Database backup works again.",
        "restarted": "♻️ The service restarted after an unexpected stop.",
        "job_crashed": "💥 {job} crashed and is restarting: {error}",
    },
    "ru": {
        "refresh_stuck": "⚠️ Цены не обновлялись {minutes} мин. Возможно, завис цикл обновления.",
        "refresh_stuck_ok": "✅ Обновление цен снова работает.",
        "steam_failing": "⚠️ Steam отвечает ошибками уже {minutes} мин, цены не обновляются.\nПоследняя ошибка: {error}",
        "steam_failing_ok": "✅ Steam снова отвечает, цены обновляются.",
        "backup_failed": "⚠️ Ежедневный бэкап базы не удался: {error}",
        "backup_failed_ok": "✅ Бэкап базы снова работает.",
        "restarted": "♻️ Сервис перезапустился после неожиданной остановки.",
        "job_crashed": "💥 {job} упал и перезапускается: {error}",
    },
    "uk": {
        "refresh_stuck": "⚠️ Ціни не оновлювалися {minutes} хв. Можливо, завис цикл оновлення.",
        "refresh_stuck_ok": "✅ Оновлення цін знову працює.",
        "steam_failing": "⚠️ Steam відповідає помилками вже {minutes} хв, ціни не оновлюються.\nОстання помилка: {error}",
        "steam_failing_ok": "✅ Steam знову відповідає, ціни оновлюються.",
        "backup_failed": "⚠️ Щоденний бекап бази не вдався: {error}",
        "backup_failed_ok": "✅ Бекап бази знову працює.",
        "restarted": "♻️ Сервіс перезапустився після неочікуваної зупинки.",
        "job_crashed": "💥 {job} впав і перезапускається: {error}",
    },
}


class BotApiError(Exception):
    pass


class TelegramBot:
    def __init__(self, settings: AppSettings, session: aiohttp.ClientSession, store=None):
        self.settings = settings
        self.session = session
        self.store = store  # usage statistics; optional
        self.username: str | None = None  # from getMe, for t.me links

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

    async def upload(self, method: str, field: str, filename: str, data: bytes, content_type: str, **params):
        """A Bot API call that sends a file (multipart); errors as in `call`."""
        url = API.format(token=self.settings.bot_token, method=method)
        form = aiohttp.FormData()
        for key, value in params.items():
            form.add_field(key, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
        form.add_field(field, data, filename=filename, content_type=content_type)
        try:
            async with self.session.post(url, data=form, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                status = resp.status
                result = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise BotApiError(f"{method}: {self._redact(f'{type(e).__name__}: {e}')}") from None
        if not isinstance(result, dict) or not result.get("ok"):
            description = result.get("description") if isinstance(result, dict) else None
            raise BotApiError(f"{method}: {description or f'HTTP {status}'}")
        return result.get("result")

    def _open_button(self, user_id: int) -> dict:
        lang = self.store.user_language(user_id) if self.store is not None else None
        return {"inline_keyboard": [[{"text": texts(lang)["open"], "web_app": {"url": self.settings.public_url}}]]}

    async def send_document(self, user_id: int, filename: str, data: bytes, caption: str = "") -> None:
        """Raises BotApiError, e.g. when the user blocked the bot."""
        await self.upload("sendDocument", "document", filename, data, "text/csv",
                          chat_id=str(user_id), caption=caption[:1000])

    async def send_photo(self, user_id: int, data: bytes, caption: str = "") -> None:
        """A picture the user can forward; `caption` is HTML. Raises BotApiError."""
        await self.upload("sendPhoto", "photo", "portfolio.jpg", data, "image/jpeg", chat_id=str(user_id),
                          caption=caption[:1000], parse_mode="HTML", reply_markup=self._share_markup(user_id))

    def _share_markup(self, user_id: int) -> dict:
        # Forwarded or shared messages can't carry a web_app button: link to the bot.
        lang = self.store.user_language(user_id) if self.store is not None else None
        url = f"https://t.me/{self.username}" if self.username else self.settings.public_url
        return {"inline_keyboard": [[{"text": texts(lang)["track"], "url": url}]]}

    async def prepare_share(self, user_id: int, photo_url: str, caption: str = "") -> str:
        """A prepared message for Telegram.WebApp.shareMessage (`caption` is HTML); raises BotApiError."""
        result = await self.call("savePreparedInlineMessage", user_id=user_id, result={
            "type": "photo", "id": photo_url.rsplit("/", 1)[-1][:64], "photo_url": photo_url,
            "thumbnail_url": photo_url, "caption": caption[:1000], "parse_mode": "HTML",
            "reply_markup": self._share_markup(user_id),
        }, allow_user_chats=True, allow_group_chats=True, allow_channel_chats=True)
        return result["id"]

    def _redact(self, text: str) -> str:
        return text.replace(self.settings.bot_token, "<token>")

    async def notify_admins(self, key: str, values: dict) -> None:
        """Sends an alert to every admin, in their language, with a button to the app."""
        for admin in sorted(self.settings.admins):
            lang = self.store.user_language(admin) if self.store is not None else None
            template = ADMIN_TEXTS.get((lang or "")[:2].lower(), ADMIN_TEXTS["en"]).get(key)
            if template is None:
                continue
            text = template.format(**{k: str(v)[:300] for k, v in values.items()})
            try:
                await self.call("sendMessage", chat_id=admin, text=self._redact(text), reply_markup={
                    "inline_keyboard": [[{"text": texts(lang)["open"], "web_app": {"url": self.settings.public_url}}]],
                })
            except BotApiError as e:  # e.g. the admin never pressed /start
                log.warning("Could not alert admin %s: %s", admin, e)

    async def message_user(self, user_id: int, text: str) -> bool:
        """A message from the bot with an "Open portfolio" button; False if Telegram refused it."""
        params = {"chat_id": user_id, "text": text[:4000], "reply_markup": self._open_button(user_id)}
        try:
            try:
                await self.call("sendMessage", **params)
            except BotApiError as e:
                # "Too Many Requests: retry after N": wait once (briefly) and retry.
                wait = re.search(r"retry after (\d+)", str(e))
                if not wait or int(wait.group(1)) > 60:
                    raise
                await asyncio.sleep(int(wait.group(1)))
                await self.call("sendMessage", **params)
        except BotApiError as e:
            # Blocked, or never pressed Start: the app asks for permission again.
            log.warning("Could not message user %s: %s", user_id, e)
            if self.store is not None and "forbidden" in str(e).lower():
                self.store.set_prefs(user_id, write_access=0)
            return False
        return True

    async def setup(self) -> None:
        me = await self.call("getMe")
        self.username = me.get("username") if isinstance(me, dict) else None
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
            self.store.set_prefs(user["id"], write_access=1)  # a private chat exists now
        t = texts(user.get("language_code"))
        if not self.settings.allows(user["id"]):
            await self.call("sendMessage", chat_id=chat["id"], text=t["private"])
            return
        # Any message gets the button: there is nothing else to talk about.
        await self.call("sendMessage", chat_id=chat["id"], text=t["welcome"], reply_markup={
            "inline_keyboard": [[{"text": t["open"], "web_app": {"url": self.settings.public_url}}]],
        })
