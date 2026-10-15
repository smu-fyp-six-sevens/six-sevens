"""
What every investigator tool shares: the typed breach flags, the case
binding, and the call log verify.py reads. The case is bound in the kit, so
the model never sees or supplies a case id.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from ....fraud_detection.numerical_validation.common import _decode, _month_number, _parse_field_date
from ..evidence import CaseContext, DocView, transaction_rows

# The only flags a tool may emit; verify.py lets a finding reach High only on one of these.
BREACH_FLAGS = frozenset({
    "IDENTIFIER_CONFLICT", "NAME_MISMATCH", "ADDRESS_MISMATCH", "PERSON_MISMATCH",
    "PERIOD_OVERLAP", "BALANCE_DISCONTINUITY", "CHRONOLOGICAL_IMPOSSIBILITY",
})
# A disagreement about who the entity is, not about how it is described.
IDENTITY_FLAGS = frozenset({"IDENTIFIER_CONFLICT"})
VARIANCE_FLAGS = frozenset({"NAME_MISMATCH", "ADDRESS_MISMATCH", "PERSON_MISMATCH"})

MAX_ROWS = 10

# Tools that compute something over the data (counts, totals, flows, periods) rather than read a field back.
# A fact one of them issued is evidence on its own, even when it comes from a single document.
COMPUTED_TOOLS = frozenset({
    "transaction_stats", "query_transactions", "top_counterparties", "monthly_flow", "compare_periods",
})
# Tools whose very job is to relate documents to each other.
CROSS_DOCUMENT_TOOLS = frozenset({
    "get_business_profile", "compare_identifiers", "compare_people", "compare_periods",
    "company_name_consistency_tool", "address_consistency_tool",
})


def unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason}


def direction_ok(amount: float, direction: str) -> bool:
    return direction == "any" or (direction == "credit" and amount > 0) or (direction == "debit" and amount < 0)


# "20 JUN", "20-Jun", "20 June" and "20/06": the day and month a statement prints on each row.
_DAY_MONTH_NAME_RE = re.compile(r"^\s*(\d{1,2})[\s\-/]+([A-Za-z]{3,9})\.?\s*$")
_DAY_MONTH_NUMBER_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})\s*$")

Period = tuple[Optional[date], Optional[date]]


def _year_from_period(month: int, day: int, period: Period) -> Optional[date]:
    """The date a year-less day and month fall on, taking the year from the statement's own period."""
    start, end = period
    if start is not None:
        first_year = start.year
        last_year = end.year if end is not None else start.year + 1
    elif end is not None:
        first_year = end.year - 1
        last_year = end.year
    else:
        return None
    for year in range(first_year, last_year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if (start is None or candidate >= start) and (end is None or candidate <= end):
            return candidate
    return None


def row_date(row: dict, period: Period = (None, None)) -> Optional[date]:
    """
    A transaction row's date. Statements often print "20 JUN" with no year; that
    year comes from the statement's period, and a date the period can't place
    stays undated rather than guessed.
    """
    value = row.get("date")
    full = _parse_field_date(value)
    if full is not None or not isinstance(value, str):
        return full
    named, numbered = _DAY_MONTH_NAME_RE.match(value), _DAY_MONTH_NUMBER_RE.match(value)
    if named:
        day, month = int(named.group(1)), _month_number(named.group(2))
    elif numbered:
        day, month = int(numbered.group(1)), int(numbered.group(2))
    else:
        return None
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return _year_from_period(month, day, period)


def in_range(row: dict, start: Optional[date], end: Optional[date], period: Period = (None, None)) -> bool:
    if start is None and end is None:
        return True
    day = row_date(row, period)
    if day is None:
        return False
    return (start is None or day >= start) and (end is None or day <= end)


def statement_rows(view: DocView) -> Optional[list[dict]]:
    return transaction_rows(view.fields) or None


def statement_period(view: DocView) -> tuple[Optional[date], Optional[date]]:
    return (
        _parse_field_date(_decode(view.fields.get("statement_period_start"))),
        _parse_field_date(_decode(view.fields.get("statement_period_end"))),
    )


@dataclass
class ToolKit:
    """The case, and the log of calls one investigator makes."""

    context: CaseContext
    calls: list[dict[str, Any]]

    def doc(self, name: str) -> tuple[Optional[DocView], Optional[dict[str, Any]]]:
        view = self.context.resolve(name)
        if view is None:
            return None, unavailable(f"Unknown document '{name}'. Use list_documents for the valid names.")
        return view, None

    def log(
        self, tool_name: str, args: dict, result: dict, probe: bool,
        document: Optional[str] = None, refs: Optional[list[str]] = None,
    ) -> str:
        self.calls.append({
            "tool": tool_name,
            "args": args,
            "document": document,
            "probe": probe,
            "available": result.get("available", True) is not False,
            "refs": refs or [],
            "breach_flags": [f for f in result.get("breach_flags", []) if f in BREACH_FLAGS],
        })
        return json.dumps(result, default=str)
