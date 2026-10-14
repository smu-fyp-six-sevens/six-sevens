"""
What every investigator tool shares: the typed breach flags, the case
binding, and the call log verify.py reads. The case is bound in the kit, so
the model never sees or supplies a case id.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from ....fraud_detection.numerical_validation.common import _decode, _parse_field_date
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


def unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason}


def direction_ok(amount: float, direction: str) -> bool:
    return direction == "any" or (direction == "credit" and amount > 0) or (direction == "debit" and amount < 0)


def in_range(row: dict, start: Optional[date], end: Optional[date]) -> bool:
    if start is None and end is None:
        return True
    day = _parse_field_date(row.get("date"))
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
