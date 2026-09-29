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

### 1.1 Price history (M): foundation — implemented, awaiting multi-day check
- **What:** a `price_history (hash_name, day, cents)` table holding one closing price per item per UTC day. The existing refresh fills it, so there are no extra Steam requests.
- **Decisions:**
  - One row per day, not every refresh: 10-minute points aren't worth the storage.
  - Keep the history forever. With one row a day, storage stays modest (typically tens of KB per item per year in SQLite).
- **Done when:** after a few days every held item has one row per day, and a test shows that a restart doesn't create duplicate rows.
- **Built:** each successful price fetch updates the current UTC day's row in `price_history` in the same transaction as the price cache. No extra Steam request is made. A fetch that finds no price stores NULL for the day, and a failed fetch stores nothing, the same rule as the price cache, so a past day's value never changes after the fact. Existing databases begin collecting history after the v3 migration; a schema upgrade now backs the database up first.

### 1.2 Portfolio value chart (M) — implemented, awaiting multi-day check
- **What:** a line chart of total net value on Home, with a 7d / 30d / All switch. Tapping a day shows its value. Home also shows the five most valuable positions; the full list lives in the Portfolio tab.
- **Decisions:**
  - The chart shows **value**, not profit. On days when items were added or removed, value jumps for reasons that aren't market moves. We mark those days on the chart rather than hide the jumps.
  - Follow the `dataviz` skill: one series, no legend, a hover or tap layer.
- **Done when:** the chart matches a hand calculation over the seeded history, in both themes.
- **Built:** quantity changes are recorded daily, including removals, and reconciled with the holdings on every start. The chart starts when this tracking begins; it does not invent quantities for earlier days. It has high/low and first/last-day labels, and a legend for the marked days. Above it, Home shows the change the market made over the period (additions, removals and items gaining or losing a price are left out) with a time-weighted percent, and the profit. "Week" and "Month" include the day they are measured from. The Home top five show the price change since yesterday when a previous close exists.

### 1.3 24h / 7d change per item (S) — implemented, awaiting multi-day check
- **What:** each row shows the change since yesterday next to the P&L. Sorting gains "Change, 24h".
- **Depends on:** 1.1.
- **Built:** Portfolio rows show the change since yesterday next to P&L and can be sorted by it. The item screen shows daily and seven-day changes when the corresponding UTC-day closing price exists. Missing history stays hidden; it is never filled from older data.

### 1.4 Break-even price (S) ✅ done
- **What:** the item screen shows the price at which selling returns what was paid after the 15% fee (`buy_price × 1.15`), for example "Break-even: 29,90 ₴". It is hidden when no price paid is known.
- **Depends on:** nothing, so it can ship at any time.
- **Built:** the item form calculates the minimum gross Steam listing price that returns the entered per-item cost after the 15% fee, rounded up to the smallest currency unit. It updates as the cost changes and is hidden when the cost is unknown.

### 1.5 Liquidity (S) ✅ done
- **What:** the item screen shows listing and buy-order counts and the spread between them. The order book request already returns these counts.
- **Why:** it answers "can I actually sell 150 of these?".
- **Decision:** the counts come from the order book only. On hosts where the priceoverview source served the price, the counts are absent.
- **Built:** each successful order book fetch stores listing and buy-order counts and the bid/ask spread. The item screen shows the available figures. A priceoverview quote clears these figures rather than presenting stale counts for that price.

---

## Phase 2: notifications

These features let the bot bring users back to the app. They depend on 1.1 for "since yesterday" style triggers. Plain price thresholds work without it.

### 2.1 Threshold alerts (M), the owner's request — implemented
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
- **Built:** `alerts` table (schema v7) and `cs2tracker/app/notify.py`, run as its own supervised job that wakes after each refresh pass that stored prices.
  - Re-arming needs the value back past the threshold by 2 % (money) or 0.5 percentage points (percent), so a price hovering at the threshold can't spam. An alert is marked as sent before the message goes out; if Telegram refuses it, the alert is re-armed and tried on a later pass. Users the bot may not write to (never pressed Start, declined, or blocked the bot) get nothing, and their alerts stay armed until they allow it. Sends are paced and honour Telegram's `retry after`.
  - Items with an alert are refreshed even when neither held nor watched. A profit alert is deleted with its position; price alerts stay.
  - Everything one user has due in a pass goes out as one message.
  - Portfolio alerts count price moves only, like the Home change: adding, editing or removing a position, and an item gaining or losing a price, shift the alert's baseline instead of triggering it. Editing an alert keeps its baseline.
  - Write access: set when the user writes to the bot or allows it in the app (`requestWriteAccess` on the first alert or digest), cleared when Telegram answers 403.
  - Entry points: the bell on Home (all alerts and the digest) and "Notify me…" on the item screen.

### 2.2 Watchlist (S, built on 2.1) — implemented
- **What:** items the user doesn't own, with a price alert, for example "tell me when Kilowatt is below 40 ₴". They show in a separate "Watching" section with no quantity.
- **Decision (changed while building):** a separate `watchlist` table rather than holdings with `qty = 0`. `holdings` has `CHECK (qty > 0)`, and every total, count, limit and history query would otherwise need a `qty > 0` filter. The refresh reads held and watched items together, so watched items still cost no extra logic; buying an item takes it off the watchlist. Limit: 50 per user.

### 2.3 Daily / weekly digest (S, needs 1.1) — implemented
- **What:** an optional bot message at a time the user chooses, for example: "Portfolio 14 795 ₴, +2.1 % today. Best: Kilowatt +6 %. Worst: Prisma 2 −3 %."
- **Decision:** it is off by default and offered once, after the user's first import or third added item.
- **Built:** daily or weekly (Mondays) at a local hour; the app sends its IANA time zone. At most one per local day; one that is more than 3 hours late (e.g. after downtime) is skipped rather than sent at night. The change is the market-only move from `portfolio_history`; best and worst compare with yesterday's (or last week's) close. The offer is a card on Home.

---

## Phase 3: the full life of an investment

### 3.1 Sales and realized profit (M) — implemented
- **Why:** removing an item after selling it currently loses that result.
- **What:** "Sold…" on the item screen asks for quantity and price, reduces the position and records a sale. Home shows realized profit next to unrealized.
- **Decision:** realized profit uses the average price paid, the same basis as unrealized, so the two can be added.
- **Built:** a `sales` table (schema v8) keeps each sale with the average price paid at the time; the remaining position keeps its average.
  - The price asked for is what one item *brought*, after fees, pre-filled with today's price net of Steam's fee. Sales made off Steam have other fees, and the user knows what arrived.
  - Sales without a known price paid count towards proceeds but not realized profit, and Home says "on N of M" like the unrealized profit does.
  - The sales list (tap "Realized" on Home) can undo a sale, which puts the items back at the recorded price paid. Undo needs room in the portfolio when the position was closed.
  - A sale goes through the same quantity-change path as edits: the chart marks the day and portfolio alerts shift their baseline, so a sale never looks like a market move. Closing a position drops its profit alerts.

### 3.2 Inventory auto-sync (M) — implemented
- **What:** after an import, remember the Steam profile. Once a day, re-read the inventory and send one message: "5 new items in your inventory, add them?", with a button that opens the import screen already filled in.
- **Decisions:**
  - Steam limits inventory requests per IP hard, so sync runs at most once a day per user, spread across the day, within the existing shared limiter.
  - It only offers new items. Items that disappeared are shown as "no longer in inventory, sold?", which links to 3.1. Nothing is removed automatically.
- **Built:** `inventory_sync` (schema v8) and `cs2tracker/app/sync.py`, a supervised job.
  - Each import stores the profile and a snapshot of the inventory, so what was there and not imported is never offered later. Importing from the same profile counts as reviewing what was new.
  - Reads are due 24 h after the last one, which spreads them by when each user imported. The job takes a request from the shared inventory limiter only while one more is left for users, and reads at most one inventory every 30 s.
  - New: marketable items not in the last snapshot and not held. Gone: fewer of a held item than last time, capped at the quantity held; a recorded sale settles them. Both accumulate until the user imports, sells or dismisses.
  - The bot writes only when a read finds something new since the last one, to users who allowed it. The app shows a card on Home either way. "Review" opens the import with the new items ticked; a gone item opens the sale screen with its quantity.
  - Private or missing profiles are retried the next day and the reason is shown under the bell, where the check can be turned off. Steam's 429 pauses the job for 15 minutes.
  - Profiles imported before v8 weren't stored, so those users start after their next import.

---

## Phase 4: growth and convenience

These are independent of each other. Pick them from feedback.

- **4.1 Share card (M) — implemented:** an image with the portfolio value, P&L and top items, shared to chats or Stories (`shareMessage` / `shareToStory`). It includes the bot's link, so it doubles as promotion. Showing amounts is opt-in.
  - **Built:** the app draws a 1080×1350 card on a canvas (there is no image library on the server) with the profit in percent and the top five, or the value and amounts when the user switches them on. It follows the folder shown.
  - The JPEG is uploaded to `/api/share` and served from memory under an unguessable URL for 24 h, so Telegram can fetch it. The server accepts only a whole JPEG (frame header and end marker, so a cut-off upload never reaches a chat), keeps a user's three newest pictures, and passes the picture's size to `savePreparedInlineMessage` so clients don't crop it.
  - Messages get a separate 800×400 thumbnail (~45 KB). Telegram for iOS shows the sender their copy from `thumbnail_url` and, when the send is confirmed, moves that download into the real photo whether or not it finished; with the full picture as the thumbnail, a send before it arrived showed the sender a grey band (recipients were unaffected).
  - "Send to a chat" uses `savePreparedInlineMessage` + `shareMessage` (Bot API 8.0). When the client is older or Telegram refuses, the bot sends the picture to the user's chat to forward. The message links to `t.me/<bot>`, because shared messages can't carry a web_app button.
  - "Share to story" (`shareToStory`, mobile clients) is shown only where it works. Ten shares per user per 10 minutes.
- **4.2 Folders / several accounts (M) — implemented:** group holdings, for example "Main", "Alt", "Long-term", with a folder filter on Home.
  - **Built:** `folders` (schema v9), with `holdings.folder_id` as a label; at most 10 folders.
  - Chips above Home and the Portfolio tab filter the totals, the list, the top five and the chart. The chart shows the history of the items now in the folder.
  - Folders are managed on their own screen (rename in place, delete keeps the items). An item's screen has a folder picker, and selection mode can move several at once.
  - New items and imports land in the folder being shown. Realized profit, alerts and the digest stay whole-portfolio.
  - **Decision:** an item is in one folder at a time. Holding the same case on two Steam accounts as two positions would change the key every other feature uses (`user_id, hash_name`), so it waits for demand.
- **4.3 Export (S) — implemented:** CSV of holdings and sales. Optionally write to the Google Sheet format of the original CLI tracker.
  - **Built:** "Export to CSV" under the Portfolio list. The bot sends `cs2-portfolio-<date>.csv` (with folder, price paid, price, net value and profit) and, when there are sales, `cs2-sales-<date>.csv` to the user's chat. `downloadFile` needs Bot API 8.0 and a public, header-less URL, while a document in the chat works everywhere and stays there.
  - Files follow the user's language: commas and a decimal point in English, semicolons and a decimal comma in Russian and Ukrainian, with a BOM for Excel. Three exports per user per 10 minutes.
  - The Google Sheet option is left out: it needs per-user Google credentials.
- **4.4 Broadcast (S) — implemented:** in the admin screen, send a message to all users who pressed `/start`, rate-limited to stay within Telegram's limits.
  - **Built:** `broadcasts` (schema v9) and `cs2tracker/app/broadcast.py`. The message goes to everyone the bot may write to (`write_access`), at up to 20 messages a second, with the "Open portfolio" button.
  - A cursor in the database makes it resume after a restart. The admin screen shows the audience, progress and the last result, and can stop a broadcast. Only one runs at a time, and a private bot skips users it doesn't allow.

---

## Backlog: decide from user feedback

- **Instant estimates after an import:** third-party bulk Steam prices shown as "≈" until exact prices arrive. Details, the source and its trade-offs are in [ideas.md](ideas.md).

## Always

- Every feature ships with tests, a screenshot check in both themes and in ru/uk/en, and an adversarial review, as the Mini App and import did.
- Before any schema change, back up the production database (see [Phase 0.1](#01-daily-database-backups-s)).
