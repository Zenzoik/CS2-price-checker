"""Pictures the bot sends from inline mode: an item's price card and a portfolio card.

Drawn with Pillow in the style of the app's share card (dark, one accent), at
the 1200 x 630 size link previews use. Everything comes in as plain data, so
drawing never touches the database or the network and can run in a thread.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .notify import MONTHS, percent
from .notify import money as _money

W, H = 1200, 630
PAD = 60
FONTS = Path(__file__).parent / "fonts"

BG_TOP, BG_BOTTOM = (26, 29, 38), (11, 12, 16)
FG = (255, 255, 255)
HINT = (154, 160, 170)
UP, DOWN = (76, 208, 125), (255, 107, 97)
ACCENT = (36, 129, 204)
PANEL = (255, 255, 255, 16)

TEXTS = {
    "en": {"market": "CS2 · Steam Market", "sell": "Lowest Steam listing", "buy": "Top Steam buy order",
           "net": "you'd get {v}", "no_price": "No listings right now", "day": "24h", "week": "7d", "month": "30d",
           "listings": "For sale: {n}", "orders": "Buy orders: {n}", "no_history": "Price history starts once the item is tracked",
           "portfolio": "CS2 portfolio", "profit": "Profit on the price paid", "items": "{n} items",
           "footer": "Track your CS2 items in Telegram"},
    "ru": {"market": "CS2 · Торговая площадка Steam", "sell": "Мин. цена в Steam", "buy": "Макс. заявка в Steam",
           "net": "после комиссии {v}", "no_price": "Сейчас нет предложений", "day": "24 ч", "week": "7 д", "month": "30 д",
           "listings": "В продаже: {n}", "orders": "Заявок: {n}", "no_history": "История цен появится, когда предмет начнут отслеживать",
           "portfolio": "Портфель CS2", "profit": "Прибыль к цене покупки", "items": "предметов: {n}",
           "footer": "Следите за своими предметами CS2 в Telegram"},
    "uk": {"market": "CS2 · Торговий майданчик Steam", "sell": "Мін. ціна в Steam", "buy": "Макс. заявка в Steam",
           "net": "після комісії {v}", "no_price": "Зараз немає пропозицій", "day": "24 год", "week": "7 д", "month": "30 д",
           "listings": "У продажу: {n}", "orders": "Заявок: {n}", "no_history": "Історія цін з'явиться, коли предмет почнуть відстежувати",
           "portfolio": "Портфель CS2", "profit": "Прибуток до ціни купівлі", "items": "предметів: {n}",
           "footer": "Стежте за своїми предметами CS2 в Telegram"},
}


@dataclass
class ItemCard:
    name: str
    currency: str
    kind: str                      # "sell" | "buy": which Steam price `cents` is
    cents: int | None
    net_cents: int | None
    changes: list[tuple[str, float | None]]  # ("day" | "week" | "month", ratio)
    series: list[int] = field(default_factory=list)  # daily closes, oldest first
    listings: int | None = None
    orders: int | None = None
    icon: bytes | None = None
    day: tuple[int, int, int] | None = None  # (year, month, day) the price is from


@dataclass
class PortfolioCard:
    ratio: float | None            # profit on the price paid
    month: float | None            # the market's move over 30 days
    count: int
    top: list[tuple[str, float | None, bytes | None]]  # (name, ratio, icon)


def money(cents: int, currency: str, lang: str) -> str:
    """As in messages, but the thousands gap is a full-width space: the narrow one
    Russian and Ukrainian use all but vanishes at poster sizes ("1313 ₴")."""
    return _money(cents, currency, lang).replace("\u202f", "\u00a0")



def _font(weight: int, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"Inter-{weight}.ttf"), size)


def _tone(ratio: float | None):
    if ratio is None or abs(ratio) < 0.0005:
        return HINT
    return UP if ratio > 0 else DOWN


def _fit(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int, lines: int) -> list[str]:
    """Up to `lines` lines; the last ends in "…" when the text is longer."""
    words, out = text.split(), []
    while words and len(out) < lines:
        line = words.pop(0)
        while words and draw.textlength(f"{line} {words[0]}", font=font) <= width:
            line = f"{line} {words.pop(0)}"
        out.append(line)
    if words:
        out[-1] = _fit(draw, f"{out[-1]} {' '.join(words)}", font, width)
    return [_fit(draw, line, font, width) for line in out]


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG_BOTTOM)
    top, bottom = BG_TOP, BG_BOTTOM
    grad = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        grad.line([(0, y), (W, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    grad.rectangle([0, 0, W, 10], fill=ACCENT)
    return img, ImageDraw.Draw(img, "RGBA")


def _paste_icon(img: Image.Image, data: bytes | None, box: tuple[int, int, int, int]) -> bool:
    """Fits a Steam icon into `box` (contain), on its transparency; False when it can't be read."""
    if not data:
        return False
    try:
        icon = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return False
    x0, y0, x1, y1 = box
    icon.thumbnail((x1 - x0, y1 - y0), Image.LANCZOS)
    img.paste(icon, (x0 + (x1 - x0 - icon.width) // 2, y0 + (y1 - y0 - icon.height) // 2), icon)
    return True


def _jpeg(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88, optimize=True, progressive=False)
    return out.getvalue()


def _footer(draw: ImageDraw.ImageDraw, t: dict, bot: str | None, right: str | None = None) -> None:
    draw.text((PAD, H - 56), _fit(draw, t["footer"], _font(600, 26), 700), font=_font(600, 26), fill=FG, anchor="ls")
    if bot:
        draw.text((PAD, H - 22), f"t.me/{bot}", font=_font(600, 26), fill=ACCENT, anchor="ls")
    if right:
        draw.text((W - PAD, H - 22), right, font=_font(400, 24), fill=HINT, anchor="rs")


def _sparkline(draw: ImageDraw.ImageDraw, series: list[int], box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    lo, hi = min(series), max(series)
    span = max(hi - lo, max(hi, 1) * 0.04)
    floor = lo - (span - (hi - lo)) / 2
    pts = [(x0 + i * (x1 - x0) / (len(series) - 1), y1 - (v - floor) * (y1 - y0) / span) for i, v in enumerate(series)]
    colour = _tone(series[-1] / series[0] - 1 if series[0] else None)
    draw.polygon(pts + [(x1, y1), (x0, y1)], fill=colour + (28,))
    draw.line(pts, fill=colour, width=4, joint="curve")
    lx, ly = pts[-1]
    draw.ellipse([lx - 8, ly - 8, lx + 8, ly + 8], fill=colour)


def item_card(card: ItemCard, lang: str, bot: str | None) -> bytes:
    t = TEXTS.get(lang, TEXTS["en"])
    img, draw = _canvas()

    # Left: the picture on a soft panel.
    panel = (PAD, 96, PAD + 380, 96 + 380)
    draw.rounded_rectangle(panel, radius=32, fill=PANEL)
    if not _paste_icon(img, card.icon, (panel[0] + 30, panel[1] + 30, panel[2] - 30, panel[3] - 30)):
        draw.rounded_rectangle((panel[0] + 120, panel[1] + 120, panel[2] - 120, panel[3] - 120), radius=24,
                               fill=(255, 255, 255, 20))

    # Right: what it is and what it costs.
    x, width = PAD + 380 + 50, W - PAD - (PAD + 380 + 50)
    draw.text((x, 70), t["market"], font=_font(600, 26), fill=HINT, anchor="lt")
    name_font = _font(800, 46)
    lines = _wrap(draw, card.name, name_font, width, 2)
    y = 110
    for line in lines:
        draw.text((x, y), line, font=name_font, fill=FG, anchor="lt")
        y += 54
    y += 12
    if card.cents is None:
        draw.text((x, y), t["no_price"], font=_font(800, 56), fill=HINT, anchor="lt")
        y += 76
    else:
        price = money(card.cents, card.currency, lang)
        size = 88
        while size > 56 and draw.textlength(price, font=_font(800, size)) > width:
            size -= 6
        draw.text((x, y), price, font=_font(800, size), fill=FG, anchor="lt")
        y += size + 12
        sub = t[card.kind]
        if card.net_cents is not None:
            sub += " · " + t["net"].format(v=money(card.net_cents, card.currency, lang))
        draw.text((x, y), _fit(draw, sub, _font(400, 26), width), font=_font(400, 26), fill=HINT, anchor="lt")
        y += 42

    # The moves, as chips.
    chip_font = _font(600, 28)
    cx = x
    for key, ratio in card.changes:
        if ratio is None:
            continue
        label = f"{t[key]} {percent(ratio, lang)}"
        w = draw.textlength(label, font=chip_font) + 36
        if cx + w > W - PAD:
            break
        draw.rounded_rectangle((cx, y, cx + w, y + 46), radius=23, fill=_tone(ratio) + (40,))
        draw.text((cx + 18, y + 23), label, font=chip_font, fill=_tone(ratio), anchor="lm")
        cx += w + 12
    if cx > x:
        y += 46

    # A month of daily closes, under the picture and the numbers.
    chart = (PAD, 500, W - PAD, 548)
    if len(card.series) >= 2:
        _sparkline(draw, card.series, chart)
    depth = [t["listings"].format(n=f"{card.listings:,}".replace(",", " ")) if card.listings is not None else None,
             t["orders"].format(n=f"{card.orders:,}".replace(",", " ")) if card.orders is not None else None]
    depth = " · ".join(d for d in depth if d)
    if len(card.series) < 2:
        depth = depth or t["no_history"]
    if depth:
        draw.text((x, y + 16), _fit(draw, depth, _font(400, 24), width), font=_font(400, 24), fill=HINT, anchor="lt")

    date = None
    if card.day:
        year, month, day = card.day
        date = f"{day} {MONTHS.get(lang, MONTHS['en'])[month - 1]} {year}"
    _footer(draw, t, bot, date)
    return _jpeg(img)


def portfolio_card(card: PortfolioCard, lang: str, bot: str | None) -> bytes:
    """Percent only, like the app's card with amounts off: it goes to other people."""
    t = TEXTS.get(lang, TEXTS["en"])
    img, draw = _canvas()
    draw.text((PAD, 80), t["portfolio"], font=_font(600, 30), fill=HINT, anchor="lt")
    if card.ratio is not None:
        big = percent(card.ratio, lang)
        draw.text((PAD, 130), big, font=_font(800, 120), fill=_tone(card.ratio), anchor="lt")
        draw.text((PAD, 272), t["profit"], font=_font(400, 28), fill=HINT, anchor="lt")
    else:
        draw.text((PAD, 130), t["items"].format(n=card.count), font=_font(800, 96), fill=FG, anchor="lt")
    y = 330
    if card.month is not None:
        draw.text((PAD, y), f"{t['month']}: {percent(card.month, lang)}", font=_font(600, 34),
                  fill=_tone(card.month), anchor="lt")
        y += 50
    if card.ratio is not None:
        draw.text((PAD, y), t["items"].format(n=card.count), font=_font(400, 28), fill=HINT, anchor="lt")

    # The top positions on the right, as in the app's share card.
    rx, rw, row_h, gap = 640, W - PAD - 640, 96, 14
    for i, (name, ratio, icon) in enumerate(card.top[:4]):
        ry = 70 + i * (row_h + gap)
        draw.rounded_rectangle((rx, ry, rx + rw, ry + row_h), radius=22, fill=PANEL)
        if not _paste_icon(img, icon, (rx + 14, ry + 10, rx + 90, ry + row_h - 10)):
            draw.rounded_rectangle((rx + 20, ry + 18, rx + 80, ry + row_h - 18), radius=14, fill=(255, 255, 255, 20))
        side = percent(ratio, lang) if ratio is not None else ""
        side_font = _font(800, 32)
        side_w = draw.textlength(side, font=side_font) if side else 0
        if side:
            draw.text((rx + rw - 22, ry + row_h / 2), side, font=side_font, fill=_tone(ratio), anchor="rm")
        name_font = _font(600, 26)
        lines = _wrap(draw, name, name_font, int(rw - 110 - side_w - 40), 2)
        top = ry + row_h / 2 - (len(lines) * 32) / 2
        for j, line in enumerate(lines):
            draw.text((rx + 104, top + j * 32), line, font=name_font, fill=FG, anchor="lt")
    _footer(draw, t, bot)
    return _jpeg(img)
