"""Shared helpers, reference tables and the check context for the numerical checks."""

from __future__ import annotations

import calendar
import re
from typing import Any, Protocol, Self, TypeVar
from datetime import date
from ...config import settings
from ..font_consistency import ExtractedField
from .models import NumericalFinding, Severity, SkippedCheck


# ---------------------------------------------------------------------------
# Reference data — see module docstring. Empty until real tables are
# supplied; every check below degrades to SKIPPED, never a guess.
# ---------------------------------------------------------------------------


REFERENCE_DATA: dict[str, dict] = {
    # Canonical bank name -> how it's named on statements, and its ACRA
    # registration number (UEN), each from the bank's own published
    # documents (checked Oct 2026):
    #   DBS        dbs.com.sg account application forms ("DBS Bank Ltd Co. Reg. No: 196800306E")
    #   OCBC       OCBC's SGX announcements ("Co. Reg. no.: 193200032W")
    #   UOB        uob.com.sg terms & conditions ("Co. Reg. No. 193500026Z")
    #   Green Link glbank.com MAS Notice 637 disclosures ("UEN: 202115950G")
    # SWIFT BIC (first 8 characters; an 11-character form only adds a branch,
    # "XXX" = head office): DBS from dbs.com.sg's SWIFT code page, OCBC from
    # its Telegraphic Transfer Guide, UOB from its International Network
    # listing; Green Link's isn't on its own site, so it's taken from the
    # SWIFT directory as republished by Wise and XE.
    # GST registration numbers are deliberately absent: none could be
    # confirmed from an official source, and a guessed one would flag
    # genuine statements. BS.BANK_IDENTIFIERS only compares fields present here.
    # account_digits (BS.ACCOUNT_NUMBER_FORMAT): DBS 10 and POSB 9 from DBS's
    # "Bank & Branch Codes" page; OCBC "10 or 12 digits" from OCBC's help page;
    # UOB 10 from OCBC's funds-transfer tips PDF. Green Link publishes none.
    # All four registration numbers also match ACRA's own register
    # (data.gov.sg "Entities Registered with ACRA").
    "bank_identifiers": {
        # POSB is a DBS brand: same legal entity, UEN and BIC (DBS's SWIFT page
        # gives "DBS & POSB" one code).
        # gst_no: an IRAS GST number of the pre-UEN format, not the UEN; real DBS
        # statements print "Co. Reg. No. 196800306E GST Registration No:
        # MR-8500180-3". OCBC's, UOB's and GLDB's GST numbers couldn't be
        # confirmed from a reliable source (IRAS's register needs a CAPTCHA or an
        # API key), so they're left out and their GST check doesn't run.
        "DBS Bank Ltd": {
            "aliases": ("dbs", "posb", "development bank of singapore"),
            "registration_no": "196800306E", "gst_no": "MR-8500180-3", "bic": "DBSSSGSG",
            "account_digits": (9, 10),
        },
        "Oversea-Chinese Banking Corporation Limited": {
            "aliases": ("ocbc", "oversea-chinese banking", "oversea chinese banking"),
            "registration_no": "193200032W", "bic": "OCBCSGSG", "account_digits": (10, 12),
        },
        "United Overseas Bank Limited": {
            "aliases": ("uob", "united overseas bank"), "registration_no": "193500026Z",
            "bic": "UOVBSGSG", "account_digits": (10,),
        },
        "Green Link Digital Bank Pte. Ltd.": {
            "aliases": ("green link digital bank", "gldb"), "registration_no": "202115950G",
            "bic": "GLDTSGSG",
        },
    },
    # Effective date -> CPF ordinary wage ceiling (SGD/month), per CPF Board's
    # "What is the Ordinary Wage (OW) ceiling?" page. Keyed by date, not
    # year: the ceiling changed mid-2023.
    "cpf_ordinary_wage_ceiling": {
        "2016-01-01": 6_000, "2023-09-01": 6_300, "2024-01-01": 6_800,
        "2025-01-01": 7_400, "2026-01-01": 8_000,
    },
}


def _in_force_on(table: dict[str, float], on: date) -> float | None:
    """The value of a date-keyed reference table in force on a given day."""
    applicable = [(date.fromisoformat(k), v) for k, v in table.items() if date.fromisoformat(k) <= on]
    return max(applicable)[1] if applicable else None


def _bank_reference(bank_name: Any) -> dict | None:
    name = re.sub(r"[^a-z ]", " ", str(bank_name or "").lower())
    name = " ".join(name.split())
    for reference in REFERENCE_DATA["bank_identifiers"].values():
        for alias in reference["aliases"]:
            alias_norm = " ".join(re.sub(r"[^a-z ]", " ", alias).split())
            if re.search(rf"\b{re.escape(alias_norm)}\b", name):
                return reference
    return None




# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _decode(value: Any) -> Any:
    """Extracted field values are sometimes JSON-encoded a second time —
    the same quirk spelling_anomaly._decode_field_text documents."""
    if isinstance(value, str):
        try:
            import json
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _fields_by_name(fields: list[ExtractedField] | None) -> dict[str, Any]:
    return {f.field_name: _decode(f.field_value) for f in fields or []}


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


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = _RATE_SUFFIX_RE.sub("", value)
        cleaned = re.sub(rf"[,\s]|{_CURRENCY_SYMBOLS}|{_CURRENCY_WORDS}", "", text, flags=re.I)
        cleaned = cleaned.strip()
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = "-" + cleaned[1:-1]
        try:
            return float(cleaned)
        except ValueError:
            match = _CURRENTLY_RATE_RE.search(value)
            return float(match.group(1)) if match else None
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


_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun",
           "jul", "aug", "sep", "oct", "nov", "dec"]

_DATE_FIELD_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), "ymd"),
    # dd/mm/yyyy or dd-mm-yyyy — the day-first numeric shape shows up with
    # either separator depending on the document/locale; the ymd pattern
    # above already claims any dash-separated string starting with a
    # 4-digit year, so there's no ambiguity in also accepting a dash here.
    (re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$"), "dmy"),
    (re.compile(r"^(\d{1,2})[- ]([A-Za-z]{3,9})[- ](\d{4})$"), "dMy"),
    (re.compile(r"^([A-Za-z]{3,9})[- ](\d{1,2}),?\s*(\d{4})$"), "Mdy"),
]


def _month_number(name: str) -> int:
    lowered = name.lower()[:3]
    return _MONTHS.index(lowered) + 1 if lowered in _MONTHS else 0


_TRAILING_TIME_RE = re.compile(r"\s+\d{1,2}:\d{2}(:\d{2})?(\s*[AaPp][Mm])?$")


def _parse_field_date(value: Any) -> date | None:
    """
    Parses a date out of an extracted field's own value (not free-form
    prose — see numerical checks' text-parsing sibling for that). Accepts
    ISO, dd/mm/yyyy, "15 March 2026" / "15-Mar-2026", and "March 15, 2026" —
    the shapes this pipeline's own doc_gen_scripts templates actually
    render dates in. A trailing time-of-day ("05 Sep 2026 08:12:17", as a
    document's own "printed on"/"generated at" timestamp is often rendered)
    is stripped first — only the date portion is validated.
    """
    if not isinstance(value, str):
        return None
    text = _TRAILING_TIME_RE.sub("", value.strip())
    for pattern, order in _DATE_FIELD_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        groups = match.groups()
        try:
            if order == "ymd":
                year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
            elif order == "dmy":
                day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
            elif order == "dMy":
                day, month, year = int(groups[0]), _month_number(groups[1]), int(groups[2])
            else:
                month, day, year = _month_number(groups[0]), int(groups[1]), int(groups[2])
            if not 1 <= month <= 12:
                return None
            if not 1 <= day <= calendar.monthrange(year, month)[1]:
                return None
            return date(year, month, day)
        except (ValueError, IndexError):
            return None
    return None



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

    A finding with no `field` (nothing to look up) is never downgraded.
    """
    if not findings:
        return findings

    updated: list[FindingT] = []

    for finding in findings:
        base_field = (finding.field or "").split("[")[0]
        confidence = confidences.get(base_field)
        low_confidence = confidence is not None and confidence < settings.EXTRACTION_CONFIDENCE_THRESHOLD

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
    severity_note: str | None = None,
) -> NumericalFinding:
    return NumericalFinding(
        check_code=code, severity=severity, summary=summary,
        field=field, stated=stated, expected=expected, severity_note=severity_note,
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
