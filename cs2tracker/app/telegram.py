"""The bot side: a menu button that opens the Mini App and a /start reply.

Long polling via the plain Bot API, so no public webhook endpoint is needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from urllib.parse import urlencode

import aiohttp

from .friends import display_name
from .friends import texts as friend_texts
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
        "inline_hint": "\n\nIn any chat, type @{bot} and an item's name to send its price card.",
    },
    "ru": {
        "welcome": "Сколько сейчас стоят ваши предметы CS2.\n\n"
                   "Добавьте, что купили и за сколько, — приложение следит за ценами Steam "
                   "и показывает прибыль.",
        "open": "Открыть портфель",
        "track": "Следить за своими предметами CS2",
        "menu": "Портфель",
        "private": "Извините, это приватный бот.",
        "inline_hint": "\n\nВ любом чате напишите @{bot} и название предмета — отправлю карточку с его ценой.",
    },
    "uk": {
        "welcome": "Скільки зараз коштують ваші предмети CS2.\n\n"
                   "Додайте, що купили і за скільки, — застосунок стежить за цінами Steam "
                   "і показує прибуток.",
        "open": "Відкрити портфель",
        "track": "Стежити за своїми предметами CS2",
        "menu": "Портфель",
        "private": "Вибачте, це приватний бот.",
        "inline_hint": "\n\nУ будь-якому чаті напишіть @{bot} і назву предмета — надішлю картку з його ціною.",
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
        self.supports_inline = False  # "@bot query" is switched on in @BotFather
        self.inline = None  # InlineMode, when the service runs it
        self._inline_tasks: dict[int, asyncio.Task] = {}

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

    async def prepare_share(self, user_id: int, photo_url: str, caption: str = "",
                            size: tuple[int, int] | None = None, thumb_url: str | None = None) -> str:
        """A prepared message for Telegram.WebApp.shareMessage (`caption` is HTML); raises BotApiError.

        `size` (width, height) lets the client lay the photo out without cropping it.
        `thumb_url`: a small copy; Telegram for iOS shows the sender that one.
        """
        photo = {
            "type": "photo", "id": photo_url.rsplit("/", 1)[-1][:64], "photo_url": photo_url,
            "thumbnail_url": thumb_url or photo_url, "caption": caption[:1000], "parse_mode": "HTML",
            "reply_markup": self._share_markup(user_id),
        }
        if size:
            photo["photo_width"], photo["photo_height"] = size
        result = await self.call("savePreparedInlineMessage", user_id=user_id, result=photo,
                                 allow_user_chats=True, allow_group_chats=True, allow_channel_chats=True)
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
        self.supports_inline = bool(isinstance(me, dict) and me.get("supports_inline_queries"))
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
                                          allowed_updates=["message", "inline_query", "chosen_inline_result"])
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
        if isinstance(update.get("inline_query"), dict):
            self._answer_inline(update["inline_query"])
            return
        chosen = update.get("chosen_inline_result")
        if isinstance(chosen, dict):  # only with inline feedback on in @BotFather
            user_id = (chosen.get("from") or {}).get("id")
            if self.store is not None and isinstance(user_id, int):
                self.store.log_event(user_id, "inline_sent")
            return
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
            if self.settings.allows(user["id"]):
                # A private chat exists now. Not for users a private bot turns
                # away: broadcasts skip them, so they'd only inflate the audience.
                self.store.set_prefs(user["id"], write_access=1)
        t = texts(user.get("language_code"))
        if not self.settings.allows(user["id"]):
            await self.call("sendMessage", chat_id=chat["id"], text=t["private"])
            return
        text = message.get("text") or ""
        # A friend's invite link: who it is from, and the app that takes it.
        if text.startswith("/start fr_") and self.store is not None:
            invite = self._friend_invite(text.split(maxsplit=1)[1][3:], user)
            if invite is not None:
                await self.call("sendMessage", chat_id=chat["id"], text=invite[0], reply_markup=invite[1])
                return
        # A card's "Track the price" button: that item, opened in the app.
        if text.startswith("/start ") and self.inline is not None:
            item = self.inline.start_payload(text.split(maxsplit=1)[1].strip(), user.get("language_code"))
            if item is not None:
                await self.call("sendMessage", chat_id=chat["id"], text=item[0], reply_markup=item[1])
                return
        # Any message gets the button: there is nothing else to talk about.
        welcome = t["welcome"]
        if self.supports_inline and self.username:
            welcome += t["inline_hint"].format(bot=self.username)
        await self.call("sendMessage", chat_id=chat["id"], text=welcome, reply_markup={
            "inline_keyboard": [[{"text": t["open"], "web_app": {"url": self.settings.public_url}}]],
        })

    def _friend_invite(self, code: str, user: dict) -> tuple[str, dict] | None:
        """The reply to /start fr_<code>; None for a code that isn't (or no longer is) one."""
        owner, expired = (self.store.invite_lookup(code) if re.fullmatch(r"[A-Za-z0-9_-]{8,32}", code)
                          else (None, False))
        if owner is None:
            return None
        lang = user.get("language_code")
        t = friend_texts(lang)
        url = self.settings.public_url
        if expired:
            text = t["expired"]
        elif owner == user["id"]:
            text = t["own_invite"]
        else:
            text = t["invite"].format(name=display_name(self.store.user_names(owner), lang, handle=True))
            url = f"{url}{'&' if '?' in url else '?'}{urlencode({'friend': code})}"
        return text, {"inline_keyboard": [[{"text": t["open"], "web_app": {"url": url}}]]}

    def _answer_inline(self, query: dict) -> None:
        """Answered in a task of its own, so a slow Steam search holds up no other update.

        A newer query from the same user (they kept typing) cancels the older one.
        """
        self.supports_inline = True  # a query came, so it is on (even if switched on since start)
        user_id = (query.get("from") or {}).get("id")
        if self.inline is None or not isinstance(user_id, int):
            return
        previous = self._inline_tasks.pop(user_id, None)
        if previous is not None:
            previous.cancel()

        async def answer() -> None:
            try:
                await self.inline.answer(query)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # e.g. the query expired while Steam answered
                log.info("Inline query not answered: %s", self._redact(repr(e)))
            finally:
                if self._inline_tasks.get(user_id) is task:
                    del self._inline_tasks[user_id]
        task = asyncio.ensure_future(answer())
        self._inline_tasks[user_id] = task
