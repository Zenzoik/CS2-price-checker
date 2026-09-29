"""What friends see of each other's portfolios.

Each user picks one of two views for all their friends (prefs.friends_view):

- "percent": the portfolio's change over 24 h / 7 d / 30 d, in percent: market
  moves only, which anyone who knows the holdings (a public Steam inventory) could
  work out anyway. No profit: with the holdings known, profit in percent gives the
  price paid away, and each purchase's too. No amounts, no items.
- "full": the whole portfolio: value, profit in money, and every item with its
  quantity, market price and price paid.

Folders, single sales, alerts and the Steam profile are never shown.
"""

from __future__ import annotations

from .db import Store, net_cents

VIEWS = ("percent", "full")

TEXTS = {
    "en": {
        "accepted": "👋 {name} is now your friend: you can see each other's results in the app.",
        "invite": "{name} invites you to be friends in CS2 tracker: you'll see how each other's "
                  "investments are doing. Each of you chooses what to show: changes in percent, or the whole portfolio.",
        "own_invite": "This is your own invite link: send it to a friend.",
        "expired": "This invite link has expired: ask your friend for a new one.",
        "open": "Open",
        "someone": "A friend",
    },
    "ru": {
        "accepted": "👋 {name} теперь у вас в друзьях — вы видите результаты друг друга в приложении.",
        "invite": "{name} зовёт вас в друзья в CS2 tracker: вы будете видеть, как идут дела с инвестициями "
                  "друг у друга. Каждый сам выбирает, что показывать: изменения в процентах или весь портфель.",
        "own_invite": "Это ваша ссылка-приглашение — отправьте её другу.",
        "expired": "Эта ссылка-приглашение устарела — попросите у друга новую.",
        "open": "Открыть",
        "someone": "Друг",
    },
    "uk": {
        "accepted": "👋 {name} тепер у вас у друзях — ви бачите результати одне одного в застосунку.",
        "invite": "{name} кличе вас у друзі в CS2 tracker: ви бачитимете, як ідуть справи з інвестиціями "
                  "одне в одного. Кожен сам обирає, що показувати: зміни у відсотках чи весь портфель.",
        "own_invite": "Це ваше посилання-запрошення — надішліть його другові.",
        "expired": "Це посилання-запрошення застаріло — попросіть у друга нове.",
        "open": "Відкрити",
        "someone": "Друг",
    },
}


def texts(language_code: str | None) -> dict[str, str]:
    return TEXTS.get((language_code or "")[:2].lower(), TEXTS["en"])


def display_name(names: dict, language_code: str | None = None, *, handle: bool = False) -> str:
    """First name, else @username, else a neutral word: what the other side is shown.

    `handle`: the @username after the name too, where someone decides whether to
    trust a stranger's link (anyone can call themselves by a friend's first name).
    """
    first, username = names.get("first_name"), names.get("username")
    if first:
        return f"{first} (@{username})" if handle and username else first
    if username:
        return f"@{username}"
    return texts(language_code)["someone"]


def _money(cents: int | float | None) -> float | None:
    return None if cents is None else round(cents) / 100


def summary(store: Store, user_id: int, *, view: str, detail: bool = False) -> dict:
    """A user's results as a friend sees them in `view`. `detail`: the friend's own screen.

    Changes are market moves only (what was bought or sold in the period doesn't count);
    friends are ranked by the 30 days' one. Profit covers the items with both a market
    price and a price paid, as on Home.
    """
    full = view == "full"
    holdings = store.holdings(user_id)
    value = tracked_value = priced_cost = 0
    now = before = 0  # the day's change, over items priced both today and yesterday
    for h in holdings:
        if h.price_cents is None:
            continue
        worth = net_cents(h.price_cents) * h.qty
        value += worth
        if h.buy_cents is not None:
            tracked_value += worth
            priced_cost += h.buy_cents * h.qty
        if h.yesterday_cents:
            now += h.price_cents * h.qty
            before += h.yesterday_cents * h.qty
    out = {
        "view": view,
        "change_24h": now / before - 1 if before else None,
        "change_30d": store.portfolio_history(user_id, 30)["change_ratio"],
    }
    if full:
        out["pnl_ratio"] = tracked_value / priced_cost - 1 if priced_cost else None
        out["value"] = _money(value)
        out["pnl"] = _money(tracked_value - priced_cost) if priced_cost else None
        out["items_count"] = len(holdings)
    if not detail:
        return out
    out["change_7d"] = store.portfolio_history(user_id, 7)["change_ratio"]
    if not full:
        return out
    realized = store.realized(user_id)
    out["realized_ratio"] = realized["profit"] / realized["cost"] if realized["cost"] else None
    out["invested"] = _money(priced_cost)
    out["realized"] = _money(realized["profit"])
    items = [{
        "hash_name": h.hash_name, "name": h.name, "icon": h.icon, "qty": h.qty,
        "price": _money(h.price_cents), "buy_price": _money(h.buy_cents),
        "value": _money(net_cents(h.price_cents) * h.qty) if h.price_cents is not None else None,
        "pnl_ratio": (net_cents(h.price_cents) / h.buy_cents - 1
                      if h.price_cents is not None and h.buy_cents else None),
        "change_24h": (h.price_cents / h.yesterday_cents - 1
                       if h.price_cents is not None and h.yesterday_cents else None),
    } for h in holdings]
    items.sort(key=lambda it: (it["value"] is None, -(it["value"] or 0), it["name"]))
    out["items"] = items
    return out
