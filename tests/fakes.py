from __future__ import annotations

import json

import requests


class FakeResponse:
    def __init__(self, status: int = 200, body=None, headers: dict | None = None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, str):
            return json.loads(self._body)
        return self._body


class FakeSession:
    """Routes GETs by URL substring to queued responses (or callables)."""

    def __init__(self, routes: dict[str, list]):
        self.routes = routes
        self.headers: dict = {}
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, params or {}))
        self.last_headers = headers or {}
        for key, queue in self.routes.items():
            if key in url:
                item = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(item, BaseException):
                    raise item
                return item(url, params) if callable(item) else item
        raise AssertionError(f"unexpected request {url}")


def orderbook_body(buy=822, sell=909, currency=3, n_buy=10, n_sell=10):
    return {"data": {"success": True, "data": {
        "amtMaxBuyOrder": buy, "amtMinSellOrder": sell, "eCurrency": currency,
        "cBuyOrders": n_buy, "cSellOrders": n_sell,
        "rgCompactBuyOrders": [], "rgCompactSellOrders": [],
    }}}


class FakeWorksheet:
    def __init__(self, column_a: list[str], row_count: int = 1000):
        self.column_a = list(column_a)
        self.row_count = row_count
        self.batch_calls: list = []
        self.update_calls: list = []
        self.appended: list = []

    def col_values(self, col):
        assert col == 1
        # Like the API: trailing empty cells are trimmed.
        values = list(self.column_a)
        while values and values[-1] == "":
            values.pop()
        return values

    def update(self, values, range_name=None, value_input_option=None):
        self.update_calls.append((range_name, values, value_input_option))

    def batch_update(self, data, value_input_option=None):
        self.batch_calls.append((list(data), value_input_option))

    id = 0

    @property
    def spreadsheet(self):
        # get_worksheet_by_id() returns fresh metadata; `real_row_count` lets tests
        # simulate the grid being resized in the browser after this object was made.
        ws = self

        class Book:
            def get_worksheet_by_id(self, _id):
                return type("Meta", (), {"row_count": getattr(ws, "real_row_count", ws.row_count)})()
        return Book()

    def resize(self, rows=None):
        self.row_count = rows

    def append_rows(self, values, value_input_option=None):
        self.appended.extend(values)


class NetworkDown(requests.ConnectionError):
    pass
