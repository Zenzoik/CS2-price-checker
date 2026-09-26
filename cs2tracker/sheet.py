"""Google Sheets side: reads tracked items, writes prices.

Only two columns are touched – item names (A) and current price (C). Everything else
in the template (totals, profit, summary block) is left to the sheet's own formulas.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable

import gspread
from gspread.utils import ValueInputOption, rowcol_to_a1

log = logging.getLogger(__name__)

NAME_COL = 1   # A: hash name (default template)
PRICE_COL = 3  # C: now price
HEADER_ROWS = 1
HISTORY_HEADER = ["Time", "Item", "Price", "Currency"]
SHEETS_TIMEOUT = 60  # seconds; gspread's default is to wait forever

_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{30,}$")


class SheetSetupError(Exception):
    """The spreadsheet could not be opened; the message says what to fix."""


class PortfolioSheet:
    def __init__(self, worksheet, history=None, *, name_col: int = NAME_COL,
                 price_col: int = PRICE_COL, dry_run: bool = False):
        self.ws = worksheet
        self.history = history
        self.name_col = name_col
        self.price_col = price_col
        self.dry_run = dry_run

    def items(self) -> list[tuple[int, str]]:
        """(row number, hash name) for every non-empty name below the header."""
        names = self.ws.col_values(self.name_col)[HEADER_ROWS:]
        return [
            (row, str(name).strip())
            for row, name in enumerate(names, start=HEADER_ROWS + 1)
            if str(name).strip()
        ]

    def add_items(self, hash_names: Iterable[str]) -> list[str]:
        """Appends names that are not in the sheet yet; returns the ones added."""
        column = self.ws.col_values(self.name_col)
        seen = {str(v).strip() for v in column[HEADER_ROWS:]}
        new = []
        for name in hash_names:
            name = name.strip()
            if name and name not in seen:
                seen.add(name)
                new.append(name)
        if not new or self.dry_run:
            if new:
                log.info("Dry run: would add %s", ", ".join(new))
            return new
        # Write right below the last name instead of append_rows(): the template has
        # a summary block to the right that confuses Sheets' table detection.
        first = max(len(column), HEADER_ROWS) + 1
        last = first + len(new) - 1
        # The cached row_count may be stale (the grid can be resized in the browser),
        # and add_rows() resizes to an absolute size – re-read it so no rows are cut off.
        rows = self.ws.spreadsheet.get_worksheet_by_id(self.ws.id).row_count
        if last > rows:
            self.ws.resize(rows=last)
        self.ws.update(
            [[n] for n in new],
            range_name=f"{rowcol_to_a1(first, self.name_col)}:{rowcol_to_a1(last, self.name_col)}",
            value_input_option=ValueInputOption.raw,
        )
        return new

    def write_prices(self, prices: dict[str, float]) -> int:
        """Writes {hash name: price} next to each matching name, in one API call.

        Rows are looked up again right before writing, so sorting or inserting rows
        while prices were being fetched cannot put a price next to the wrong item.
        Returns the number of cells written.
        """
        if not prices:
            return 0
        data = [
            {"range": rowcol_to_a1(row, self.price_col), "values": [[round(prices[name], 2)]]}
            for row, name in self.items()
            if name in prices
        ]
        if self.dry_run:
            for d in data:
                log.info("Dry run: would write %s = %s", d["range"], d["values"][0][0])
        elif data:
            self.ws.batch_update(data, value_input_option=ValueInputOption.raw)
        return len(data)

    def log_history(self, timestamp: str, currency: str, prices: dict[str, float]) -> None:
        if self.history is None or not prices or self.dry_run:
            return
        self.history.append_rows(
            [[timestamp, name, round(price, 2), currency] for name, price in prices.items()],
            value_input_option=ValueInputOption.user_entered,
        )


def open_portfolio(
    credentials: Path,
    spreadsheet: str,
    worksheet: str | int = 0,
    history_worksheet: str | None = None,
    *,
    name_col: int = NAME_COL,
    price_col: int = PRICE_COL,
    dry_run: bool = False,
) -> PortfolioSheet:
    if not credentials.is_file():
        raise SheetSetupError(
            f"Google service account key not found: {credentials}. See 'Setup' in README.md."
        )
    try:
        client = gspread.service_account(filename=credentials)
    except (ValueError, AttributeError, KeyError, TypeError) as e:  # not JSON / not a service account key
        raise SheetSetupError(
            f"{credentials} is not a service account key ({e}). Download a JSON key from "
            "Google Cloud → Service Accounts → Keys."
        ) from e
    client.set_timeout(SHEETS_TIMEOUT)

    book = _open_book(client, spreadsheet)
    try:
        ws = book.get_worksheet(worksheet) if isinstance(worksheet, int) else book.worksheet(worksheet)
    except gspread.WorksheetNotFound as e:
        raise SheetSetupError(f"worksheet {worksheet!r} not found in {book.title!r}") from e

    history = None
    if history_worksheet and not dry_run:
        # Sheets tab names are unique case-insensitively.
        if history_worksheet.casefold() == ws.title.casefold():
            raise SheetSetupError("history_worksheet must be a different tab than the portfolio")
        history = next(
            (w for w in book.worksheets() if w.title.casefold() == history_worksheet.casefold()), None
        )
        if history is None:
            log.info("Creating worksheet %r for price history", history_worksheet)
            history = book.add_worksheet(history_worksheet, rows=1, cols=len(HISTORY_HEADER))
            history.update([HISTORY_HEADER], range_name="A1")
    return PortfolioSheet(ws, history, name_col=name_col, price_col=price_col, dry_run=dry_run)


def _open_book(client, spreadsheet: str):
    no_access = SheetSetupError(
        f"Cannot open spreadsheet {spreadsheet!r}. Share it (Editor) with the service "
        "account e-mail from your key file, and make sure the Google Sheets and Drive "
        "APIs are enabled for the project."
    )
    try:
        if spreadsheet.startswith(("http://", "https://")):
            return client.open_by_url(spreadsheet)
        if _KEY_RE.match(spreadsheet):
            try:
                return client.open_by_key(spreadsheet)
            except gspread.SpreadsheetNotFound:
                pass  # a long title that merely looks like a key
        return client.open(spreadsheet)
    except gspread.exceptions.NoValidUrlKeyFound as e:
        raise SheetSetupError(f"not a Google Sheets URL: {spreadsheet}") from e
    except (gspread.SpreadsheetNotFound, PermissionError) as e:
        raise no_access from e
