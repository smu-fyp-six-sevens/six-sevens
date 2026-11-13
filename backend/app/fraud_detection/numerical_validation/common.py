"""Shared helpers and the check context for the numerical checks."""

from __future__ import annotations

import re
from typing import Any, Protocol, Self, TypeVar
from datetime import date
from ...config import settings
from ..dates.date_parsing import parse_date
from ..font_consistency import ExtractedField
from ..shared.field_values import decode_field_value
from .models import NumericalFinding, Severity, SkippedCheck


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _fields_by_name(fields: list[ExtractedField] | None) -> dict[str, Any]:
    return {f.field_name: decode_field_value(f.field_value) for f in fields or []}


def _confidence_by_name(fields: list[ExtractedField] | None) -> dict[str, float]:
    return {
        f.field_name: f.confidence
        for f in fields or []
        if isinstance(f.confidence, (int, float))
    }


_CURRENCY_SYMBOLS = r"\$|€|£|¥|₹"
_CURRENCY_WORDS = r"SGD|USD|EUR|GBP|MYR|AUD|HKD|JPY|CNY|RMB|IDR|THB|PHP|INR"

# A rate/percentage's decorative suffix — doc_gen_scripts' own loan-letter
# templates render e.g. "6.00% per annum, fixed" / "6.50% per annum" — must
# come off before the comma/whitespace strip below collapses the spaces
# these phrases depend on for their word boundaries.
_RATE_SUFFIX_RE = re.compile(
    r"%|\bper\s+annum\b|\bp\.?a\.?\b|\bfixed\b|\bannually\b|\bper\s+year\b", re.IGNORECASE,
)

# doc_gen_scripts' overdraft_facility_generator.py renders interest_rate as
# a reference-rate formula, not a flat number: "Bank's Prime Rate + 2.50%
# (currently 6.72% p.a.)" — the plain cleanup above can't turn that into a
# float (the bank/reference-rate name is genuinely non-numeric), but the
# effective rate the formula resolves to is right there in the "currently
# N%" clause; used as a last-resort fallback when the straightforward parse
# fails, not tried first, so it never overrides a real, simple number.
_CURRENTLY_RATE_RE = re.compile(r"currently\s+(-?\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
# Statements print a debit or an overdrawn balance as "1,234.56 DR", a credit as "... CR".
_DEBIT_CREDIT_SUFFIX_RE = re.compile(r"(.*?)(DR|CR)\.?", re.IGNORECASE)
# "1.234,56" or "1234,56": a decimal comma, with dots (if any) grouping thousands.
_COMMA_DECIMAL_RE = re.compile(r"\(?-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{1,2}\)?")


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = _RATE_SUFFIX_RE.sub("", value)
        cleaned = re.sub(rf"\s|{_CURRENCY_SYMBOLS}|{_CURRENCY_WORDS}", "", text, flags=re.I)
        marker = None
        if suffixed := _DEBIT_CREDIT_SUFFIX_RE.fullmatch(cleaned):
            cleaned, marker = suffixed.group(1), suffixed.group(2).upper()
        if _COMMA_DECIMAL_RE.fullmatch(cleaned):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = "-" + cleaned[1:-1]
        try:
            number = float(cleaned)
        except ValueError:
            match = _CURRENTLY_RATE_RE.search(value)
            return float(match.group(1)) if match else None
        return -abs(number) if marker == "DR" else abs(number) if marker == "CR" else number
    return None


def _field_number(row: dict, key: str) -> float | None:
    """One numeric value from a row (a line item, account, holding...)."""
    return _as_number(row.get(key))


def _rows(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _row_value(row: dict, *key_words: str) -> Any:
    """
    The first non-empty value in a row whose key contains one of key_words.
    Keys inside a list field come from the column header as the extraction
    LLM transcribed it ("Position Held" -> position_held, "Date of
    Appointment" -> date_of_appointment), so a value is looked up by the
    words in its key rather than one exact name.
    """
    for key, value in row.items():
        if any(word in key.lower() for word in key_words) and value not in (None, ""):
            return value
    return None


def officer_role(row: dict) -> str:
    """An officer's role ("Director", "Secretary"), however its column was named."""
    return str(_row_value(row, "role", "position", "designation", "capacity") or "")


def officer_appointed(row: dict) -> Any:
    """An officer's appointment date, however its column was named."""
    return _row_value(row, "appoint")


def row_amount(row: dict | None, *, prior: bool = False) -> float | None:
    """The amount of one statement row (this year's, or the prior year's column)."""
    if row is None:
        return None
    return _as_number(row.get("amount_prior_year" if prior else "amount"))


def _money(amount: float) -> str:
    return f"{amount:,.2f}"


_MONEY_TOLERANCE = 0.011

# Below this, a financial-statement arithmetic mismatch is still always
# flagged (never silently suppressed as "just rounding") — this only
# controls whether _materiality_note adds a "this is small" qualifier, so
# the reviewing agent/officer can weigh a $200-on-$2.4M gap differently
# from a $200,000 one, rather than the check deciding that for them.
_MATERIALITY_THRESHOLD_PCT = 0.1


def _materiality_note(diff: float, base: float) -> str | None:
    if not base:
        return None
    pct = abs(diff) / abs(base) * 100
    if pct < _MATERIALITY_THRESHOLD_PCT:
        return (
            f"Discrepancy is {pct:.3f}% of the reference figure — small enough to "
            "plausibly be rounding or a transcription slip rather than deliberate "
            "manipulation; use judgement on whether it's worth escalating."
        )
    return None


def _parse_field_date(value: Any, default_year: int | None = None) -> date | None:
    """A complete date from an extracted field's own value, in any form a document prints it (see
    fraud_detection/dates/date_parsing.py). A printed date with no year takes default_year if given."""
    return parse_date(value, default_year)



# ---------------------------------------------------------------------------
# needs_review downgrade
# ---------------------------------------------------------------------------


class _ReviewableFinding(Protocol):
    """
    The minimal shape _apply_needs_review needs — NumericalFinding and
    date_anomalies.DateFinding are separate (structurally identical)
    Pydantic classes, not a shared base class, since date_anomalies.py
    already imports this function from here and a reverse import would be
    circular. A Protocol lets one function serve both without either
    concrete type depending on the other.
    """

    field: str | None
    needs_review: bool

    def model_copy(self, *, update: dict[str, Any]) -> Self: ...


FindingT = TypeVar("FindingT", bound=_ReviewableFinding)


def _apply_needs_review(
    findings: list[FindingT],
    confidences: dict[str, float],
) -> list[FindingT]:
    """
    A failing value is downgraded to needs_review rather than reported as a
    confident anomaly when the top-level field it came from has extraction
    confidence below the same threshold the rest of the app uses to decide
    a field needs a loan officer's review (settings.EXTRACTION_CONFIDENCE_
    THRESHOLD). extracted_fields is trusted as already human-reviewed
    otherwise, so this is the only remaining signal that a failing value
    might be an extraction artefact rather than a genuine anomaly — there's
    no raw-text corroboration pass any more (that needed re-ingesting the
    file on every check run just to sanity-check a field a human has
    already verified).

    A value extracted in several places (also_fields) uses the most
    confident of them: one confident reading is enough to rule out an
    extraction artefact.

    A finding with no `field` (nothing to look up) is never downgraded.
    """
    if not findings:
        return findings

    updated: list[FindingT] = []

    for finding in findings:
        # A nested value's path ("shareholders[1].address", "officer.address") comes from its top-level field.
        paths = [finding.field or "", *getattr(finding, "also_fields", [])]
        known = [c for p in paths if (c := confidences.get(re.split(r"[\[.]", p, maxsplit=1)[0])) is not None]
        low_confidence = bool(known) and max(known) < settings.EXTRACTION_CONFIDENCE_THRESHOLD

        if low_confidence and not finding.needs_review:
            updated.append(finding.model_copy(update={"needs_review": True}))
        else:
            updated.append(finding)

    return updated



# ---------------------------------------------------------------------------
# Check registry plumbing
# ---------------------------------------------------------------------------


CheckResult = tuple[list[NumericalFinding], SkippedCheck | None]


def _finding(
    code: str, severity: Severity, summary: str, *,
    field: str | None = None, stated: str | None = None, expected: str | None = None,
    severity_note: str | None = None, also_fields: list[str] | None = None,
) -> NumericalFinding:
    return NumericalFinding(
        check_code=code, severity=severity, summary=summary,
        field=field, stated=stated, expected=expected, severity_note=severity_note,
        also_fields=also_fields or [],
    )


def skipped(*, reason: str) -> SkippedCheck:
    """A skip whose check code the runner fills in from the check's registration."""
    return SkippedCheck(check_code="", reason=reason)


def _skip_missing(code: str, *names: str) -> SkippedCheck:
    return SkippedCheck(check_code=code, reason=f"Required field(s) not extracted: {', '.join(names)}")


def _skip_reference(code: str, table: str) -> SkippedCheck:
    return SkippedCheck(
        check_code=code,
        reason=f"No verified reference data available ({table} is not configured)",
    )
