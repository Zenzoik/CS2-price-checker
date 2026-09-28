# Roadmap

The Telegram Mini App's planned features, in the order we intend to build them. Each phase lists what it needs from earlier phases. The effort figure (S / M / L) is a rough relative size, not a time estimate.

Research that led to some of these items, including options we rejected for now, is in [ideas.md](ideas.md).

| Phase | Theme | Items |
|---|---|---|
| 0 | Don't lose data, know when things break | Daily DB backups · Admin alerts |
| 1 | See how the portfolio moves | Price history · Portfolio chart · 24h / 7d change · Break-even price · Liquidity |
| 2 | Let the bot come to the user | Threshold alerts · Watchlist · Daily / weekly digest |
| 3 | Track the whole life of an investment | Sales and realized profit · Inventory auto-sync |
| 4 | Growth and convenience | Share card · Folders / several accounts · Export · Broadcast |
| Backlog | Decide from feedback | Instant estimates after an import (third-party prices) |

---

## Phase 0: operations

Do this first. It is small, and every later phase writes more data we would not want to lose.

### 0.1 Daily database backups (S) ✅ done
- **Why:** backups are made by hand only before risky deploys. A disk failure or a bad migration would lose every portfolio.
- **What was built:** the service backs itself up (`cs2tracker/app/backup.py`, scheduled by the health monitor) instead of a systemd timer.
  - Once a day it writes an integrity-checked copy through SQLite's online backup API into `backups/` next to the database, and keeps 14 of them (`CS2BOT_BACKUP_DIR`, `CS2BOT_BACKUP_KEEP`).
  - Running inside the service means the same (dynamic) user owns every file. A root timer opening the WAL database could leave root-owned `-shm`/`-wal` files the service then can't open.
- **Still open:** the backups live on the same disk as the database. An off-server copy, such as rsync to another host, is a follow-up.
- **Done when:** a backup appears every day, a restore has been tested once on a copy, and the steps are written in the README.

### 0.2 Admin alerts (S) ✅ done
- **Why:** today we only notice that Steam is failing, or that prices stopped updating, when the app looks wrong.
- **What:** the bot messages everyone in `CS2BOT_ADMINS` when:
  - a refresh pass hasn't finished for more than 1 hour;
  - Steam answers 429 or errors for 30 minutes in a row;
  - the service restarts after a crash.
- The same condition alerts at most once an hour.
- **Done when:** these conditions, forced in a test, produce exactly one message each.

---

## Phase 1: history and insight

This phase adds the price history that phases 2 and 3 build on.

### 1.1 Price history (M): foundation
- **What:** a `price_history (hash_name, day, cents)` table holding one closing price per item per UTC day. The existing refresh fills it, so there are no extra Steam requests.
- **Decisions:**
  - One row per day, not every refresh: 10-minute points aren't worth the storage.
  - Keep the history forever. At about 1 KB per item per year it costs almost nothing.
- **Done when:** after a few days every held item has one row per day, and a test shows that a restart doesn't create duplicate rows.

### 1.2 Portfolio value chart (M)
- **What:** a line chart of total net value on Home, above the list, with a 7d / 30d / All switch. Tapping a day shows its value.
- **Decisions:**
  - The chart shows **value**, not profit. On days when items were added or removed, value jumps for reasons that aren't market moves. We mark those days on the chart rather than hide the jumps.
  - Follow the `dataviz` skill: one series, no legend, a hover or tap layer.
- **Done when:** the chart matches a hand calculation over the seeded history, in both themes.

### 1.3 24h / 7d change per item (S)
- **What:** each row shows the change since yesterday next to the P&L. Sorting gains "Change, 24h".
- **Depends on:** 1.1.

### 1.4 Break-even price (S)
- **What:** the item screen shows the price at which selling returns what was paid after the 15% fee (`buy_price × 1.15`), for example "Break-even: 29,90 ₴". It is hidden when no price paid is known.
- **Depends on:** nothing, so it can ship at any time.

### 1.5 Liquidity (S)
- **What:** the item screen shows listing and buy-order counts and the spread between them. The order book request already returns these counts.
- **Why:** it answers "can I actually sell 150 of these?".
- **Decision:** the counts come from the order book only. On hosts where the priceoverview source served the price, the counts are absent.

---

## Phase 2: notifications

These features let the bot bring users back to the app. They depend on 1.1 for "since yesterday" style triggers. Plain price thresholds work without it.

### 2.1 Threshold alerts (M), the owner's request
- **What:** "Notify me when…" on the item screen, and the same for the whole portfolio on Home. Each alert is one field and a direction.
- **Alert types:**
  - Item: price paid +/− X %; price per item above or below X ₴.
  - Portfolio: total value +/− X % or X ₴, measured from when the alert was set.
- **Decisions:**
  - Alerts are checked inside the existing refresh pass, so they cost no extra Steam requests. The delay is up to one refresh interval, about 10 minutes.
  - An alert fires **once**. It re-arms only after the value moves back across the threshold, so a price hovering near it can't spam.
  - Delivery goes through the bot. Users who never pressed `/start` get the `requestWriteAccess` prompt when they create their first alert.
  - Every alert message has an "Open portfolio" button.
- **Limits:** 20 alerts per user.
- **Done when:** crossing a threshold sends exactly one message, crossing back and forth sends one per crossing, and alerts can be listed, edited and deleted.

### 2.2 Watchlist (S, built on 2.1)
- **What:** items the user doesn't own, with a price alert, for example "tell me when Kilowatt is below 40 ₴". They show in a separate "Watching" section with no quantity.
- **Decision:** a watched item is a holding with `qty = 0`, so it reuses price refresh and alerts. Totals and "invested" skip these rows.

### 2.3 Daily / weekly digest (S, needs 1.1)
- **What:** an optional bot message at a time the user chooses, for example: "Portfolio 14 795 ₴, +2.1 % today. Best: Kilowatt +6 %. Worst: Prisma 2 −3 %."
- **Decision:** it is off by default and offered once, after the user's first import or third added item.

---

## Phase 3: the full life of an investment

### 3.1 Sales and realized profit (M)
- **Why:** removing an item after selling it currently loses that result.
- **What:** "Sold…" on the item screen asks for quantity and price, reduces the position and records a sale. Home shows realized profit next to unrealized.
- **Decision:** realized profit uses the average price paid, the same basis as unrealized, so the two can be added.

### 3.2 Inventory auto-sync (M)
- **What:** after an import, remember the Steam profile. Once a day, re-read the inventory and send one message: "5 new items in your inventory, add them?", with a button that opens the import screen already filled in.
- **Decisions:**
  - Steam limits inventory requests per IP hard, so sync runs at most once a day per user, spread across the day, within the existing shared limiter.
  - It only offers new items. Items that disappeared are shown as "no longer in inventory, sold?", which links to 3.1. Nothing is removed automatically.

---

## Phase 4: growth and convenience

These are independent of each other. Pick them from feedback.

- **4.1 Share card (M):** an image with the portfolio value, P&L and top items, shared to chats or Stories (`shareMessage` / `shareToStory`). It includes the bot's link, so it doubles as promotion. Showing amounts is opt-in.
- **4.2 Folders / several accounts (M):** group holdings, for example "Main", "Alt", "Long-term", with a folder filter on Home.
- **4.3 Export (S):** CSV of holdings and sales. Optionally write to the Google Sheet format of the original CLI tracker.
- **4.4 Broadcast (S):** in the admin screen, send a message to all users who pressed `/start`, rate-limited to stay within Telegram's limits.

---

## Backlog: decide from user feedback

- **Instant estimates after an import:** third-party bulk Steam prices shown as "≈" until exact prices arrive. Details, the source and its trade-offs are in [ideas.md](ideas.md).

## Always

- Every feature ships with tests, a screenshot check in both themes and in ru/uk/en, and an adversarial review, as the Mini App and import did.
- Before any schema change, back up the production database (see [Phase 0.1](#01-daily-database-backups-s)).
