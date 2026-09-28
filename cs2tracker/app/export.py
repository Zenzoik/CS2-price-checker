"""CSV exports of a user's positions and sales, sent to them by the bot.

Spreadsheets split and parse CSV by locale: English ones expect commas and
a decimal point, Russian and Ukrainian ones semicolons and a decimal comma.
The file follows the user's language, and starts with a BOM so Excel reads
it as UTF-8.
"""

from __future__ import annotations

import csv
import io
import time

from .db import Store, net_cents
from .notify import language

HEADERS = {
    "en": {
        "holdings": ["Item", "Market hash name", "Folder", "Quantity", "Price paid, each", "Steam price",
                     "You'd get, each", "Value", "Profit", "Profit, %", "Currency"],
        "sales": ["Date", "Item", "Market hash name", "Quantity", "Received, each", "Price paid, each",
                  "Received", "Profit", "Currency"],
    },
    "ru": {
        "holdings": ["Предмет", "Market hash name", "Папка", "Количество", "Цена покупки за шт.", "Цена Steam",
                     "Получите за шт.", "Стоимость", "Прибыль", "Прибыль, %", "Валюта"],
        "sales": ["Дата", "Предмет", "Market hash name", "Количество", "Получено за шт.", "Цена покупки за шт.",
                  "Получено", "Прибыль", "Валюта"],
    },
    "uk": {
        "holdings": ["Предмет", "Market hash name", "Папка", "Кількість", "Ціна купівлі за шт.", "Ціна Steam",
                     "Отримаєте за шт.", "Вартість", "Прибуток", "Прибуток, %", "Валюта"],
        "sales": ["Дата", "Предмет", "Market hash name", "Кількість", "Отримано за шт.", "Ціна купівлі за шт.",
                  "Отримано", "Прибуток", "Валюта"],
    },
}


def _formats(lang: str):
    comma = lang != "en"
    delimiter = ";" if comma else ","

    def amount(cents: int | None) -> str:
        if cents is None:
            return ""
        text = f"{cents / 100:.2f}"
        return text.replace(".", ",") if comma else text

    def ratio(value: float | None) -> str:
        if value is None:
            return ""
        text = f"{value * 100:.1f}"
        return text.replace(".", ",") if comma else text

    return delimiter, amount, ratio


def _csv(rows: list[list], delimiter: str) -> bytes:
    out = io.StringIO()
    csv.writer(out, delimiter=delimiter, lineterminator="\r\n").writerows(rows)
    return ("﻿" + out.getvalue()).encode("utf-8")


def holdings_csv(store: Store, user_id: int, lang_code: str | None) -> bytes:
    lang = language(lang_code)
    delimiter, amount, ratio = _formats(lang)
    folders = {f["id"]: f["name"] for f in store.folders(user_id)}
    rows = [HEADERS[lang]["holdings"]]
    for h in store.holdings(user_id):
        net = None if h.price_cents is None else net_cents(h.price_cents)
        value = None if net is None else net * h.qty
        profit = None if net is None or h.buy_cents is None else (net - h.buy_cents) * h.qty
        pct = net / h.buy_cents - 1 if net is not None and h.buy_cents else None
        rows.append([h.name, h.hash_name, folders.get(h.folder_id, ""), h.qty, amount(h.buy_cents),
                     amount(h.price_cents), amount(net), amount(value), amount(profit), ratio(pct), store.currency])
    return _csv(rows, delimiter)


def sales_csv(store: Store, user_id: int, lang_code: str | None) -> bytes | None:
    """None when there are no sales."""
    sales = store.sales(user_id)
    if not sales:
        return None
    lang = language(lang_code)
    delimiter, amount, _ = _formats(lang)
    rows = [HEADERS[lang]["sales"]]
    for s in reversed(sales):  # oldest first, like a ledger
        profit = None if s["buy_cents"] is None else s["qty"] * (s["price_cents"] - s["buy_cents"])
        rows.append([time.strftime("%Y-%m-%d", time.gmtime(s["sold_at"])), s["name"], s["hash_name"], s["qty"],
                     amount(s["price_cents"]), amount(s["buy_cents"]), amount(s["qty"] * s["price_cents"]),
                     amount(profit), store.currency])
    return _csv(rows, delimiter)
