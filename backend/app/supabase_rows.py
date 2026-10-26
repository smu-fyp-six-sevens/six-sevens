from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

# PostgREST returns at most this many rows per request, and says nothing when a result is cut off.
PAGE = 1000


def row(mapping: Any) -> dict:
    """postgrest types a row as a loose JSON union; every row we read is a dict we wrote."""
    return cast(dict, mapping)


def fetch_all(build: Callable[[], Any], order: str) -> list[dict]:
    """Every row of a query, a page at a time. `build` returns a fresh select with its filters; `order` keeps the pages apart."""
    rows: list[dict] = []
    while True:
        page = build().order(order).range(len(rows), len(rows) + PAGE - 1).execute().data
        rows.extend(row(item) for item in page)
        if len(page) < PAGE:
            return rows
