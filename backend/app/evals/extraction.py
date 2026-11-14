"""The extractor: are its values the right shape, and did the second look stay inside its limits."""

from __future__ import annotations

import re
from typing import Any

from ..fraud_detection.dates.date_parsing import parse_date
from ..fraud_detection.shared.field_values import decode_field_value
from ..langgraph.extraction_prompts import EXTRACTION_SCHEMAS
from .base import Check, judged, last, names, of

AGENT = "extractor"
MAX_SECOND_LOOK_TURNS = 4  # extract_graph._MAX_AGENT_TURNS

_NUMBER_SUFFIXES = (
    "_amount", "_balance", "_total", "_salary", "_pay", "_income", "_tax", "_payable", "_contribution",
    "_contributions", "_credits", "_debits",
)
# Pay can't be negative; a balance or a total can (an overdraft, a loss).
_NON_NEGATIVE = ("salary", "_pay")
_MONEY_TEXT = re.compile(r"^\(?-?\s*(?:[A-Z]{3}|S?\$)?\s*-?\d[\d,]*(?:\.\d+)?\)?$")


def _wants_date(name: str) -> bool:
    return name == "date" or name.endswith("_date") or name.startswith("date_of_")


def _wants_number(name: str) -> bool:
    return name in ("amount", "balance") or name.endswith(_NUMBER_SUFFIXES)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and _MONEY_TEXT.match(value.strip()):
        text = re.sub(r"[^\d.\-()]", "", value)
        negative = text.startswith("(") or "-" in text
        try:
            amount = float(text.strip("()").replace("-", ""))
        except ValueError:
            return None
        return -amount if negative else amount
    return None


def field_shape(document_type: str | None, fields: dict[str, Any] | None) -> dict[str, list[str]]:
    """Which fields are missing or the wrong kind of value. Names only, no values: it goes on the trace."""
    fields = fields or {}
    required = EXTRACTION_SCHEMAS.get(document_type or "", {}).get("required", [])
    missing = [n for n in required if fields.get(n) in (None, "") or (isinstance(fields.get(n), str) and not fields[n].strip())]
    bad_dates, bad_numbers, negative = [], [], []
    for name, raw in fields.items():
        value = decode_field_value(raw)
        if value in (None, "") or isinstance(value, (list, dict)):
            continue
        if _wants_date(name) and parse_date(value) is None:
            bad_dates.append(name)
        elif _wants_number(name):
            amount = _number(value)
            if amount is None:
                bad_numbers.append(name)
            elif amount < 0 and any(part in name for part in _NON_NEGATIVE):
                negative.append(name)
    return {"missing_required": missing, "bad_dates": bad_dates, "bad_numbers": bad_numbers, "negative": negative}


def checks(steps: list[dict[str, Any]]) -> list[Check]:
    extraction = last(steps, "extraction")
    second = last(steps, "extraction_second_look")
    if extraction is None and second is None:
        return []
    shape = (extraction or {}).get("field_shape")
    looked = bool(second) or bool((extraction or {}).get("second_look"))
    refused = [t for s in of(steps, "extraction_tool_refused") for t in s.get("tools") or []]
    rejected = (extraction or {}).get("second_look_rejected_fields") or []
    odd = [*(shape or {}).get("bad_dates", []), *(shape or {}).get("bad_numbers", []), *(shape or {}).get("negative", [])]
    return [
        judged(AGENT, "required_fields_present", "output",
               None if shape is None else not shape.get("missing_required"),
               f"Required fields missing: {names((shape or {}).get('missing_required', []))}"),
        judged(AGENT, "values_fit_their_fields", "output", None if shape is None else not odd,
               f"Values that don't fit their field (a date, an amount): {names(odd)}"),
        judged(AGENT, "second_look_tool_order", "trajectory", not refused if looked else None,
               f"The second look tried {names(refused)} outside its limits (the guard refused it)"),
        judged(AGENT, "second_look_turns", "trajectory",
               None if second is None else (second.get("turn_count") or 0) < MAX_SECOND_LOOK_TURNS,
               "The second look used all its turns"),
        judged(AGENT, "second_look_changes_used", "output", not rejected if looked else None,
               f"The code rejected {len(rejected)} change(s) the second look proposed"),
        judged(AGENT, "second_look_finished", "output",
               not (extraction or {}).get("second_look_error") if looked else None,
               "The second look failed, so the text pass was kept", broken="fail"),
    ]
