"""
A native-text bank statement's transaction table, read in code from word positions.

Nothing here knows a bank or a template: each page's columns come from its own header row. A row is any line with a
figure in the money columns. Accepted on parse quality only, never on whether the balances add up: a statement that
doesn't add up may be forged, and that is detection's call. Anything unclear rejects the whole document, which then
takes the usual Claude extraction, as does a scan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast

import pymupdf

from ..fraud_detection.numerical_validation.statements import _CARRY_OVER_RE

_SAME_LINE = 2.0
_HEADER_DEPTH = 4
# Past the balance heading's right edge is outside the table (a margin note), in points.
_RIGHT_MARGIN = 15.0
# How far a description line can sit from its row, in line heights.
_BELOW_GAP = 2.0
_AROUND_GAP = 3.0
_DEBIT, _CREDIT, _BALANCE = ("debit", "withdrawal"), ("credit", "deposit"), ("balance",)
_DESCRIPTION = ("desc", "detail", "particular", "narrati", "remark")
_MONEY_RE = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}")
_DATE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{1,2}-[A-Za-z]{3,9}(?:-\d{2,4})?|\d{1,2} [A-Za-z]{3,9}(?: \d{4})?"
)
_Word = tuple[float, float, float, float, str]


class _Unreadable(Exception):
    pass


@dataclass(frozen=True)
class _Columns:
    description: float
    debit: float
    credit: float
    balance: float
    right: float


@dataclass
class _Row:
    y: float
    values: dict[str, Any]
    text: list[tuple[float, str]]


def _lines(page: pymupdf.Page) -> list[list[_Word]]:
    lines: list[list[_Word]] = []
    for word in sorted(cast(list[tuple], page.get_text("words")), key=lambda w: (w[1], w[0])):
        if lines and abs(lines[-1][0][1] - word[1]) <= _SAME_LINE:
            lines[-1].append(word[:5])
        else:
            lines.append([word[:5]])
    return [sorted(line, key=lambda w: w[0]) for line in lines]


def _heading(line: list[_Word], names: tuple[str, ...]) -> _Word | None:
    return next((w for w in line if w[4].lower().startswith(names)), None)


def _columns(lines: list[list[_Word]]) -> tuple[int, _Columns] | None:
    """The header row and its columns: the first line naming debit, credit and balance, which may wrap over lines."""
    for index, line in enumerate(lines):
        debit, credit, balance = _heading(line, _DEBIT), _heading(line, _CREDIT), _heading(line, _BALANCE)
        if not debit or not credit or not balance or debit[0] == credit[0] or balance[0] < max(debit[0], credit[0]):
            continue
        first = min(debit[0], credit[0])
        starts = [w[0] for near in lines[index:index + _HEADER_DEPTH] for w in near
                  if w[4].lower().startswith(_DESCRIPTION) and w[0] < first]
        if not starts:
            return None
        return index, _Columns(min(starts), debit[0], credit[0], balance[0], balance[2] + _RIGHT_MARGIN)
    return None


def _money_column(word: _Word, columns: _Columns) -> str | None:
    """Columns run from one heading's left edge to the next, which fits a figure aligned either way under its heading."""
    edge = word[2]
    if edge > columns.right:
        return None
    if edge >= columns.balance:
        return "balance"
    low, high = sorted(((columns.debit, "debit"), (columns.credit, "credit")))
    return high[1] if edge >= high[0] else low[1]


def _figure(text: str) -> float:
    return float(text.replace(",", ""))


def _read_row(line: list[_Word], columns: _Columns) -> _Row | None:
    """The row this line anchors, or None when it has no figure. A summary line (no date, no balance) has empty values."""
    first_money = min(columns.debit, columns.credit)
    cells: dict[str, float] = {}
    strays = False
    for word in line:
        if word[0] < first_money:
            continue
        column = _money_column(word, columns)
        if column is None:
            continue
        if not _MONEY_RE.fullmatch(word[4]):
            strays = True
        elif column in cells:
            raise _Unreadable("two figures in one column")
        else:
            cells[column] = _figure(word[4])
    if not cells:
        return None
    if strays:
        raise _Unreadable("a money column holds something other than a plain figure")

    y = line[0][1]
    described = [w[4] for w in line if columns.description <= w[0] < first_money]
    description = " ".join(described)
    found = _DATE_RE.search(" ".join(w[4] for w in line if w[2] <= columns.description))
    date = found.group(0) if found else None
    debit, credit, balance = cells.get("debit"), cells.get("credit"), cells.get("balance")
    text = [(y, description)] if description else []
    if debit is None and credit is None:
        if _CARRY_OVER_RE.search(description):
            return _Row(y, {"date": date, "amount": None, "balance": balance}, text)
        raise _Unreadable("a row has no amount")
    if date is None:
        if balance is None:
            return _Row(y, {}, [])
        raise _Unreadable("a row has no date")
    if debit and credit:
        raise _Unreadable("a row has both a debit and a credit")
    amount = -abs(debit) if debit else (credit or 0.0)
    return _Row(y, {"date": date, "amount": amount, "balance": balance}, text)


def _only_a_date_before(line: list[_Word], columns: _Columns) -> bool:
    """A row's next line has at most its value date left of the description; a page footer starts there with words."""
    before = " ".join(w[4] for w in line if w[2] <= columns.description)
    return not before or _DATE_RE.fullmatch(before) is not None


def _attach_descriptions(rows: list[_Row], others: list[list[_Word]], columns: _Columns) -> None:
    """Description lines run below each row, or around it when some row has none on its own line."""
    first_money = min(columns.debit, columns.credit)
    around = any(row.values.get("date") and not row.text for row in rows)
    last_y: float | None = None
    owner: _Row | None = None
    pending = sorted(
        [(row.y, row, None) for row in rows]
        + [(line[0][1], None, line) for line in others],
        key=lambda item: item[0],
    )
    for y, row, line in pending:
        if row is not None:
            owner, last_y = (row if row.values else None), y
            continue
        assert line is not None
        words = [w[4] for w in line if columns.description <= w[0] < first_money]
        if not words:
            continue
        height = line[0][3] - line[0][1]
        if around:
            nearest = min((r for r in rows if r.values), key=lambda r: abs(r.y - y), default=None)
            if nearest is not None and abs(nearest.y - y) <= _AROUND_GAP * height:
                nearest.text.append((y, " ".join(words)))
        elif owner is not None and last_y is not None and y - last_y <= _BELOW_GAP * height and _only_a_date_before(line, columns):
            owner.text.append((y, " ".join(words)))
            last_y = y


def _read_page(page: pymupdf.Page) -> list[dict[str, Any]]:
    lines = _lines(page)
    if not lines:
        raise _Unreadable("a page has no text")
    found = _columns(lines)
    if found is None:
        if any(_DATE_RE.match(line[0][4]) and sum(bool(_MONEY_RE.fullmatch(w[4])) for w in line) >= 2 for line in lines):
            raise _Unreadable("a page has transaction rows but no column header")
        return []
    index, columns = found
    rows: list[_Row] = []
    others: list[list[_Word]] = []
    for line in lines[index + 1:]:
        row = _read_row(line, columns)
        if row is None:
            others.append(line)
        else:
            rows.append(row)
    _attach_descriptions(rows, others, columns)
    return [
        {**row.values, "description": " ".join(text for _, text in sorted(row.text, key=lambda item: item[0])),
         "counterparty": None}
        for row in rows if row.values
    ]


def read_transactions(file_bytes: bytes) -> dict[str, Any]:
    """{"rows": [...]} in printed order, or {"rejected": why}. CPU-bound: run through cpu_pool.run_cpu."""
    document = pymupdf.open(stream=file_bytes, filetype="pdf")
    try:
        rows = [row for page in document for row in _read_page(page)]
    except _Unreadable as exc:
        return {"rejected": str(exc)}
    finally:
        document.close()
    if not any(row["amount"] is not None for row in rows):
        return {"rejected": "no transaction table found"}
    return {"rows": rows}
