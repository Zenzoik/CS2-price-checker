# Ideas

Possible features we have researched but not built. Decide on them from user feedback. The plan with priorities is in [roadmap.md](roadmap.md).

## Instant price estimates after an inventory import

*Partly built (2026-09-29):* the catalogue (`catalog.py`) downloads this file daily and inline mode shows "≈" estimates from it for items nobody tracks, with the USD→UAH ratio learned as sketched below. Using the estimates for imports is still open.

**Problem.** After an import, the Mini App prices each item with its own Steam request, at about 1.5 s per item (≈40 items per minute). A 64-item import takes about 1.5 minutes and a 300-item one about 7.5 minutes. Home shows the progress, but the portfolio value keeps changing until the pass finishes.

**Idea.** Show an estimate ("≈") for every imported item right away, and replace it with the exact Steam price as the refresh reaches each item.

**Source found (2026-09-28):** `https://prices.csgotrader.app/latest/steam.json`, the price file behind the csgotrader browser extension.
- One request, about 540 KB and about 0.1 s, covers about 34,000 CS2 items, keyed by `market_hash_name`.
- Each entry holds Steam **median sale prices in USD**, for example `"Snakebite Case": {"last_24h": 0.74, "last_7d": 0.72, "last_30d": 0.8, "last_90d": 0.94}`.
- It is free and needs no key. Its terms and uptime are not guaranteed, so treat it as best-effort.

**Why it isn't built yet:**
- It is a third-party dependency. We want to hear from users first.
- The prices are in USD, and they are median *sales*, not the current lowest listing, so they are only approximate.
  - Converting to UAH would need a rate. We could learn it from items we already priced exactly through the order book (median of UAH ÷ USD), but it would still differ from Steam's own prices.
  - Checked: Snakebite Case was 0.74 USD there and 25 ₴ lowest listing on Steam.

**Sketch if we build it:**
1. Download the file at most every few hours and keep it in memory (or a table).
2. On import, give items that have never been priced an `estimate_cents` computed with the learned USD→UAH ratio.
3. Show estimates with a "≈" mark and leave them out of profit. The existing refresh then replaces them with exact prices.
4. Optionally use the estimates to price the most valuable items first.

## Other speed-ups we checked

- **Steam market search** (`/market/search/render`, anonymous) returns at most 10 results per request and only in USD. Bulk pricing through it would not beat one order book request per item.
- **priceoverview as a second source** is already built: `PriceService` runs it next to the order book, each with its own Steam limit.
  - The production IP currently gets `429` on it no matter which User-Agent is sent, so it sits out in 15-minute cooldowns.
  - It speeds imports up (about 1.5×) on hosts where Steam allows it.
