"""Messages the bot sends on its own: price alerts and the daily / weekly digest.

Alerts are checked after each price refresh pass, so they cost no Steam calls.
An alert fires once, then waits until the value moves back past its threshold
(by a small margin, so a price hovering at the threshold can't spam) before it
can fire again. Everything one user has due in a pass goes out as one message.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from .db import Store, net_cents

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception

log = logging.getLogger(__name__)

# Re-arming needs the value back past the threshold by this much.
MONEY_MARGIN_PERCENT = 2
PERCENT_MARGIN_BP = 50
# A digest is sent at most this late (e.g. after a restart); later ones are skipped.
DIGEST_GRACE = 3 * 3600
DIGEST_CHECK_EVERY = 300
# Telegram allows about 30 messages a second in total; stay well under it.
SEND_GAP = 0.05

SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "UAH": "₴", "RUB": "₽", "KZT": "₸", "PLN": "zł",
           "BRL": "R$", "CNY": "¥", "JPY": "¥", "TRY": "₺", "INR": "₹", "KRW": "₩", "CAD": "CA$",
           "AUD": "A$", "CHF": "CHF", "CZK": "Kč"}
PREFIX_SYMBOLS = {"USD", "GBP", "BRL", "CAD", "AUD", "CNY", "JPY", "INR", "KRW"}

TEXTS = {
    "en": {
        "alerts": "🔔 Your alerts",
        "price_above": "{name}: {value}, at or above your {threshold}",
        "price_below": "{name}: {value}, at or below your {threshold}",
        "profit_above": "{name}: {value} on the price paid (alert at {threshold})",
        "profit_below": "{name}: {value} on the price paid (alert at {threshold})",
        "value_pct_above": "Portfolio: {total}, {value} since {since} (alert at {threshold})",
        "value_pct_below": "Portfolio: {total}, {value} since {since} (alert at {threshold})",
        "value_amount_above": "Portfolio: {total}, {value} since {since} (alert at {threshold})",
        "value_amount_below": "Portfolio: {total}, {value} since {since} (alert at {threshold})",
        "digest_daily": "📊 Portfolio: {total}", "digest_weekly": "📊 Your week: {total}",
        "change_daily": "{change} today", "change_weekly": "{change} this week",
        "best": "Best: {name} {change}", "worst": "Worst: {name} {change}",
        "decimal": ".", "group": ",",
    },
    "ru": {
        "alerts": "🔔 Уведомления",
        "price_above": "{name}: {value}, не ниже ваших {threshold}",
        "price_below": "{name}: {value}, не выше ваших {threshold}",
        "profit_above": "{name}: {value} к цене покупки (порог {threshold})",
        "profit_below": "{name}: {value} к цене покупки (порог {threshold})",
        "value_pct_above": "Портфель: {total}, {value} с {since} (порог {threshold})",
        "value_pct_below": "Портфель: {total}, {value} с {since} (порог {threshold})",
        "value_amount_above": "Портфель: {total}, {value} с {since} (порог {threshold})",
        "value_amount_below": "Портфель: {total}, {value} с {since} (порог {threshold})",
        "digest_daily": "📊 Портфель: {total}", "digest_weekly": "📊 Ваша неделя: {total}",
        "change_daily": "{change} за сегодня", "change_weekly": "{change} за неделю",
        "best": "Лучший: {name} {change}", "worst": "Худший: {name} {change}",
        "decimal": ",", "group": " ",
    },
    "uk": {
        "alerts": "🔔 Сповіщення",
        "price_above": "{name}: {value}, не нижче ваших {threshold}",
        "price_below": "{name}: {value}, не вище ваших {threshold}",
        "profit_above": "{name}: {value} до ціни купівлі (поріг {threshold})",
        "profit_below": "{name}: {value} до ціни купівлі (поріг {threshold})",
        "value_pct_above": "Портфель: {total}, {value} з {since} (поріг {threshold})",
        "value_pct_below": "Портфель: {total}, {value} з {since} (поріг {threshold})",
        "value_amount_above": "Портфель: {total}, {value} з {since} (поріг {threshold})",
        "value_amount_below": "Портфель: {total}, {value} з {since} (поріг {threshold})",
        "digest_daily": "📊 Портфель: {total}", "digest_weekly": "📊 Ваш тиждень: {total}",
        "change_daily": "{change} за сьогодні", "change_weekly": "{change} за тиждень",
        "best": "Найкращий: {name} {change}", "worst": "Найгірший: {name} {change}",
        "decimal": ",", "group": " ",
    },
}
MONTHS = {
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "ru": ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"],
    "uk": ["січ", "лют", "бер", "квіт", "трав", "черв", "лип", "серп", "вер", "жовт", "лист", "груд"],
}


def language(code: str | None) -> str:
    code = (code or "")[:2].lower()
    return code if code in TEXTS else "en"


def _number(value: float, lang: str, digits: int) -> str:
    text = f"{abs(value):,.{digits}f}"
    t = TEXTS[lang]
    text = text.replace(",", "\0").replace(".", t["decimal"]).replace("\0", t["group"])
    return ("−" if value < 0 else "") + text


def money(cents: int, currency: str, lang: str, sign: bool = False, whole: bool = False) -> str:
    units = cents / 100
    body = _number(abs(units), lang, 0 if whole or abs(units) >= 1000 else 2)
    symbol = SYMBOLS.get(currency, currency)
    text = f"{symbol}{body}" if lang == "en" and currency in PREFIX_SYMBOLS else f"{body} {symbol}"
    return (("+" if cents > 0 else "−" if cents < 0 else "") if sign else ("−" if cents < 0 else "")) + text


def approx_money(cents: int, currency: str, lang: str) -> str:
    """An estimate, without false precision: two significant digits from 10 up ("1 200 ₴", "36 ₴")."""
    units = cents / 100
    if units < 10:
        return money(cents, currency, lang)
    step = 10 ** max(0, len(str(int(units))) - 2)
    return money(round(units / step) * step * 100, currency, lang, whole=True)


def percent(ratio: float, lang: str) -> str:
    body = _number(abs(ratio) * 100, lang, 1)
    space = "" if lang == "en" else " "
    return ("+" if ratio > 0 else "−" if ratio < 0 else "") + body + space + "%"


def short_date(ts: float, tz: str | None, lang: str) -> str:
    moment = datetime.fromtimestamp(ts, zone(tz))
    return f"{moment.day} {MONTHS[lang][moment.month - 1]}"


def zone(name: str | None):
    if name and ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return timezone.utc


def valid_zone(name: str) -> bool:
    if ZoneInfo is None or not name or len(name) > 64:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


# -- evaluation -----------------------------------------------------------------

def current_value(alert: dict) -> int | None:
    """What the alert compares with its threshold now, in its own units; None: unknown."""
    metric = alert["metric"]
    price = alert.get("price_cents")
    if metric == "price":
        return price
    if metric == "profit":
        if price is None or not alert.get("qty") or not alert.get("buy_cents"):
            return None
        return round((net_cents(price) / alert["buy_cents"] - 1) * 10000)
    baseline = alert.get("baseline")
    value = alert.get("value_cents")
    if baseline is None or value is None:
        return None
    if metric == "value_pct":
        return round((value / baseline - 1) * 10000) if baseline > 0 else None
    return value - baseline  # value_amount


def margin(alert: dict) -> int:
    if alert["metric"] in ("profit", "value_pct"):
        return PERCENT_MARGIN_BP
    return max(1, abs(alert["threshold"]) * MONEY_MARGIN_PERCENT // 100)


def decide(alert: dict, value: int | None) -> str | None:
    """'fire', 'rearm' or None."""
    if value is None:
        return None
    threshold = alert["threshold"]
    met = value >= threshold if alert["above"] else value <= threshold
    if alert["armed"]:
        return "fire" if met else None
    back = value < threshold - margin(alert) if alert["above"] else value > threshold + margin(alert)
    return "rearm" if back else None


def describe(alert: dict, value: int, currency: str, lang: str, tz: str | None) -> str:
    t = TEXTS[lang]
    metric = alert["metric"]
    key = f"{metric}_{'above' if alert['above'] else 'below'}"
    if metric == "price":
        shown = {"value": money(value, currency, lang), "threshold": money(alert["threshold"], currency, lang)}
    elif metric in ("profit", "value_pct"):
        shown = {"value": percent(value / 10000, lang), "threshold": percent(alert["threshold"] / 10000, lang)}
    else:
        shown = {"value": money(value, currency, lang, sign=True),
                 "threshold": money(alert["threshold"], currency, lang, sign=True)}
    return t[key].format(name=alert.get("name") or "", total=money(alert.get("value_cents") or 0, currency, lang),
                         since=short_date(alert["created_at"], tz, lang), **shown)


# -- the job -------------------------------------------------------------------

# (user id, text) -> delivered?
Sender = Callable[[int, str], Awaitable[bool]]


class Notifier:
    def __init__(self, store: Store, send: Sender, *, currency: str, clock: Callable[[], float] = time.time):
        self.store = store
        self.send = send
        self.currency = currency
        self.clock = clock

    async def check_alerts(self) -> int:
        """Sends what fired; returns the number of messages sent."""
        fired: list[tuple[int, int]] = []
        rearmed: list[int] = []
        due: dict[int, list[tuple[dict, int]]] = {}
        reachable: dict[int, bool] = {}
        for alert in self.store.alerts():
            value = current_value(alert)
            action = decide(alert, value)
            if action == "fire":
                user = alert["user_id"]
                if user not in reachable:
                    reachable[user] = bool(self.store.prefs(user)["write_access"])
                if not reachable[user]:
                    continue  # stays armed until the user lets the bot write
            if action == "fire":
                fired.append((alert["id"], value))
                due.setdefault(alert["user_id"], []).append((alert, value))
            elif action == "rearm":
                rearmed.append(alert["id"])
        # Marked before sending: a failed or slow delivery must never repeat a message.
        self.store.alerts_fired(fired, rearmed, self.clock())
        sent = 0
        for user_id, items in due.items():
            lang = language(self.store.user_language(user_id))
            tz = self.store.prefs(user_id)["tz"]
            lines = [TEXTS[lang]["alerts"], ""] + [
                "• " + describe(alert, value, self.currency, lang, tz) for alert, value in items]
            if await self._deliver(user_id, "\n".join(lines)):
                sent += 1
            else:
                # Not delivered (blocked, rate-limited, network): try again on a later
                # pass. A block also clears write access, which stops the retries.
                self.store.rearm_alerts([alert["id"] for alert, _ in items])
            await asyncio.sleep(SEND_GAP)
        return sent

    def _digest_slot(self, prefs: dict, now: float) -> str | None:
        """The local date whose digest is due now, or None."""
        local = datetime.fromtimestamp(now, zone(prefs["tz"]))
        slot = local.replace(hour=prefs["digest_hour"], minute=0, second=0, microsecond=0)
        if local < slot or local - slot > timedelta(seconds=DIGEST_GRACE):
            return None
        if prefs["digest"] == "weekly" and slot.weekday() != 0:  # Mondays
            return None
        day = slot.date().isoformat()
        return None if prefs["digest_sent"] == day else day

    async def send_digests(self) -> int:
        sent = 0
        now = self.clock()
        for prefs in self.store.digest_users():
            day = self._digest_slot(prefs, now)
            if day is None:
                continue
            user_id = prefs["user_id"]
            if not prefs["write_access"]:
                continue
            self.store.set_prefs(user_id, digest_sent=day)  # before sending: never twice
            text = self.digest_text(user_id, weekly=prefs["digest"] == "weekly", now=now)
            if text:
                sent += await self._deliver(user_id, text)
                await asyncio.sleep(SEND_GAP)
        return sent

    def digest_text(self, user_id: int, weekly: bool, now: float | None = None) -> str | None:
        holdings = [h for h in self.store.holdings(user_id, now=now) if h.price_cents is not None]
        if not holdings:
            return None
        lang = language(self.store.user_language(user_id))
        t = TEXTS[lang]
        kind = "weekly" if weekly else "daily"
        total = sum(h.qty * net_cents(h.price_cents) for h in holdings)
        head = t[f"digest_{kind}"].format(total=money(total, self.currency, lang))
        history = self.store.portfolio_history(user_id, days=7 if weekly else 1, now=now)
        if history["change"] is not None and history["change_ratio"] is not None:
            change = f"{percent(history['change_ratio'], lang)} ({money(round(history['change'] * 100), self.currency, lang, sign=True)})"
            head += ", " + t[f"change_{kind}"].format(change=change)
        lines = [head + "."]
        moves = sorted(
            ((h.price_cents / ref - 1, h.name) for h in holdings
             if (ref := (h.week_ago_cents if weekly else h.yesterday_cents))),
        )
        if len(moves) >= 2:
            best, worst = moves[-1], moves[0]
            if best[0] > 0:
                lines.append(t["best"].format(name=best[1], change=percent(best[0], lang)))
            if worst[0] < 0:
                lines.append(t["worst"].format(name=worst[1], change=percent(worst[0], lang)))
        return "\n".join(lines)

    async def _deliver(self, user_id: int, text: str) -> int:
        try:
            ok = await self.send(user_id, text)
        except Exception:  # one user's delivery must not stop the others
            log.exception("Could not message user %s", user_id)
            return 0
        return int(bool(ok))

    async def run(self, passed: asyncio.Event) -> None:
        """Checks alerts after each price pass and digests every few minutes."""
        while True:
            try:
                await asyncio.wait_for(passed.wait(), DIGEST_CHECK_EVERY)
            except asyncio.TimeoutError:
                pass
            if passed.is_set():
                passed.clear()
                await self.check_alerts()
            await self.send_digests()
