# CS2 Case Price Tracker

<p align="center">
  <strong>English</strong> •
  <a href="README_UA.md">Українська</a>
</p>

Tracks CS2 (Counter-Strike 2) Steam Community Market prices and keeps the **Now price** column of your Google Sheets portfolio up to date. The sheet's own formulas compute totals and profit/loss.

It can also run as a [Telegram Mini App](#telegram-mini-app), a minimal portfolio tracker right inside Telegram.

## Features

- 🔍 **Search and add items**: pick the exact item from the market search results. Containers (cases, capsules) are searched first, then all items.
- 📊 **Price updates**: once (handy for cron / Task Scheduler) or in a loop.
- 💱 **Your currency**: prices are written in the currency you configure (UAH, USD, EUR, …), and the tool never silently mixes currencies.
- ⚡ **Light on quotas**: one Steam request per item and a single Google Sheets write per update. Handles Steam rate limits with backoff.
- 📈 **Optional price history**: every update can append rows to a *History* tab, ready for charts.

## Requirements

- Python 3.10+
- A Google account and a Google Cloud project (the free tier is enough)

## Installation

```bash
git clone https://github.com/Zenzoik/CS2-price-checker.git
cd CS2-price-checker
pip install -r requirements.txt
```

You can also run `pip install .`, which adds a `cs2tracker` command to your PATH.

## Setup

### 1. Copy the sheet template

1. Open the [sheet template](https://docs.google.com/spreadsheets/d/1eShxZQ34gI8dir-6LISCNX-omjF8A2XQJb9vL1jh_bs/edit?usp=sharing).
2. **File → Make a copy**, and name it `Template_CS_2_cases`. Any other name works if you set `spreadsheet` in the config.

### 2. Create a Google service account

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project or pick an existing one.
2. Under **APIs & Services → Library**, enable the **Google Sheets API** and the **Google Drive API**.
3. Under **APIs & Services → Credentials**, choose **Create Credentials → Service Account**. Give it any name, skip the roles, then click **Done**.
4. Open the service account, go to **Keys → Add Key → Create new key → JSON**, and save the file as `service-account.json` in the project folder.

### 3. Share the sheet with the service account

Open your copy of the sheet, click **Share**, paste the service account e-mail (`client_email` in the JSON file, like `name@project.iam.gserviceaccount.com`), give it **Editor** access and untick "Notify people".

### 4. (Optional) Configure

Every setting has a default, so this step is optional. To change something:

```bash
cp config.example.toml config.toml
```

| Key | Default | Meaning |
|-----|---------|---------|
| `credentials` | `service-account.json` | Path to the service account key |
| `spreadsheet` | `Template_CS_2_cases` | Sheet title, full URL, or key |
| `worksheet` | `0` | Tab index (0 = first) or tab name |
| `name_column` / `price_column` | `A` / `C` | Columns with hash names and the price to write |
| `currency` | `UAH` | ISO code, or `auto` for Steam's currency for your IP |
| `price` | `sell` | `sell` = lowest listing, `buy` = highest buy order |
| `interval_minutes` | `5` | Delay between updates in `watch` mode |
| `request_delay` | `1.5` | Minimum seconds between Steam requests |
| `history_worksheet` | *(off)* | Tab name for the price log, e.g. `History` |

## Usage

```bash
python main.py                        # interactive menu: 1 add, 2 track, 3 exit, 4 update once
python main.py add "breakout case"    # search and pick from the results
python main.py add -y "chroma 2 case" "prisma case"   # take the top results
python main.py search "sticker capsule" --all-items   # look without touching the sheet
python main.py update                 # update prices once
python main.py update --dry-run       # fetch and show, write nothing
python main.py watch -i 10            # update every 10 minutes until Ctrl+C
```

With `pip install .` you can type `cs2tracker …` instead of `python main.py …`.

Fill in **Buy price (B)** and **Quantity (E)** yourself. The tool writes only **Hash name (A)** and **Now price (C)**.

### Running on a schedule

`update` runs once and exits, so you don't have to keep a terminal open. Let the OS run it on a schedule instead:

- **macOS / Linux (cron)**: `*/15 * * * * cd /path/to/CS2-price-checker && /usr/bin/python3 main.py update >> tracker.log 2>&1`
- **Windows**: in Task Scheduler, create a task that runs `python main.py update` with the project folder as *Start in*.

Exit codes: `0` means OK, `2` means some items failed or Steam was unreachable (see the log), `1` means a setup error (bad arguments, config, key, sheet access), and `130` means stopped with Ctrl+C.

Prices are written by item name, so sorting the sheet while an update runs is safe. If Steam is down, an update stops after 3 failed items in a row instead of waiting on every one.

## Telegram Mini App

Besides the Google Sheet, the tracker can run as a Telegram bot with a Mini App. Everyone who opens it keeps their own portfolio: search the Steam market, enter how many you bought and at what price, and see what the portfolio is worth now and your profit. The app has a single action button, and prices refresh in the background.

**Setup**

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Point a domain at your server and put an HTTPS reverse proxy in front of the app. Telegram opens Mini Apps only over HTTPS; see [`deploy/nginx.conf`](deploy/nginx.conf) (with certbot) or the two-line [`deploy/Caddyfile`](deploy/Caddyfile).
3. Install with the app extra: `pip install ".[app]"` (or `pip install -r requirements.txt`).
4. Copy [`deploy/app.env.example`](deploy/app.env.example) to `/etc/cs2tracker/app.env`. Fill in `CS2BOT_TOKEN` and `CS2BOT_URL`, and optionally `CS2BOT_ALLOWED_USERS` to keep the bot private.
5. Run it with `python -m cs2tracker.app`, or as a service: [`deploy/cs2tracker-app.service`](deploy/cs2tracker-app.service).

**Backups and alerts.** Once a day the service writes a checked copy of the database to `backups/` next to it (with the systemd unit: `/var/lib/cs2tracker/backups/`) and keeps the last 14. Admins in `CS2BOT_ADMINS` get a Telegram message when prices stop updating, Steam keeps failing for 30 minutes, a backup fails or the service restarts after a crash, and another when it is fixed. To restore a backup:

```bash
systemctl stop cs2tracker-app
cd /var/lib/private/cs2tracker
cp cs2tracker.db cs2tracker.db.before-restore        # just in case
cp backups/cs2tracker-YYYYMMDD-HHMMSS.db cs2tracker.db
rm -f cs2tracker.db-wal cs2tracker.db-shm
chown --reference=. cs2tracker.db                    # the service's dynamic user
systemctl start cs2tracker-app
```

**Upgrades and rollbacks.** When a new release changes the database schema, the service first writes a backup and refuses to start if it can't. An older release can't open an upgraded database, so rolling back a deploy means restoring that backup, taken just before the upgrade, as above.

On start the bot sets its menu button to open the app, and it answers any message with an **Open portfolio** button. The app speaks English, Russian and Ukrainian, following the user's Telegram language.

All prices use one currency (`CS2BOT_CURRENCY`, default UAH). The database remembers it and refuses to start with a different one, so prices never get mixed. Large imports are priced one item at a time (about 40 per minute) while Home shows the progress; ideas for faster estimates are in [docs/ideas.md](docs/ideas.md). Planned features are in [docs/roadmap.md](docs/roadmap.md). The [same rules](#how-prices-are-fetched) apply for which currencies and price kinds work from your server's IP.

The Mini App also keeps one price per item per UTC day in `price_history`. Each successful refresh replaces that day's value, so the last one before midnight is the daily close. A refresh that finds no price (no listings or buy orders) is recorded as "no price"; a failed request records nothing and the previous price stands, the same rule the current prices follow. Existing databases start collecting history when upgraded.

Home shows the portfolio value, how much prices moved it over the chosen period (a week, a month or all available history), the profit, a value chart for that period and the five most valuable items. The full list is in the Portfolio tab. The price move leaves out items that were added or removed, and items that gained or lost a price, so a purchase never shows up as a gain. The chart uses recorded daily quantities, so it begins on the day quantity tracking was enabled; days when holdings changed are marked on the line.

Portfolio rows show the change since yesterday's UTC close and can be sorted by that change. The item screen shows seven-day change when enough history exists, the break-even listing price per item after Steam's 15% fee, and the latest listing/buy-order counts and spread when the order book provides them. Missing history or market depth is left blank.

**Notifications.** The bell on Home opens alerts and the summary. An alert is one condition: an item's price above or below a level, its profit on the price paid, or the portfolio's value up or down by a percent or an amount since the alert was set (only price moves count, not items added or removed). Alerts are checked after each price refresh, so they arrive within about one refresh interval and cost no extra Steam requests. Each fires once and fires again only after the value has moved back across the threshold by a small margin; everything due at once arrives as one message. Items you don't own can be watched from their screen: they are refreshed like holdings and listed under "Watching", but never counted in the totals. The summary is a daily or Monday message at the hour you choose (in your time zone) with the portfolio value, the price move and the best and worst item; it is off by default and offered once. The bot can only write to users who allowed it; the app asks when the first alert or summary is set up. Limits: 20 alerts and 50 watched items per user.

**Sales.** "Sold…" on an item's screen records a sale: how many, and what one item brought after fees (filled in with today's price after Steam's fee). The position shrinks, or closes, and the sale keeps the average price paid, so realized profit is measured the same way as the profit on paper and the two add up. Home shows realized profit under the unrealized one; tapping it lists the sales, and tapping a sale undoes it, putting the items back at the price paid. A sale changes quantities, not prices, so it never counts as a market move on the chart or in portfolio alerts.

**Inventory check.** An import remembers the Steam profile and what its inventory held. Once a day per user the service reads that inventory again and, if something changed, the bot sends one message and Home shows a card: new marketable items open the import with them ticked, and held items that left the inventory ask "sold?" and open the sale screen. Nothing is added or removed on its own. The check shares the server's inventory budget with users' own lookups and runs only when a request is left over for them, so it never makes an import wait. It can be turned off under the bell. Databases upgraded from before this start checking after the next import.

## How prices are fetched

Steam decides the currency of its order book from your **IP address**, and the order book is the only source of exact buy/sell prices. So:

- If your configured `currency` matches Steam's currency for your IP, or you set `currency = "auto"`, prices come from the order book. That's one fast request per item, and both `price = "buy"` and `price = "sell"` work.
- Otherwise the tool switches to Steam's *price overview* in your currency. This source has only the lowest listing (`price = "sell"`), and it is slower because Steam allows about 20 of these requests per minute.
- `price = "buy"` with a mismatching currency stops with an explanation instead of writing prices in the wrong currency.

`currency = "auto"` decides the currency anew on every run (it stays fixed within one `watch` session). If your IP can change, for example with a VPN, set an explicit currency so the sheet never mixes currencies.

> **Upgrading from 1.x:** the old script wrote the highest buy order in UAH, and that no longer works: Steam removed the page data the script relied on. The new default is the lowest listing price in UAH. To keep buy-order prices, set `price = "buy"` together with the currency Steam uses for your IP. If that isn't UAH, the error message tells you which currency it is.

## Sheet layout

| Column | Content |
|--------|---------|
| A | Hash name *(written by the tool)* |
| B | Buy price *(you)* |
| C | Now price *(written by the tool)* |
| D | Now price minus Steam fee |
| E | Quantity *(you)* |
| F–L | Totals and results (formulas) |
| N–R | Portfolio summary (formulas) |

## Troubleshooting

- **`Google service account key not found`**: put `service-account.json` next to `main.py`, or set `credentials` in `config.toml`. Both files are looked up in the current folder first, then next to `main.py`.
- **`Cannot open spreadsheet`**: share the sheet with the service account e-mail (Editor), check the `spreadsheet` value, and check that the Sheets and Drive APIs are enabled. A URL or key works even when the title differs.
- **`Steam serves order books in EUR for your IP…`**: see [How prices are fetched](#how-prices-are-fetched). Use `price = "sell"`, or set `currency` to the currency named in the message.
- **`rate limited (429)`**: Steam limits requests per IP. The tool waits and retries. If it keeps happening, increase `request_delay` or update less often.
- **`not found on Steam market`**: the name in column A must be the exact market hash name. Add items with `add` to get it right.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Legal notice

This tool is for personal and educational use. It relies on unofficial Steam endpoints that can change at any time. Respect Steam's Terms of Service and rate limits.

## License

MIT, see [LICENSE](LICENSE).
