# numerical_validation.py
"""
Content Validation Specialist — single-document numerical checks.

Every check is deterministic code that runs on a document's already-
extracted, already-human-reviewed fields — no file download or
re-ingestion happens here. Two forms of "this check didn't produce a
verdict":

* SKIPPED — the field(s) a check needs were not extracted at all. Recorded
  in `skipped_checks` with a reason, never silently dropped.
* NEEDS_REVIEW — the check ran and something failed, but the failing
  value's field has low extraction confidence (see _apply_needs_review) —
  so the failure might be an extraction artefact rather than a genuine
  anomaly. Reported as a finding with needs_review=True rather than a
  confident flag.

Four severities:
  impossible   - the document cannot be genuine, or was not extracted
                 correctly, as it stands (an identity that must hold in any
                 real document is broken).
  inconsistent - two values in the document contradict each other.
  implausible  - possible, but very unlikely in a real document.
  signal       - weak evidence worth a look but not conclusive alone.

Reference-data checks (an issuer's registration numbers, the deposit
insurance limit in force on a date, a government loan scheme's parameters,
a tax year's exemption tiers) need real tables this codebase doesn't have.
Building them would mean inventing a plausible-looking constant with no way
to verify it — worse than not checking at all, since a wrong constant
flags genuine documents. These are wired up against REFERENCE_DATA (empty
by default) and report SKIPPED with the specific table that's missing,
never a fabricated value. See that dict's own comment for what's needed.

Field names: every check reads from a documented, fixed field-name contract
per document type (see each check's docstring). Several of these fields
aren't in extraction_prompts.EXTRACTION_SCHEMAS yet (credit report score/
risk grade, FATCA entity type and controlling persons, detailed proof-of-
address billing fields, and others) — those checks are real and will start
firing the moment extraction is extended to populate them; today they
report SKIPPED, which is the correct, honest behaviour for "the fields a
check needs were not extracted."
"""
from __future__ import annotations

import calendar
import math
import re
from datetime import date
from typing import Any, Callable, Literal, Protocol, Self, TypeVar

from pydantic import BaseModel

from ..config import settings
from .font_consistency import ExtractedField

Severity = Literal["impossible", "inconsistent", "implausible", "signal"]

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class NumericalFinding(BaseModel):
    check_code: str
    severity: Severity
    field: str | None = None
    stated: str | None = None
    expected: str | None = None
    # True when the failing value has low extraction confidence, or its raw
    # string can't be found anywhere in the document's own text — the
    # failure might be an extraction artefact rather than a genuine anomaly.
    needs_review: bool = False
    severity_note: str | None = None
    summary: str


class SkippedCheck(BaseModel):
    check_code: str
    reason: str


class BenfordDigit(BaseModel):
    digit: int
    count: int
    observed_percentage: float
    expected_percentage: float


class BenfordAnalysis(BaseModel):
    sample_size: int
    digits: list[BenfordDigit]
    mean_absolute_deviation: float | None = None
    chi_square: float | None = None
    chi_square_critical: float | None = None
    conforms: bool | None = None
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None


class NumericalValidationResult(BaseModel):
    """
    Response for GET /documents/{id}/detection/numerical-validation.
    Nothing is persisted — a pure read/compute, like the spelling and
    layout checks.

    Two independent Benford passes over transaction amounts (all amounts,
    and amounts with likely bank fees excluded) are named benford /
    benford_excluding_fees; both are None when the document type has no
    transaction list to run them on.
    """

    document_id: str
    document_type: str | None = None
    values_analysed: int
    findings: list[NumericalFinding]
    skipped_checks: list[SkippedCheck]
    benford: BenfordAnalysis
    benford_excluding_fees: BenfordAnalysis | None = None
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None


# ---------------------------------------------------------------------------
# Reference data — see module docstring. Empty until real tables are
# supplied; every check below degrades to SKIPPED, never a guess.
# ---------------------------------------------------------------------------

REFERENCE_DATA: dict[str, dict] = {
    # bank_name -> {"registration_no": ..., "gst_no": ...}
    "bank_identifiers": {},
    # ISO date string (from which this limit took effect) -> SGD limit
    "deposit_insurance_limits": {},
    # scheme_name -> {"amount_cap": ..., "risk_share_pct": ..., "effective_from": ...}
    "loan_scheme_params": {},
    # year_of_assessment (int) -> {"exempt_first_pct", "exempt_first_cap",
    # "exempt_next_pct", "exempt_next_cap", "tax_rate_pct", "rebate_pct",
    # "rebate_cap"} — the compute_noa_tax formula this pipeline's own
    # generator uses (75%/$10k, 50%/$190k, 17%, 50% capped $40k) is a real,
    # current Singapore scheme, not a placeholder, but it is a SINGLE
    # year's parameters hardcoded, not a year-keyed table — so NOA checks
    # below use it only when this dict has no year-specific override.
    "noa_tax_parameters": {},
    # calendar year (int) -> CPF ordinary wage ceiling in SGD/month
    "cpf_ordinary_wage_ceiling": {},
}

# The current scheme this pipeline's own NOA generator uses (see
# doc_gen_scripts/optional_documents/company_noa/generate_noa.py's
# compute_noa_tax) — real, current Singapore corporate tax policy, kept
# here as the fallback for a year not found in REFERENCE_DATA.
_CURRENT_NOA_SCHEME = {
    "exempt_first_pct": 0.75, "exempt_first_cap": 10_000,
    "exempt_next_pct": 0.50, "exempt_next_cap": 190_000,
    "tax_rate_pct": 0.17,
    "rebate_pct": 0.50, "rebate_cap": 40_000,
}


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


def _rows(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _is_item(ctx: _Ctx, *labels: str) -> tuple[float | None, float | None]:
    """Looks up a line item from income_statement_items by label (case-
    insensitive, first match wins across the candidate labels given — e.g.
    "profit before taxation" also matches a document that printed "profit
    before tax"). Returns (amount, amount_prior_year), either side None if
    that item/column wasn't extracted."""
    wanted = {label.lower() for label in labels}
    for row in _rows(ctx.fields.get("income_statement_items")):
        if str(row.get("label", "")).strip().lower() in wanted:
            return _as_number(row.get("amount")), _as_number(row.get("amount_prior_year"))
    return None, None


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


def _on_or_after(later: date | None, earlier: date | None) -> bool | None:
    if later is None or earlier is None:
        return None
    return later >= earlier


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


class _Ctx(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    fields: dict[str, Any]
    document_type: str | None
    today: date


def _finding(
    code: str, severity: Severity, summary: str, *,
    field: str | None = None, stated: str | None = None, expected: str | None = None,
    severity_note: str | None = None,
) -> NumericalFinding:
    return NumericalFinding(
        check_code=code, severity=severity, summary=summary,
        field=field, stated=stated, expected=expected, severity_note=severity_note,
    )


CheckFn = Callable[[_Ctx], "tuple[list[NumericalFinding], SkippedCheck | None]"]


def _need(ctx: _Ctx, code: str, *names: str) -> list[Any] | None:
    """Returns the values for `names` if every one is present and non-empty
    in ctx.fields, else None (caller should record a skip)."""
    values = []
    for name in names:
        value = ctx.fields.get(name)
        if value is None or value == "" or value == []:
            return None
        values.append(value)
    return values


def _skip_missing(code: str, *names: str) -> SkippedCheck:
    return SkippedCheck(check_code=code, reason=f"Required field(s) not extracted: {', '.join(names)}")


def _skip_reference(code: str, table: str) -> SkippedCheck:
    return SkippedCheck(
        check_code=code,
        reason=f"No verified reference data available ({table} is not configured)",
    )


# ---------------------------------------------------------------------------
# GEN.* / ID.* — checks that apply to every document type
# ---------------------------------------------------------------------------


# Real, published algorithm (verified against samliew/singapore-nric, a
# maintained npm library), not derived from memory alone: weights 2-7-6-5-
# 4-3-2 over the 7 digits, an offset added before the modulus (4 for T/G,
# 3 for M, 0 for S/F), remainder mod 11, and — only for the M series — the
# index is reflected as 10 - index before the table lookup.
_NRIC_WEIGHTS = (2, 7, 6, 5, 4, 3, 2)
_NRIC_TABLE_ST = "JZIHGFEDCBA"
_NRIC_TABLE_FG = "XWUTRQPNMLK"
_NRIC_TABLE_M = "KLJNPQRTUWX"
_NRIC_RE = re.compile(r"\b([STFGM])(\d{7})([A-Z])\b", re.IGNORECASE)


def _nric_expected_letter(prefix: str, digits: str) -> str | None:
    prefix = prefix.upper()
    weighted = sum(int(d) * w for d, w in zip(digits, _NRIC_WEIGHTS))
    offset = 4 if prefix in ("T", "G") else 3 if prefix == "M" else 0
    index = (offset + weighted) % 11
    if prefix == "M":
        index = 10 - index
        return _NRIC_TABLE_M[index]
    if prefix in ("S", "T"):
        return _NRIC_TABLE_ST[index]
    if prefix in ("F", "G"):
        return _NRIC_TABLE_FG[index]
    return None


def _extract_nric_candidates(fields: dict[str, Any]) -> list[str]:
    candidates: set[str] = set()
    for name in ("nric_number", "id_number", "individual_name"):
        pass  # names themselves never contain an NRIC; kept for clarity
    for name, value in fields.items():
        if isinstance(value, str):
            candidates.update(m.group(0).upper() for m in _NRIC_RE.finditer(value))
    return sorted(candidates)


def _check_nric_checksum(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """ID.NRIC_CHECKSUM. Runs against any field whose value matches an
    NRIC/FIN pattern, on every document type."""
    candidates = _extract_nric_candidates(ctx.fields)
    if not candidates:
        return [], _skip_missing("ID.NRIC_CHECKSUM", "nric_number")

    findings = []
    for value in candidates:
        prefix, digits, letter = value[0], value[1:8], value[8]
        expected = _nric_expected_letter(prefix, digits)
        if expected and expected != letter:
            findings.append(_finding(
                "ID.NRIC_CHECKSUM", "impossible",
                f"{value} has an invalid check letter — expected {expected}, got {letter}",
                field="nric_number", stated=value, expected=f"{value[:8]}{expected}",
            ))
    return findings, None


_UEN_RE = re.compile(r"^(\d{4})(\d{5}|-\d{4})[A-Z]$", re.IGNORECASE)
_UEN_BUSINESS_RE = re.compile(r"^\d{8}[A-Z]$", re.IGNORECASE)


def _check_uen_format(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """ID.UEN_FORMAT. A UEN is 9 or 10 characters ending in a letter, with
    a plausible year prefix for entity-registered UENs (businesses
    registered before UEN existed use an 8-digit business-registration
    number instead, which this doesn't apply to)."""
    values = _need(ctx, "ID.UEN_FORMAT", "uen")
    if values is None:
        return [], _skip_missing("ID.UEN_FORMAT", "uen")
    uen = str(values[0]).strip().upper()

    if _UEN_BUSINESS_RE.match(uen):
        return [], None

    match = _UEN_RE.match(uen)
    if not match:
        return [_finding(
            "ID.UEN_FORMAT", "impossible", f"{uen} does not match any valid UEN format",
            field="uen", stated=uen,
        )], None

    year_prefix = int(match.group(1))
    current_year = ctx.today.year
    if not 1965 <= year_prefix <= current_year:
        return [_finding(
            "ID.UEN_FORMAT", "impossible",
            f"{uen}'s year prefix ({year_prefix}) is not a plausible registration year",
            field="uen", stated=uen,
        )], None
    return [], None


def _is_forward_looking_date_field(name: str) -> bool:
    """Fields that describe a scheduled point in the future by design — a
    loan's review date, a bill's due date, a passport's expiry, a
    contract's end/renewal date. GEN.DATE_VALID's "must not be after
    today" rule doesn't apply to these (each either has its own, more
    specific date-logic check elsewhere — POA.DUE_DATE, ID.EXPIRED,
    LOAN.DATE_LOGIC's validity window — or simply isn't meant to be
    validated against "today" at all) — but they're still checked for
    being parseable at all, same as every other date field."""
    return bool(re.search(
        r"expiry|expires|due|renewal|review|valid_until|maturity|next_|contract_end",
        name, re.IGNORECASE,
    ))


def _looks_like_date_field(name: str) -> bool:
    """Shared with date_anomalies.py (GEN.DATE_VALID and the labelled date
    extraction both use this name pattern)."""
    return bool(re.search(r"date|_at$|_on$", name, re.IGNORECASE)) and name not in (
        "date_of_birth",  # can legitimately be decades in the past but is
        # checked for internal consistency elsewhere, not "not in the future"
        # in a way that needs its own generic pass — kept out only to avoid
        # duplicate findings alongside the NRIC/passport age checks.
    )


def _check_amount_parse(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """GEN.AMOUNT_PARSE. Every amount-like field must parse as a number,
    with a consistent sign/currency convention. A value that fails to
    parse goes to needs_review (via the low-confidence path below is not
    guaranteed, so this check marks it directly) rather than being treated
    as a confident anomaly — it's very often an OCR artefact, not a fake
    figure."""
    amount_fields = {
        name: value for name, value in ctx.fields.items()
        if isinstance(value, (str, int, float)) and _looks_like_amount_field(name)
    }
    if not amount_fields:
        return [], _skip_missing("GEN.AMOUNT_PARSE", "(no amount-like fields)")

    findings = []
    for name, raw in amount_fields.items():
        if _as_number(raw) is None:
            findings.append(NumericalFinding(
                check_code="GEN.AMOUNT_PARSE", severity="signal", field=name, stated=str(raw),
                needs_review=True,
                summary=f"{name} ({raw!r}) does not parse as a numeric amount",
            ))
    return findings, None


def _looks_like_amount_field(name: str) -> bool:
    return bool(re.search(
        r"amount|balance|pay$|_pay|capital|value|tax|eci|income|price|rate$",
        name, re.IGNORECASE,
    ))


GENERAL_CHECKS: list[CheckFn] = [
    _check_nric_checksum,
    _check_uen_format,
    _check_amount_parse,
]


# ---------------------------------------------------------------------------
# Bank statement
# ---------------------------------------------------------------------------


class _BalanceBreak(BaseModel):
    index: int
    previous: float
    amount: float
    balance: float
    expected: float
    description: str
    sign_inverted: bool = False


_SYSTEMIC_BREAK_RATIO = 0.25
_SYSTEMIC_MIN_BREAKS = 4


def _running_balance_breaks(rows: list[dict]) -> list[_BalanceBreak]:
    breaks: list[_BalanceBreak] = []
    previous: float | None = None
    for index, row in enumerate(rows):
        balance = _as_number(row.get("balance"))
        amount = _as_number(row.get("amount"))
        if previous is None or amount is None:
            previous = balance
            continue
        expected = previous + amount
        if balance is None:
            previous = expected
            continue
        if abs(expected - balance) > _MONEY_TOLERANCE:
            breaks.append(_BalanceBreak(
                index=index, previous=previous, amount=amount, balance=balance,
                expected=expected, description=str(row.get("description") or "").strip(),
                sign_inverted=abs(previous - amount - balance) <= _MONEY_TOLERANCE,
            ))
        previous = balance
    return breaks


def _balance_finding(b: _BalanceBreak) -> NumericalFinding:
    where = f"transaction {b.index + 1}" + (f" ({b.description})" if b.description else "")
    if b.sign_inverted:
        return _finding(
            "BS.RUNNING_BALANCE", "inconsistent",
            f"Debit/credit direction looks reversed on {where}: recorded as "
            f"{_money(b.amount)}, but the balance moved from {_money(b.previous)} to "
            f"{_money(b.balance)} — a change of {_money(-b.amount)}",
            field=f"transactions[{b.index}].amount", stated=_money(b.amount), expected=_money(-b.amount),
            severity_note="Direction, not magnitude: usually a debit/credit read the wrong way round",
        )
    return _finding(
        "BS.RUNNING_BALANCE", "impossible",
        f"Running balance does not reconcile on {where}: "
        f"{_money(b.previous)} {'-' if b.amount < 0 else '+'} {_money(abs(b.amount))} = "
        f"{_money(b.expected)}, but the statement shows {_money(b.balance)}",
        field=f"transactions[{b.index}].balance", stated=_money(b.balance), expected=_money(b.expected),
    )


def _check_running_balance(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """
    BS.RUNNING_BALANCE. Each balance must equal the previous one plus that
    row's amount. Direction (oldest-first vs newest-first) is worked out
    from the figures rather than assumed — verified against this
    pipeline's own real extracted statements, most list oldest-first but a
    real minority list newest-first, and assuming one direction produced
    dozens of false findings on entirely genuine statements.
    """
    values = _need(ctx, "BS.RUNNING_BALANCE", "transactions")
    if values is None:
        return [], _skip_missing("BS.RUNNING_BALANCE", "transactions")
    rows = _rows(values[0])
    if len(rows) < 2:
        return [], _skip_missing("BS.RUNNING_BALANCE", "transactions (fewer than 2 rows)")

    oldest_first = _running_balance_breaks(rows)
    newest_first = _running_balance_breaks(list(reversed(rows)))
    if len(newest_first) < len(oldest_first):
        last = len(rows) - 1
        breaks = [b.model_copy(update={"index": last - b.index}) for b in newest_first]
    else:
        breaks = oldest_first

    if not breaks:
        return [], None

    comparable = max(len(rows) - 1, 1)
    if len(breaks) >= _SYSTEMIC_MIN_BREAKS and len(breaks) / comparable > _SYSTEMIC_BREAK_RATIO:
        return [_finding(
            "BS.RUNNING_BALANCE", "implausible",
            f"The running balance fails to reconcile on {len(breaks)} of {comparable} "
            "transaction lines, in both date directions — the balance column could not be "
            "followed through this statement",
            field="transactions", stated=f"{len(breaks)} of {comparable} lines",
            expected="a balance column that reconciles end to end",
            severity_note="Systemic rather than per-figure: usually means the column couldn't "
                           "be read cleanly, not that this many lines were edited",
        )], None

    return [_balance_finding(b) for b in breaks], None


def _check_open_close(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """
    BS.OPEN_CLOSE. Opening balance + total credits - total debits =
    closing balance. Prefers the statement's own stated opening_balance/
    ledger_balance header fields as the opening/closing anchors when
    they're extracted — more robust than deriving both purely from the
    transaction rows' own running-balance column, which breaks if that
    list is incomplete or out of order. Falls back to the transaction-
    derived values when the header fields weren't extracted.

    BS.STATED_OPENING / BS.STATED_CLOSING: when both a stated header value
    and the transaction-derived value are available, they're also cross-
    checked against each other directly — a mismatch here means the
    printed header and the transaction list disagree, independent of
    whether the credit/debit arithmetic itself reconciles.
    """
    values = _need(ctx, "BS.OPEN_CLOSE", "transactions")
    if values is None:
        return [], _skip_missing("BS.OPEN_CLOSE", "transactions")
    rows = _rows(values[0])
    amounts = [_as_number(r.get("amount")) for r in rows]
    balances = [_as_number(r.get("balance")) for r in rows]
    first_balance = balances[0] if balances else None
    last_balance = balances[-1] if balances else None
    if not rows or any(a is None for a in amounts) or first_balance is None or last_balance is None:
        return [], _skip_missing("BS.OPEN_CLOSE", "transactions[].amount/.balance")
    amounts = [a for a in amounts if a is not None]

    derived_opening = first_balance - amounts[0]
    derived_closing = last_balance
    stated_opening = _as_number(ctx.fields.get("opening_balance"))
    stated_closing = _as_number(ctx.fields.get("ledger_balance"))

    opening = stated_opening if stated_opening is not None else derived_opening
    closing = stated_closing if stated_closing is not None else derived_closing
    credits = sum(a for a in amounts if a and a > 0)
    debits = sum(-a for a in amounts if a and a < 0)
    expected_closing = opening + credits - debits

    findings = []
    if abs(expected_closing - closing) > _MONEY_TOLERANCE:
        findings.append(_finding(
            "BS.OPEN_CLOSE", "impossible",
            f"Opening balance {_money(opening)} + credits {_money(credits)} - debits "
            f"{_money(debits)} = {_money(expected_closing)}, but the closing balance is "
            f"{_money(closing)}",
            field="transactions", stated=_money(closing), expected=_money(expected_closing),
        ))

    if stated_opening is not None and abs(stated_opening - derived_opening) > _MONEY_TOLERANCE:
        findings.append(_finding(
            "BS.STATED_OPENING", "inconsistent",
            f"Stated opening balance {_money(stated_opening)} does not match the balance "
            f"implied by the first transaction ({_money(derived_opening)})",
            field="opening_balance", stated=_money(stated_opening), expected=_money(derived_opening),
        ))

    if stated_closing is not None and abs(stated_closing - derived_closing) > _MONEY_TOLERANCE:
        findings.append(_finding(
            "BS.STATED_CLOSING", "inconsistent",
            f"Stated ledger balance {_money(stated_closing)} does not match the last "
            f"transaction's running balance ({_money(derived_closing)})",
            field="ledger_balance", stated=_money(stated_closing), expected=_money(derived_closing),
        ))

    return findings, None


def _check_totals(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """BS.TOTALS. Any stated total_credits/total_debits/transaction_count
    fields must match the sum/count of the transaction lines."""
    values = _need(ctx, "BS.TOTALS", "transactions")
    if values is None:
        return [], _skip_missing("BS.TOTALS", "transactions")
    rows = _rows(values[0])
    amounts = [a for a in (_as_number(r.get("amount")) for r in rows) if a is not None]

    findings = []
    checks = [
        ("total_credits", sum(a for a in amounts if a > 0)),
        ("total_debits", -sum(a for a in amounts if a < 0)),
        ("transaction_count", float(len(rows))),
    ]
    ran_any = False
    for field_name, computed in checks:
        stated_raw = ctx.fields.get(field_name)
        if stated_raw is None:
            continue
        ran_any = True
        stated = _as_number(stated_raw)
        if stated is None:
            continue
        tolerance = _MONEY_TOLERANCE if field_name != "transaction_count" else 0.5
        if abs(stated - computed) > tolerance:
            findings.append(_finding(
                "BS.TOTALS", "inconsistent",
                f"Stated {field_name} ({stated:,.2f}) does not match the transaction lines "
                f"({computed:,.2f})",
                field=field_name, stated=f"{stated:,.2f}", expected=f"{computed:,.2f}",
            ))
    if not ran_any:
        return [], _skip_missing("BS.TOTALS", "total_credits/total_debits/transaction_count")
    return findings, None


def _check_overdraft_limit(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """BS.OVERDRAFT_LIMIT. No balance may fall below the negative of the
    overdraft limit (including a limit of zero)."""
    values = _need(ctx, "BS.OVERDRAFT_LIMIT", "transactions", "overdraft_limit")
    if values is None:
        return [], _skip_missing("BS.OVERDRAFT_LIMIT", "transactions", "overdraft_limit")
    rows, limit_raw = values
    limit = _as_number(limit_raw)
    if limit is None:
        return [], _skip_missing("BS.OVERDRAFT_LIMIT", "overdraft_limit")

    findings = []
    for i, row in enumerate(_rows(rows)):
        balance = _as_number(row.get("balance"))
        if balance is not None and balance < -abs(limit) - _MONEY_TOLERANCE:
            findings.append(_finding(
                "BS.OVERDRAFT_LIMIT", "impossible",
                f"Balance {_money(balance)} on transaction {i + 1} exceeds the overdraft "
                f"limit of {_money(limit)}",
                field=f"transactions[{i}].balance", stated=_money(balance), expected=f">= -{_money(limit)}",
            ))
    return findings, None


def _check_available_vs_ledger(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """BS.AVAILABLE_VS_LEDGER. Available balance = ledger balance - holds."""
    values = _need(ctx, "BS.AVAILABLE_VS_LEDGER", "ledger_balance", "available_balance")
    if values is None:
        return [], _skip_missing("BS.AVAILABLE_VS_LEDGER", "ledger_balance", "available_balance")
    ledger, available = (_as_number(v) for v in values)
    holds = _as_number(ctx.fields.get("holds")) or 0.0
    if ledger is None or available is None:
        return [], _skip_missing("BS.AVAILABLE_VS_LEDGER", "ledger_balance", "available_balance")
    expected = ledger - holds
    if abs(expected - available) > _MONEY_TOLERANCE:
        return [_finding(
            "BS.AVAILABLE_VS_LEDGER", "inconsistent",
            f"Available balance {_money(available)} does not equal ledger balance "
            f"{_money(ledger)} minus holds {_money(holds)} ({_money(expected)})",
            field="available_balance", stated=_money(available), expected=_money(expected),
        )], None
    return [], None


_ROUNDED_AMOUNT_IN_TEXT_RE = re.compile(
    rf"(?:{_CURRENCY_WORDS}|{_CURRENCY_SYMBOLS})\s?(\d{{1,3}}(?:,\d{{3}})*)"
)


def _check_narrative_amount(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """BS.NARRATIVE_AMOUNT. Where a transaction description repeats a
    rounded amount, it must match the amount column to within $1."""
    values = _need(ctx, "BS.NARRATIVE_AMOUNT", "transactions")
    if values is None:
        return [], _skip_missing("BS.NARRATIVE_AMOUNT", "transactions")

    findings = []
    ran_any = False
    for i, row in enumerate(_rows(values[0])):
        description = str(row.get("description") or "")
        match = _ROUNDED_AMOUNT_IN_TEXT_RE.search(description)
        amount = _as_number(row.get("amount"))
        if not match or amount is None:
            continue
        ran_any = True
        narrative_amount = _as_number(match.group(1))
        if narrative_amount is not None and abs(abs(amount) - narrative_amount) > 1.0:
            findings.append(_finding(
                "BS.NARRATIVE_AMOUNT", "inconsistent",
                f"Transaction {i + 1}'s description states {_money(narrative_amount)}, but the "
                f"amount column shows {_money(abs(amount))}",
                field=f"transactions[{i}].description", stated=_money(narrative_amount),
                expected=_money(abs(amount)),
            ))
    if not ran_any:
        return [], _skip_missing("BS.NARRATIVE_AMOUNT", "transactions[].description (no repeated amount found)")
    return findings, None


_FEE_KEYWORDS = re.compile(r"fee|charge|fast\b|giro|interbank", re.IGNORECASE)
_FEE_BEARING_KEYWORDS = re.compile(r"fast\b|giro|interbank|salary|payroll", re.IGNORECASE)


def _check_fee_schedule(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """BS.FEE_SCHEDULE. A transaction of a kind that normally carries a fee
    should have one nearby, and recurring fees should be a consistent
    amount."""
    values = _need(ctx, "BS.FEE_SCHEDULE", "transactions")
    if values is None:
        return [], _skip_missing("BS.FEE_SCHEDULE", "transactions")
    rows = _rows(values[0])
    if len(rows) < 3:
        return [], _skip_missing("BS.FEE_SCHEDULE", "transactions (too few rows)")

    fee_amounts_by_desc: dict[str, list[float]] = {}
    for row in rows:
        description = str(row.get("description") or "")
        amount = _as_number(row.get("amount"))
        if amount is not None and _FEE_KEYWORDS.search(description):
            key = re.sub(r"\d+", "#", description.strip().lower())
            fee_amounts_by_desc.setdefault(key, []).append(abs(amount))

    findings = []
    for desc, amounts in fee_amounts_by_desc.items():
        if len(amounts) < 3:
            continue
        mean = sum(amounts) / len(amounts)
        if mean == 0:
            continue
        if any(abs(a - mean) / mean > 0.5 for a in amounts):
            findings.append(_finding(
                "BS.FEE_SCHEDULE", "implausible",
                f"Recurring charge \"{desc}\" varies widely across the statement "
                f"({', '.join(_money(a) for a in amounts)})",
                field="transactions",
            ))
    return findings, None


def _check_bank_identifiers(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """BS.BANK_IDENTIFIERS. Registration/GST numbers must match the
    reference table for the named bank."""
    if not REFERENCE_DATA["bank_identifiers"]:
        return [], _skip_reference("BS.BANK_IDENTIFIERS", "bank_identifiers")
    values = _need(ctx, "BS.BANK_IDENTIFIERS", "bank_name")
    if values is None:
        return [], _skip_missing("BS.BANK_IDENTIFIERS", "bank_name")
    reference = REFERENCE_DATA["bank_identifiers"].get(str(values[0]))
    if reference is None:
        return [], _skip_reference("BS.BANK_IDENTIFIERS", f"bank_identifiers[{values[0]!r}]")
    findings = []
    for key in ("registration_no", "gst_no"):
        stated, expected = ctx.fields.get(key), reference.get(key)
        if stated and expected and str(stated).strip() != str(expected).strip():
            findings.append(_finding(
                "BS.BANK_IDENTIFIERS", "impossible", f"{key} {stated!r} does not match {expected!r}",
                field=key, stated=str(stated), expected=str(expected),
            ))
    return findings, None


def _check_deposit_insurance_text(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """BS.DEPOSIT_INSURANCE_TEXT."""
    if not REFERENCE_DATA["deposit_insurance_limits"]:
        return [], _skip_reference("BS.DEPOSIT_INSURANCE_TEXT", "deposit_insurance_limits")
    return [], _skip_reference("BS.DEPOSIT_INSURANCE_TEXT", "deposit_insurance_limits")


# Verified against this pipeline's own real extracted transaction
# descriptions (e.g. "4933-XXXX-XXXX-6411"): 4 digits, two masked groups,
# 4 digits. The second alternative covers the other common shape, a
# straight run of mask characters ending in the last 4 real digits.
_CARD_RE = re.compile(r"\b\d{4}-[Xx*]{4}-[Xx*]{4}-\d{4}\b|\b[Xx*]{4,}\d{4}\b")


def _check_card_diversity(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """BS.CARD_DIVERSITY. A near one-to-one ratio of distinct masked card
    numbers to card transactions is a signal."""
    values = _need(ctx, "BS.CARD_DIVERSITY", "transactions")
    if values is None:
        return [], _skip_missing("BS.CARD_DIVERSITY", "transactions")
    card_txns = 0
    cards: set[str] = set()
    for row in _rows(values[0]):
        description = str(row.get("description") or "")
        match = _CARD_RE.search(description)
        if match:
            card_txns += 1
            cards.add(match.group(0))
    if card_txns < 5:
        return [], _skip_missing("BS.CARD_DIVERSITY", "transactions (fewer than 5 card transactions)")
    if len(cards) / card_txns > 0.8:
        return [_finding(
            "BS.CARD_DIVERSITY", "signal",
            f"{len(cards)} distinct masked card numbers across only {card_txns} card "
            "transactions — unusually little reuse of the same card",
            field="transactions",
        )], None
    return [], None


def _check_recurring_variance(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """BS.RECURRING_VARIANCE. A recurring payee (rent, subscriptions)
    whose amount varies beyond a configurable percentage."""
    values = _need(ctx, "BS.RECURRING_VARIANCE", "transactions")
    if values is None:
        return [], _skip_missing("BS.RECURRING_VARIANCE", "transactions")
    by_payee: dict[str, list[float]] = {}
    for row in _rows(values[0]):
        description = str(row.get("description") or "").strip().lower()
        amount = _as_number(row.get("amount"))
        if description and amount is not None:
            key = re.sub(r"\d+", "#", description)
            by_payee.setdefault(key, []).append(abs(amount))

    findings = []
    for payee, amounts in by_payee.items():
        if len(amounts) < 3 or _FEE_KEYWORDS.search(payee):
            continue
        mean = sum(amounts) / len(amounts)
        if mean == 0:
            continue
        if any(abs(a - mean) / mean > 0.15 for a in amounts):
            findings.append(_finding(
                "BS.RECURRING_VARIANCE", "signal",
                f"Recurring payment \"{payee}\" varies by more than 15% across the statement "
                f"({', '.join(_money(a) for a in amounts)})",
                field="transactions",
            ))
    return findings, None


# --- Statistical checks (bank statement transactions) ----------------------

BENFORD_EXPECTED: dict[int, float] = {d: math.log10(1 + 1 / d) * 100 for d in range(1, 10)}
BENFORD_MIN_SAMPLE = 100
_CHI_SQUARE_CRITICAL_8DF_P01 = 20.090


def _benford_unavailable(reason: str, sample_size: int = 0) -> BenfordAnalysis:
    # No digits padded in with zeroed-out counts — there's nothing to show
    # when the analysis never ran at all, and a 9-row table of zeros reads
    # as real (if boring) data rather than "not applicable".
    return BenfordAnalysis(
        sample_size=sample_size, digits=[],
        unable_to_assess=True, unable_to_assess_reason=reason,
    )


def _leading_digit(magnitude: float) -> int | None:
    text = f"{magnitude:.10e}"
    first = text[0]
    return int(first) if first.isdigit() and first != "0" else None


def _benford(amounts: list[float], code: str) -> tuple[BenfordAnalysis, list[NumericalFinding]]:
    digits = [d for d in (_leading_digit(abs(a)) for a in amounts if a) if d is not None]
    if len(digits) < BENFORD_MIN_SAMPLE:
        return _benford_unavailable(
            f"Only {len(digits)} eligible value(s); Benford's Law needs at least "
            f"{BENFORD_MIN_SAMPLE} to be meaningful", sample_size=len(digits),
        ), []

    total = len(digits)
    rows: list[BenfordDigit] = []
    deviations, chi_square = [], 0.0
    for digit in range(1, 10):
        count = digits.count(digit)
        observed = count / total * 100
        expected = BENFORD_EXPECTED[digit]
        expected_count = total * expected / 100
        deviations.append(abs(observed - expected) / 100)
        chi_square += (count - expected_count) ** 2 / expected_count
        rows.append(BenfordDigit(digit=digit, count=count, observed_percentage=round(observed, 4),
                                  expected_percentage=round(expected, 4)))

    conforms = chi_square <= _CHI_SQUARE_CRITICAL_8DF_P01
    analysis = BenfordAnalysis(
        sample_size=total, digits=rows, mean_absolute_deviation=round(sum(deviations) / 9, 6),
        chi_square=round(chi_square, 4), chi_square_critical=_CHI_SQUARE_CRITICAL_8DF_P01,
        conforms=conforms, unable_to_assess=False,
    )
    if conforms:
        return analysis, []

    worst = max(rows, key=lambda r: abs(r.observed_percentage - r.expected_percentage))
    return analysis, [_finding(
        code, "signal",
        f"Leading-digit distribution across {total} amounts differs from Benford's Law by "
        f"more than chance (chi-square {chi_square:.1f} against a p<0.01 critical value of "
        f"{_CHI_SQUARE_CRITICAL_8DF_P01}); the largest gap is digit {worst.digit}",
        field="transactions",
        severity_note="Statistical, not per-figure: describes the whole distribution, not any "
                       "single amount",
    )]


def _check_duplicates(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """STAT.DUPLICATES. Repeated/near-identical amounts to the same payee
    within a short window."""
    values = _need(ctx, "STAT.DUPLICATES", "transactions")
    if values is None:
        return [], _skip_missing("STAT.DUPLICATES", "transactions")
    seen: dict[tuple[str, float], int] = {}
    findings = []
    for i, row in enumerate(_rows(values[0])):
        description = str(row.get("description") or "").strip().lower()
        amount = _as_number(row.get("amount"))
        if amount is None or not description:
            continue
        key = (description, amount)
        if key in seen:
            findings.append(_finding(
                "STAT.DUPLICATES", "signal",
                f"Transaction {i + 1} repeats the same payee and amount as transaction "
                f"{seen[key] + 1}",
                field="transactions",
            ))
        seen[key] = i
    return findings, None


def _check_round_numbers(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """STAT.ROUND_NUMBERS. An unusually high share of round amounts."""
    values = _need(ctx, "STAT.ROUND_NUMBERS", "transactions")
    if values is None:
        return [], _skip_missing("STAT.ROUND_NUMBERS", "transactions")
    amounts = [a for a in (_as_number(r.get("amount")) for r in _rows(values[0])) if a]
    if len(amounts) < BENFORD_MIN_SAMPLE:
        return [], _skip_missing("STAT.ROUND_NUMBERS", "transactions (too few amounts)")
    round_share = sum(1 for a in amounts if abs(a) % 100 < 0.005) / len(amounts)
    if round_share > 0.30:
        return [_finding(
            "STAT.ROUND_NUMBERS", "signal",
            f"{round_share:.0%} of amounts are round multiples of 100 — higher than typical",
            field="transactions",
        )], None
    return [], None


def _check_last_two_digits(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """STAT.LAST_TWO_DIGITS. The last two digits (cents) should be close
    to uniformly distributed."""
    values = _need(ctx, "STAT.LAST_TWO_DIGITS", "transactions")
    if values is None:
        return [], _skip_missing("STAT.LAST_TWO_DIGITS", "transactions")
    cents = []
    for row in _rows(values[0]):
        amount = _as_number(row.get("amount"))
        if amount is not None and abs(round(amount, 2) - round(amount, 0)) > 0.001:
            cents.append(int(round(abs(amount) * 100)) % 100)
    if len(cents) < BENFORD_MIN_SAMPLE:
        return [], _skip_missing("STAT.LAST_TWO_DIGITS", "transactions (too few decimal amounts)")
    expected = len(cents) / 100
    chi_square = sum((cents.count(d) - expected) ** 2 / expected for d in range(100))
    # 99 degrees of freedom, p < 0.01 critical value.
    if chi_square > 135.807:
        return [_finding(
            "STAT.LAST_TWO_DIGITS", "signal",
            f"The last two digits of amounts are not uniformly distributed (chi-square "
            f"{chi_square:.1f})",
            field="transactions",
            severity_note="Statistical, not per-figure",
        )], None
    return [], None


def _run_benford_pair(ctx: _Ctx) -> tuple[BenfordAnalysis | None, BenfordAnalysis | None, list[NumericalFinding]]:
    """STAT.BENFORD_1D, run twice: once on all amounts, once excluding
    likely bank fees."""
    rows = _rows(ctx.fields.get("transactions"))
    if not rows:
        return None, None, []
    all_amounts = [a for a in (_as_number(r.get("amount")) for r in rows) if a]
    non_fee_amounts = [
        _as_number(r.get("amount")) for r in rows
        if not _FEE_KEYWORDS.search(str(r.get("description") or ""))
    ]
    non_fee_amounts = [a for a in non_fee_amounts if a]

    all_analysis, all_findings = _benford(all_amounts, "STAT.BENFORD_1D")
    excl_analysis, excl_findings = _benford(non_fee_amounts, "STAT.BENFORD_1D")
    return all_analysis, excl_analysis, all_findings + excl_findings


BANK_STATEMENT_CHECKS: list[CheckFn] = [
    _check_running_balance, _check_open_close, _check_totals,
    _check_overdraft_limit, _check_available_vs_ledger, _check_narrative_amount,
    _check_fee_schedule, _check_bank_identifiers, _check_deposit_insurance_text,
    _check_card_diversity, _check_recurring_variance, _check_duplicates,
    _check_round_numbers, _check_last_two_digits,
]


# ---------------------------------------------------------------------------
# Source of wealth
# ---------------------------------------------------------------------------


def _check_holding_value(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """SOW.HOLDING_VALUE. quantity x closing_price = market_value."""
    values = _need(ctx, "SOW.HOLDING_VALUE", "holdings")
    if values is None:
        return [], _skip_missing("SOW.HOLDING_VALUE", "holdings")
    findings = []
    for i, row in enumerate(_rows(values[0])):
        quantity, price, market_value = (
            _as_number(row.get("quantity")), _as_number(row.get("closing_price")),
            _as_number(row.get("market_value")),
        )
        if quantity is None or price is None or market_value is None:
            continue
        expected = quantity * price
        if abs(expected - market_value) > max(0.51, abs(expected) * 0.01):
            findings.append(_finding(
                "SOW.HOLDING_VALUE", "impossible",
                f"Holding {i + 1}: {quantity:g} x {_money(price)} = {_money(expected)}, but "
                f"market value shows {_money(market_value)}",
                field=f"holdings[{i}].market_value", stated=_money(market_value), expected=_money(expected),
            ))
    return findings, None


def _check_portfolio_sum(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """SOW.PORTFOLIO_SUM. Holdings must add up to the stated portfolio
    value."""
    values = _need(ctx, "SOW.PORTFOLIO_SUM", "holdings", "portfolio_value")
    if values is None:
        return [], _skip_missing("SOW.PORTFOLIO_SUM", "holdings", "portfolio_value")
    rows, stated_raw = values
    stated = _as_number(stated_raw)
    market_values = [_as_number(r.get("market_value")) for r in _rows(rows)]
    if stated is None or any(v is None for v in market_values) or not market_values:
        return [], _skip_missing("SOW.PORTFOLIO_SUM", "holdings[].market_value")
    total = sum(v for v in market_values if v is not None)
    if abs(total - stated) > _MONEY_TOLERANCE:
        return [_finding(
            "SOW.PORTFOLIO_SUM", "impossible",
            f"Holdings sum to {_money(total)}, but the stated portfolio value is {_money(stated)}",
            field="portfolio_value", stated=_money(stated), expected=_money(total),
            severity_note=_materiality_note(total - stated, total),
        )], None
    return [], None


def _check_nav(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """SOW.NAV. cash + portfolio_value - margin_loan = net_asset_value."""
    values = _need(ctx, "SOW.NAV", "cash_balance", "portfolio_value", "net_asset_value")
    if values is None:
        return [], _skip_missing("SOW.NAV", "cash_balance", "portfolio_value", "net_asset_value")
    cash, portfolio, nav = (_as_number(v) for v in values)
    margin_loan = _as_number(ctx.fields.get("margin_loan")) or 0.0
    if cash is None or portfolio is None or nav is None:
        return [], _skip_missing("SOW.NAV", "cash_balance", "portfolio_value", "net_asset_value")
    expected = cash + portfolio - margin_loan
    if abs(expected - nav) > _MONEY_TOLERANCE:
        return [_finding(
            "SOW.NAV", "impossible",
            f"Cash {_money(cash)} + portfolio {_money(portfolio)} - margin loan "
            f"{_money(margin_loan)} = {_money(expected)}, but net asset value shows "
            f"{_money(nav)}",
            field="net_asset_value", stated=_money(nav), expected=_money(expected),
            severity_note=_materiality_note(expected - nav, expected),
        )], None
    return [], None


def _check_fs_is_cast(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """FS.IS_CAST. Revenue -> gross profit -> operating expenses -> profit
    before tax -> profit for the year, both years presented.

    cost_of_sales/operating_expenses/tax_expense now live as rows inside
    income_statement_items (amount + amount_prior_year per row) rather than
    as their own flat *_prior_year fields — see _is_item."""
    findings = []
    ran_any = False
    cost_of_sales_cur, cost_of_sales_prior = _is_item(ctx, "cost of sales")
    opex_cur, opex_prior = _is_item(ctx, "total operating expenses")
    tax_cur, tax_prior = _is_item(ctx, "tax expense")
    for suffix, cost_of_sales, opex, tax in (
        ("", cost_of_sales_cur, opex_cur, tax_cur),
        ("_prior_year", cost_of_sales_prior, opex_prior, tax_prior),
    ):
        values = _need(ctx, "FS.IS_CAST", f"revenue{suffix}", f"profit_for_the_year{suffix}")
        if values is None or cost_of_sales is None or opex is None or tax is None:
            continue
        ran_any = True
        revenue, profit = (_as_number(v) for v in values)
        if revenue is None or profit is None:
            continue
        gross_profit = revenue - cost_of_sales
        profit_before_tax = gross_profit - opex
        expected = profit_before_tax - tax
        if abs(expected - profit) > _MONEY_TOLERANCE:
            label = "prior year" if suffix else "current year"
            findings.append(_finding(
                "FS.IS_CAST", "impossible",
                f"Income statement ({label}) does not cast: revenue {_money(revenue)} - COGS "
                f"{_money(cost_of_sales)} - opex {_money(opex)} - tax {_money(tax)} = "
                f"{_money(expected)}, but profit for the year shows {_money(profit)}",
                field=f"profit_for_the_year{suffix}", stated=_money(profit), expected=_money(expected),
                severity_note=_materiality_note(expected - profit, expected),
            ))
    if not ran_any:
        return [], _skip_missing("FS.IS_CAST", "revenue", "income_statement_items",
                                  "profit_for_the_year")
    return findings, None


# Keys into the single consolidated balance_sheet_items array — each row's
# "section" is expected to be one of these, matched case-insensitively.
_BS_SECTIONS = (
    ("current assets", "current_assets", "total_current_assets"),
    ("non-current assets", "non_current_assets", "total_non_current_assets"),
    ("current liabilities", "current_liabilities", "total_current_liabilities"),
    ("non-current liabilities", "non_current_liabilities", "total_non_current_liabilities"),
    ("equity", "equity", "total_equity"),
)


def _bs_section_rows(ctx: _Ctx, section: str) -> list[dict]:
    return [
        row for row in _rows(ctx.fields.get("balance_sheet_items"))
        if str(row.get("section", "")).strip().lower().replace(" ", "_") == section
    ]


def _check_fs_bs_cast(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """FS.BS_CAST. Each balance sheet section (grouped from the single
    balance_sheet_items array by its "section" key) adds up to its stated
    total."""
    findings = []
    ran_any = False
    for label, section, total_field in _BS_SECTIONS:
        rows = _bs_section_rows(ctx, section)
        stated_raw = ctx.fields.get(total_field)
        if not rows or stated_raw is None:
            continue
        ran_any = True
        amounts = [_as_number(r.get("amount")) for r in rows]
        stated = _as_number(stated_raw)
        if stated is None or any(a is None for a in amounts):
            continue
        total = sum(a for a in amounts if a is not None)
        if abs(total - stated) > _MONEY_TOLERANCE:
            findings.append(_finding(
                "FS.BS_CAST", "impossible",
                f"{label.capitalize()} items sum to {_money(total)}, but the stated total is "
                f"{_money(stated)}",
                field=total_field, stated=_money(stated), expected=_money(total),
                severity_note=_materiality_note(total - stated, total),
            ))
    if not ran_any:
        return [], _skip_missing("FS.BS_CAST", "balance_sheet_items", "total_*")
    return findings, None


def _check_fs_balance_identity(
    code: str, field: str, assets_raw: Any, liabilities_raw: Any, equity_raw: Any,
) -> list[NumericalFinding]:
    assets, liabilities, equity = (_as_number(v) for v in (assets_raw, liabilities_raw, equity_raw))
    if assets is None or liabilities is None or equity is None:
        return []
    expected = liabilities + equity
    if abs(assets - expected) > _MONEY_TOLERANCE:
        return [_finding(
            code, "impossible",
            f"Total assets {_money(assets)} does not equal total liabilities "
            f"{_money(liabilities)} + total equity {_money(equity)} ({_money(expected)})",
            field=field, stated=_money(assets), expected=_money(expected),
            severity_note=_materiality_note(assets - expected, assets),
        )]
    return []


def _check_fs_balances(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """
    FS.BALANCES. total_assets = total_liabilities + total_equity.

    FS.BALANCES_PRIOR_YEAR: a two-column statement (current year next to
    the prior year) states a full prior-year balance sheet too, not just
    its own headline figures — the same identity is checked there when
    the *_prior_year fields were extracted, independently of whether the
    current year's own figures are present or reconcile. Only skips (AC1's
    "field(s) not extracted" contract) when neither year's figures are
    available at all — same "ran_any" shape as this module's other
    multi-part checks (e.g. _check_cr_summary_counts).
    """
    findings: list[NumericalFinding] = []
    ran_any = False

    values = _need(ctx, "FS.BALANCES", "total_assets", "total_liabilities", "total_equity")
    if values is not None:
        ran_any = True
        findings.extend(_check_fs_balance_identity("FS.BALANCES", "total_assets", *values))

    prior = _need(
        ctx, "FS.BALANCES_PRIOR_YEAR",
        "total_assets_prior_year", "total_liabilities_prior_year", "total_equity_prior_year",
    )
    if prior is not None:
        ran_any = True
        findings.extend(
            _check_fs_balance_identity("FS.BALANCES_PRIOR_YEAR", "total_assets_prior_year", *prior)
        )

    if not ran_any:
        return [], _skip_missing("FS.BALANCES", "total_assets", "total_liabilities", "total_equity")
    return findings, None


def _check_fs_re_rollforward(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """FS.RE_ROLLFORWARD. Retained earnings rolls forward correctly, and
    last year's closing figure equals this year's opening figure."""
    values = _need(
        ctx, "FS.RE_ROLLFORWARD",
        "retained_earnings_opening", "profit_for_the_year", "dividends_paid", "retained_earnings_closing",
    )
    if values is None:
        return [], _skip_missing(
            "FS.RE_ROLLFORWARD", "retained_earnings_opening", "profit_for_the_year",
            "dividends_paid", "retained_earnings_closing",
        )
    opening, profit, dividends, closing = (_as_number(v) for v in values)
    if opening is None or profit is None or dividends is None or closing is None:
        return [], _skip_missing("FS.RE_ROLLFORWARD", "retained_earnings (unparseable)")

    findings = []
    expected_closing = opening + profit - dividends
    if abs(expected_closing - closing) > _MONEY_TOLERANCE:
        findings.append(_finding(
            "FS.RE_ROLLFORWARD", "impossible",
            f"Retained earnings: opening {_money(opening)} + profit {_money(profit)} - "
            f"dividends {_money(dividends)} = {_money(expected_closing)}, but closing shows "
            f"{_money(closing)}",
            field="retained_earnings_closing", stated=_money(closing), expected=_money(expected_closing),
            severity_note=_materiality_note(expected_closing - closing, expected_closing),
        ))
    prior_closing = _as_number(ctx.fields.get("retained_earnings_closing_prior_year"))
    if prior_closing is not None and abs(prior_closing - opening) > _MONEY_TOLERANCE:
        findings.append(_finding(
            "FS.RE_ROLLFORWARD", "impossible",
            f"This year's opening retained earnings ({_money(opening)}) does not equal last "
            f"year's closing figure ({_money(prior_closing)})",
            field="retained_earnings_opening", stated=_money(opening), expected=_money(prior_closing),
        ))
    return findings, None


def _check_fs_ppe_rollforward(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """FS.PPE_ROLLFORWARD. Implied PPE additions (closing - opening +
    depreciation) must not be negative. depreciation_expense now lives as a
    row inside income_statement_items — see _is_item."""
    values = _need(ctx, "FS.PPE_ROLLFORWARD", "ppe_opening", "ppe_closing")
    depreciation, _prior = _is_item(
        ctx, "depreciation of property, plant and equipment", "depreciation", "depreciation expense",
    )
    if values is None or depreciation is None:
        return [], _skip_missing(
            "FS.PPE_ROLLFORWARD", "ppe_opening", "ppe_closing", "income_statement_items(depreciation)",
        )
    opening, closing = (_as_number(v) for v in values)
    if opening is None or closing is None:
        return [], _skip_missing("FS.PPE_ROLLFORWARD", "ppe_* (unparseable)")
    implied_additions = closing - opening + depreciation
    if implied_additions < -_MONEY_TOLERANCE:
        return [_finding(
            "FS.PPE_ROLLFORWARD", "implausible",
            f"Implied PPE additions are negative ({_money(implied_additions)}): closing "
            f"{_money(closing)} - opening {_money(opening)} + depreciation {_money(depreciation)}",
            field="ppe_closing", stated=_money(closing), expected=f">= {_money(opening - depreciation)}",
        )], None
    return [], None


def _check_fs_tax_expense_rate(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """FS.TAX_EXPENSE_RATE. Tax expense should be close to 17% of profit
    after the partial tax exemption, not a flat 17% of profit before
    exemption. Both values now live as rows inside income_statement_items —
    see _is_item."""
    profit, _p = _is_item(ctx, "profit before taxation", "profit before tax")
    tax, _t = _is_item(ctx, "tax expense")
    if profit is None or tax is None:
        return [], _skip_missing("FS.TAX_EXPENSE_RATE", "income_statement_items(profit before tax, tax expense)")
    if profit <= 0:
        return [], _skip_missing("FS.TAX_EXPENSE_RATE", "profit_before_tax/tax_expense (unparseable)")
    flat_rate_tax = profit * _CURRENT_NOA_SCHEME["tax_rate_pct"]
    if abs(tax - flat_rate_tax) < max(1.0, flat_rate_tax * 0.02):
        return [_finding(
            "FS.TAX_EXPENSE_RATE", "inconsistent",
            f"Tax expense ({_money(tax)}) is a flat {_CURRENT_NOA_SCHEME['tax_rate_pct']:.0%} of "
            f"profit before tax with no sign of the partial tax exemption a genuine Singapore "
            f"company would apply",
            field="tax_expense", stated=_money(tax),
        )], None
    return [], None


def _check_fs_debt_classification(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """FS.DEBT_CLASSIFICATION. A term loan repaid in instalments should
    have a non-current portion."""
    values = _need(ctx, "FS.DEBT_CLASSIFICATION", "borrowings_current", "loan_tenure")
    if values is None:
        return [], _skip_missing("FS.DEBT_CLASSIFICATION", "borrowings_current", "loan_tenure")
    current, tenure_raw = values
    non_current = _as_number(ctx.fields.get("borrowings_non_current"))
    current_amount = _as_number(current)
    tenure = _as_number(tenure_raw)
    if current_amount is None or tenure is None or tenure <= 12:
        return [], _skip_missing("FS.DEBT_CLASSIFICATION", "borrowings_current/loan_tenure (unparseable)")
    if current_amount > 0 and (non_current is None or non_current <= 0):
        return [_finding(
            "FS.DEBT_CLASSIFICATION", "inconsistent",
            f"A loan with a {tenure:.0f}-month tenure is classified entirely as current, with "
            "no non-current portion",
            field="borrowings_non_current",
        )], None
    return [], None


_RATIO_BANDS = {
    "finance_cost_ratio": (0.0, 0.20),
    "depreciation_ratio": (0.0, 0.40),
    "gross_margin": (-0.5, 0.95),
}


def _check_fs_ratio_bands(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """FS.RATIO_BANDS. finance costs / average borrowings, depreciation /
    PPE, and gross margin must fall within configurable bands.
    finance_costs/depreciation_expense/cost_of_sales now live as rows
    inside income_statement_items — see _is_item."""
    findings = []
    ran_any = False

    costs, _c = _is_item(ctx, "finance costs")
    opening = _as_number(ctx.fields.get("borrowings_opening"))
    closing = _as_number(ctx.fields.get("borrowings_closing"))
    if costs is not None and opening is not None and closing is not None and (opening + closing) > 0:
        ran_any = True
        ratio = costs / ((opening + closing) / 2)
        lo, hi = _RATIO_BANDS["finance_cost_ratio"]
        if not lo <= ratio <= hi:
            findings.append(_finding(
                "FS.RATIO_BANDS", "implausible",
                f"Finance costs are {ratio:.1%} of average borrowings, outside the "
                f"{lo:.0%}-{hi:.0%} plausible band",
                field="finance_costs",
            ))

    depreciation, _d = _is_item(
        ctx, "depreciation of property, plant and equipment", "depreciation", "depreciation expense",
    )
    ppe = _as_number(ctx.fields.get("ppe_closing"))
    if depreciation is not None and ppe is not None and ppe > 0:
        ran_any = True
        ratio = depreciation / ppe
        lo, hi = _RATIO_BANDS["depreciation_ratio"]
        if not lo <= ratio <= hi:
            findings.append(_finding(
                "FS.RATIO_BANDS", "implausible",
                f"Depreciation is {ratio:.1%} of PPE, outside the {lo:.0%}-{hi:.0%} "
                "plausible band",
                field="depreciation_expense",
            ))

    cost, _cost_prior = _is_item(ctx, "cost of sales")
    revenue = _as_number(ctx.fields.get("revenue"))
    if revenue is not None and cost is not None and revenue > 0:
        ran_any = True
        ratio = (revenue - cost) / revenue
        lo, hi = _RATIO_BANDS["gross_margin"]
        if not lo <= ratio <= hi:
            findings.append(_finding(
                "FS.RATIO_BANDS", "implausible",
                f"Gross margin is {ratio:.1%}, outside the {lo:.0%}-{hi:.0%} plausible band",
                field="revenue",
            ))

    if not ran_any:
        return [], _skip_missing("FS.RATIO_BANDS", "income_statement_items/revenue/borrowings/ppe (none present)")
    return findings, None


def _check_fs_note_refs(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """FS.NOTE_REFS. Every note referenced elsewhere in the statements must
    exist in the notes. There's no standalone note_references field —
    references live as each line item's own "note" column, in both
    income_statement_items and balance_sheet_items."""
    notes = ctx.fields.get("notes")
    item_rows = [
        *_rows(ctx.fields.get("income_statement_items")),
        *_rows(ctx.fields.get("balance_sheet_items")),
    ]
    if notes is None or not item_rows:
        return [], _skip_missing("FS.NOTE_REFS", "notes", "income_statement_items/balance_sheet_items")
    note_numbers = {str(n.get("note_number")).strip() for n in _rows(notes) if n.get("note_number")}
    referenced = {str(r.get("note")).strip() for r in item_rows if r.get("note")}
    missing = referenced - note_numbers
    if missing:
        return [_finding(
            "FS.NOTE_REFS", "signal",
            f"Note(s) {', '.join(sorted(missing))} are referenced but not present",
            field="notes",
        )], None
    return [], None


SOURCE_OF_WEALTH_CHECKS: list[CheckFn] = [
    _check_holding_value, _check_portfolio_sum, _check_nav,
    _check_fs_is_cast, _check_fs_bs_cast, _check_fs_balances, _check_fs_re_rollforward,
    _check_fs_ppe_rollforward, _check_fs_tax_expense_rate, _check_fs_debt_classification,
    _check_fs_ratio_bands, _check_fs_note_refs,
]


# ---------------------------------------------------------------------------
# Notice of assessment
# ---------------------------------------------------------------------------


def _noa_scheme(year_of_assessment: int | None) -> dict:
    if year_of_assessment is not None:
        override = REFERENCE_DATA["noa_tax_parameters"].get(year_of_assessment)
        if override:
            return override
    return _CURRENT_NOA_SCHEME


def _compute_noa(eci: float, scheme: dict) -> dict[str, float]:
    first = min(eci, scheme["exempt_first_cap"])
    nxt = min(max(eci - scheme["exempt_first_cap"], 0), scheme["exempt_next_cap"])
    exempt_first = round(first * scheme["exempt_first_pct"])
    exempt_next = round(nxt * scheme["exempt_next_pct"])
    after = eci - exempt_first - exempt_next
    tax = round(after * scheme["tax_rate_pct"], 2)
    rebate = round(min(tax * scheme["rebate_pct"], scheme["rebate_cap"]), 2)
    payable = round(tax - rebate, 2)
    return {"exempt_first": exempt_first, "exempt_next": exempt_next, "after": after,
            "tax": tax, "rebate": rebate, "payable": payable}


def _noa_year(ctx: _Ctx) -> int | None:
    raw = ctx.fields.get("year_of_assessment")
    match = re.search(r"\d{4}", str(raw or ""))
    return int(match.group(0)) if match else None


def _check_noa_chain(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """
    NOA.EXEMPTION_TIERS / NOA.CHARGEABLE_AFTER_EXEMPTION / NOA.TAX_AT_RATE /
    NOA.REBATE / NOA.PAYABLE, run together since they're one arithmetic
    chain from ECI to payable tax.

    Uses this pipeline's own doc_gen_scripts formula (see
    doc_gen_scripts/optional_documents/company_noa/generate_noa.py's
    compute_noa_tax) as the current scheme — a real, current Singapore
    partial tax exemption scheme, not a placeholder — unless
    REFERENCE_DATA["noa_tax_parameters"] has an override for the specific
    year of assessment on this document.
    """
    values = _need(ctx, "NOA.PAYABLE", "eci", "tax_payable")
    if values is None:
        return [], _skip_missing("NOA.PAYABLE", "eci", "tax_payable")
    eci, stated_payable = (_as_number(v) for v in values)
    if eci is None or stated_payable is None:
        return [], _skip_missing("NOA.PAYABLE", "eci/tax_payable (unparseable)")

    scheme = _noa_scheme(_noa_year(ctx))
    computed = _compute_noa(eci, scheme)

    findings = []
    lines = _rows(ctx.fields.get("tax_computation"))
    line_by_label = {str(r.get("line", "")).lower(): _as_number(r.get("amount")) for r in lines}

    def _find_line(*keywords: str) -> float | None:
        for label, amount in line_by_label.items():
            if all(k in label for k in keywords):
                return amount
        return None

    stated_after = _find_line("17") or _find_line("chargeable", "after")
    if stated_after is not None and abs(stated_after - computed["after"]) > _MONEY_TOLERANCE:
        findings.append(_finding(
            "NOA.CHARGEABLE_AFTER_EXEMPTION", "impossible",
            f"Chargeable income after exemption should be {_money(computed['after'])} for ECI "
            f"{_money(eci)}, but the computation shows {_money(stated_after)}",
            field="tax_computation", stated=_money(stated_after), expected=_money(computed["after"]),
        ))

    stated_tax = _find_line("tax") or _find_line("17.00%") or _find_line("17%")
    if stated_tax is not None and abs(stated_tax - computed["tax"]) > _MONEY_TOLERANCE:
        findings.append(_finding(
            "NOA.TAX_AT_RATE", "impossible",
            f"Tax at {scheme['tax_rate_pct']:.0%} should be {_money(computed['tax'])}, but the "
            f"computation shows {_money(stated_tax)}",
            field="tax_computation", stated=_money(stated_tax), expected=_money(computed["tax"]),
        ))

    stated_rebate = _find_line("rebate")
    if stated_rebate is not None and abs(abs(stated_rebate) - computed["rebate"]) > _MONEY_TOLERANCE:
        findings.append(_finding(
            "NOA.REBATE", "impossible",
            f"Tax rebate should be {_money(computed['rebate'])} (capped at "
            f"{_money(scheme['rebate_cap'])}), but the computation shows {_money(stated_rebate)}",
            field="tax_computation", stated=_money(stated_rebate), expected=_money(computed["rebate"]),
        ))

    if abs(stated_payable - computed["payable"]) > _MONEY_TOLERANCE:
        findings.append(_finding(
            "NOA.PAYABLE", "impossible",
            f"Tax payable should be {_money(computed['payable'])} for ECI {_money(eci)} under "
            f"the current partial tax exemption scheme, but the notice states "
            f"{_money(stated_payable)}",
            field="tax_payable", stated=_money(stated_payable), expected=_money(computed["payable"]),
        ))
    return findings, None


NOTICE_OF_ASSESSMENT_CHECKS: list[CheckFn] = [_check_noa_chain]


# ---------------------------------------------------------------------------
# Payslip
# ---------------------------------------------------------------------------


def _check_payslip_sums(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """PAY.EARNINGS_SUM / PAY.DEDUCTIONS_SUM / PAY.NET, run together since
    they share the same earnings/deductions line items."""
    values = _need(ctx, "PAY.NET", "net_pay")
    if values is None:
        return [], _skip_missing("PAY.NET", "net_pay")
    net_pay = _as_number(values[0])
    if net_pay is None:
        return [], _skip_missing("PAY.NET", "net_pay (unparseable)")

    earnings_items = ctx.fields.get("earnings_items")
    deductions_items = ctx.fields.get("deductions_items")
    if earnings_items is None and deductions_items is None:
        return [], _skip_missing("PAY.NET", "earnings_items", "deductions_items")

    findings = []
    total_earnings = _as_number(ctx.fields.get("total_earnings"))
    total_deductions = _as_number(ctx.fields.get("total_deductions"))

    if earnings_items is not None and total_earnings is not None:
        amounts = [_as_number(r.get("amount")) for r in _rows(earnings_items)]
        if amounts and all(a is not None for a in amounts):
            total = sum(a for a in amounts if a is not None)
            if abs(total - total_earnings) > _MONEY_TOLERANCE:
                findings.append(_finding(
                    "PAY.EARNINGS_SUM", "impossible",
                    f"Earnings lines sum to {_money(total)}, but total earnings shows "
                    f"{_money(total_earnings)}",
                    field="total_earnings", stated=_money(total_earnings), expected=_money(total),
                ))

    if deductions_items is not None and total_deductions is not None:
        amounts = [_as_number(r.get("amount")) for r in _rows(deductions_items)]
        if amounts and all(a is not None for a in amounts):
            total = sum(a for a in amounts if a is not None)
            if abs(total - total_deductions) > _MONEY_TOLERANCE:
                findings.append(_finding(
                    "PAY.DEDUCTIONS_SUM", "impossible",
                    f"Deduction lines sum to {_money(total)}, but total deductions shows "
                    f"{_money(total_deductions)}",
                    field="total_deductions", stated=_money(total_deductions), expected=_money(total),
                ))

    if total_earnings is not None and total_deductions is not None:
        expected_net = total_earnings - total_deductions
        if abs(expected_net - net_pay) > _MONEY_TOLERANCE:
            findings.append(_finding(
                "PAY.NET", "impossible",
                f"Total earnings {_money(total_earnings)} - total deductions "
                f"{_money(total_deductions)} = {_money(expected_net)}, but net pay shows "
                f"{_money(net_pay)}",
                field="net_pay", stated=_money(net_pay), expected=_money(expected_net),
            ))
    return findings, None


def _check_cpf_ceiling(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """PAY.CPF_CEILING. Employee CPF contribution must not exceed 20% of
    the smaller of ordinary wages and the ordinary wage ceiling for the
    pay year."""
    values = _need(ctx, "PAY.CPF_CEILING", "employee_cpf", "ordinary_wages", "pay_date")
    if values is None:
        return [], _skip_missing("PAY.CPF_CEILING", "employee_cpf", "ordinary_wages", "pay_date")
    cpf_raw, wages_raw, pay_date_raw = values
    pay_date_val = _parse_field_date(pay_date_raw)
    year = pay_date_val.year if pay_date_val else None
    ceiling = REFERENCE_DATA["cpf_ordinary_wage_ceiling"].get(year) if year else None
    if ceiling is None:
        return [], _skip_reference("PAY.CPF_CEILING", f"cpf_ordinary_wage_ceiling[{year!r}]")
    cpf, wages = _as_number(cpf_raw), _as_number(wages_raw)
    if cpf is None or wages is None:
        return [], _skip_missing("PAY.CPF_CEILING", "employee_cpf/ordinary_wages (unparseable)")
    max_contribution = 0.20 * min(wages, ceiling)
    if cpf > max_contribution + _MONEY_TOLERANCE:
        return [_finding(
            "PAY.CPF_CEILING", "impossible",
            f"Employee CPF contribution {_money(cpf)} exceeds 20% of the lower of ordinary "
            f"wages and the ceiling ({_money(max_contribution)})",
            field="employee_cpf", stated=_money(cpf), expected=f"<= {_money(max_contribution)}",
        )], None
    return [], None


def _check_cpf_base(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """PAY.CPF_BASE. The contribution should be calculated on ordinary
    wages (basic pay + allowances + overtime), not basic pay alone."""
    values = _need(ctx, "PAY.CPF_BASE", "employee_cpf", "basic_pay", "ordinary_wages")
    if values is None:
        return [], _skip_missing("PAY.CPF_BASE", "employee_cpf", "basic_pay", "ordinary_wages")
    cpf, basic, ordinary = (_as_number(v) for v in values)
    if cpf is None or basic is None or ordinary is None or basic <= 0 or ordinary <= basic:
        return [], _skip_missing("PAY.CPF_BASE", "employee_cpf/basic_pay/ordinary_wages (unparseable)")
    rate_on_basic = cpf / basic
    rate_on_ordinary = cpf / ordinary
    # A contribution consistent with basic pay alone but not with the wider
    # ordinary-wage base is the inconsistency this check targets.
    if abs(rate_on_basic - 0.20) < 0.01 and abs(rate_on_ordinary - 0.20) > 0.02:
        return [_finding(
            "PAY.CPF_BASE", "inconsistent",
            f"CPF appears calculated on basic pay ({_money(basic)}) rather than the wider "
            f"ordinary wages base ({_money(ordinary)})",
            field="employee_cpf", stated=_money(cpf),
        )], None
    return [], None


PAYSLIP_CHECKS: list[CheckFn] = [_check_payslip_sums, _check_cpf_ceiling, _check_cpf_base]


# ---------------------------------------------------------------------------
# Credit report
# ---------------------------------------------------------------------------


def _check_cr_summary_counts(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """CR.SUMMARY_COUNTS. Summary counts of accounts/enquiries/defaults
    must match the number of rows listed."""
    findings = []
    ran_any = False
    for label, row_field, count_field in (
        ("accounts", "accounts", "account_count"),
        ("enquiries", "previous_enquiries", "enquiry_count"),
        ("defaults", "defaults", "default_count"),
    ):
        rows, stated_raw = ctx.fields.get(row_field), ctx.fields.get(count_field)
        if rows is None or stated_raw is None:
            continue
        ran_any = True
        stated = _as_number(stated_raw)
        actual = len(_rows(rows))
        if stated is not None and abs(stated - actual) > 0.5:
            findings.append(_finding(
                "CR.SUMMARY_COUNTS", "inconsistent",
                f"Summary states {stated:.0f} {label}, but {actual} row(s) are listed",
                field=count_field, stated=f"{stated:.0f}", expected=str(actual),
            ))
    if not ran_any:
        return [], _skip_missing("CR.SUMMARY_COUNTS", "accounts/account_count (and similar)")
    return findings, None


_DEFAULT_STATUS_RE = re.compile(r"overdue|write.?off|restructur", re.IGNORECASE)


def _check_cr_default_consistency(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """CR.DEFAULT_CONSISTENCY. A default count of zero is inconsistent
    with an account showing an overdue/write-off/restructure status."""
    values = _need(ctx, "CR.DEFAULT_CONSISTENCY", "accounts")
    if values is None:
        return [], _skip_missing("CR.DEFAULT_CONSISTENCY", "accounts")
    default_count = _as_number(ctx.fields.get("default_count"))
    defaults_rows = _rows(ctx.fields.get("defaults"))
    if default_count is None and not defaults_rows:
        return [], _skip_missing("CR.DEFAULT_CONSISTENCY", "default_count/defaults")
    zero_defaults = (default_count == 0 if default_count is not None else len(defaults_rows) == 0)
    if not zero_defaults:
        return [], None
    concerning = [
        r for r in _rows(values[0])
        if _DEFAULT_STATUS_RE.search(str(r.get("status") or ""))
    ]
    if concerning:
        return [_finding(
            "CR.DEFAULT_CONSISTENCY", "inconsistent",
            f"Default count is zero, but {len(concerning)} account(s) show an overdue/"
            "write-off/restructure status",
            field="default_count",
        )], None
    return [], None


def _check_cr_limit_vs_balance(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """CR.LIMIT_VS_BALANCE. Secured/unsecured balances are compared with
    their stated limits."""
    values = _need(ctx, "CR.LIMIT_VS_BALANCE", "accounts")
    if values is None:
        return [], _skip_missing("CR.LIMIT_VS_BALANCE", "accounts")
    findings = []
    for i, row in enumerate(_rows(values[0])):
        balance, limit = _as_number(row.get("current_balance")), _as_number(row.get("credit_limit"))
        if balance is not None and limit is not None and balance > limit + _MONEY_TOLERANCE:
            findings.append(_finding(
                "CR.LIMIT_VS_BALANCE", "inconsistent",
                f"Account {i + 1}'s balance ({_money(balance)}) exceeds its stated limit "
                f"({_money(limit)})",
                field=f"accounts[{i}].current_balance", stated=_money(balance), expected=f"<= {_money(limit)}",
            ))
    return findings, None


def _check_cr_aggregate_sum(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """CR.AGGREGATE_SUM. Rows for each product must add up to the monthly
    aggregate totals."""
    values = _need(ctx, "CR.AGGREGATE_SUM", "accounts")
    if values is None:
        return [], _skip_missing("CR.AGGREGATE_SUM", "accounts")
    aggregate = _as_number(ctx.fields.get("total_outstanding_balance"))
    if aggregate is None:
        return [], _skip_missing("CR.AGGREGATE_SUM", "total_outstanding_balance")
    balances = [_as_number(r.get("current_balance")) for r in _rows(values[0])]
    if any(b is None for b in balances) or not balances:
        return [], _skip_missing("CR.AGGREGATE_SUM", "accounts[].current_balance (unparseable)")
    total = sum(b for b in balances if b is not None)
    if abs(total - aggregate) > max(_MONEY_TOLERANCE, abs(total) * 0.01):
        return [_finding(
            "CR.AGGREGATE_SUM", "impossible",
            f"Account balances sum to {_money(total)}, but the stated total outstanding "
            f"balance is {_money(aggregate)}",
            field="total_outstanding_balance", stated=_money(aggregate), expected=_money(total),
        )], None
    return [], None


def _check_cr_amortisation(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """CR.AMORTISATION. Month-to-month balance fall must not exceed the
    instalment, and the implied rate must be plausible."""
    values = _need(ctx, "CR.AMORTISATION", "accounts")
    if values is None:
        return [], _skip_missing("CR.AMORTISATION", "accounts")
    findings = []
    for i, row in enumerate(_rows(values[0])):
        history = _rows(row.get("balance_history"))
        instalment = _as_number(row.get("monthly_instalment"))
        if len(history) < 2 or instalment is None:
            continue
        for j in range(1, len(history)):
            prev, curr = _as_number(history[j - 1].get("balance")), _as_number(history[j].get("balance"))
            if prev is None or curr is None:
                continue
            fall = prev - curr
            if fall > instalment + _MONEY_TOLERANCE:
                findings.append(_finding(
                    "CR.AMORTISATION", "impossible",
                    f"Account {i + 1}'s balance fell by {_money(fall)} between two months, "
                    f"more than its own instalment ({_money(instalment)}) can account for",
                    field=f"accounts[{i}].balance_history",
                ))
    return findings, None


def _check_cr_linear_series(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """CR.LINEAR_SERIES. A balance falling by exactly the same amount for
    4+ consecutive months."""
    values = _need(ctx, "CR.LINEAR_SERIES", "accounts")
    if values is None:
        return [], _skip_missing("CR.LINEAR_SERIES", "accounts")
    findings = []
    for i, row in enumerate(_rows(values[0])):
        balances = [_as_number(h.get("balance")) for h in _rows(row.get("balance_history"))]
        balances = [b for b in balances if b is not None]
        if len(balances) < 5:
            continue
        diffs = [round(balances[j] - balances[j + 1], 2) for j in range(len(balances) - 1)]
        run = 1
        for j in range(1, len(diffs)):
            run = run + 1 if diffs[j] == diffs[j - 1] and diffs[j] != 0 else 1
            if run >= 4:
                findings.append(_finding(
                    "CR.LINEAR_SERIES", "implausible",
                    f"Account {i + 1}'s balance falls by exactly {_money(diffs[j])} for "
                    f"{run} consecutive months — an unusually mechanical pattern",
                    field=f"accounts[{i}].balance_history",
                ))
                break
    return findings, None


def _check_cr_tenor(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """CR.TENOR. outstanding_balance / instalment must not exceed a
    maximum plausible term in months."""
    values = _need(ctx, "CR.TENOR", "accounts")
    if values is None:
        return [], _skip_missing("CR.TENOR", "accounts")
    findings = []
    for i, row in enumerate(_rows(values[0])):
        balance, instalment = _as_number(row.get("current_balance")), _as_number(row.get("monthly_instalment"))
        if balance is None or not instalment:
            continue
        term = balance / instalment
        if term > 480:  # 40 years — beyond any facility this pipeline models
            findings.append(_finding(
                "CR.TENOR", "implausible",
                f"Account {i + 1}'s balance implies a {term:.0f}-month term at its stated "
                "instalment — beyond any plausible facility length",
                field=f"accounts[{i}].current_balance",
            ))
    return findings, None


def _check_cr_score(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """CR.SCORE. An actual score must be present, within its risk grade's
    band, and not the template's own placeholder figure."""
    values = _need(ctx, "CR.SCORE", "bureau_score")
    if values is None:
        return [], _skip_missing("CR.SCORE", "bureau_score")
    score_raw = values[0]
    if isinstance(score_raw, str) and "example" in score_raw.lower():
        return [_finding(
            "CR.SCORE", "inconsistent",
            "Bureau score field still contains the template's own \"Example\" placeholder text",
            field="bureau_score", stated=str(score_raw),
        )], None
    score = _as_number(score_raw)
    if score is None:
        return [], _skip_missing("CR.SCORE", "bureau_score (unparseable)")
    if not 1000 <= score <= 2000:
        return [_finding(
            "CR.SCORE", "inconsistent", f"Bureau score {score:.0f} is outside the 1000-2000 range",
            field="bureau_score", stated=f"{score:.0f}",
        )], None
    return [], None


CREDIT_REPORT_CHECKS: list[CheckFn] = [
    _check_cr_summary_counts, _check_cr_default_consistency,
    _check_cr_limit_vs_balance, _check_cr_aggregate_sum, _check_cr_amortisation,
    _check_cr_linear_series, _check_cr_tenor,
    _check_cr_score,
]


# ---------------------------------------------------------------------------
# List of loans
# ---------------------------------------------------------------------------


def _annuity_instalment(principal: float, monthly_rate: float, n_payments: int) -> float:
    if monthly_rate == 0:
        return principal / n_payments
    factor = (1 + monthly_rate) ** n_payments
    return principal * monthly_rate * factor / (factor - 1)


def _check_loan_annuity(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """LOAN.ANNUITY. A stated monthly instalment must match the annuity
    formula for principal, rate and tenure."""
    values = _need(ctx, "LOAN.ANNUITY", "loan_amount", "interest_rate", "loan_tenure", "monthly_instalment")
    if values is None:
        return [], _skip_missing("LOAN.ANNUITY", "loan_amount", "interest_rate", "loan_tenure",
                                  "monthly_instalment")
    principal, rate_pct, tenure, stated = (_as_number(v) for v in values)
    if principal is None or rate_pct is None or tenure is None or stated is None or tenure <= 0:
        return [], _skip_missing("LOAN.ANNUITY", "loan fields (unparseable)")
    monthly_rate = (rate_pct / 100) / 12
    expected = _annuity_instalment(principal, monthly_rate, int(tenure))
    if abs(expected - stated) > max(1.0, expected * 0.02):
        return [_finding(
            "LOAN.ANNUITY", "inconsistent",
            f"Instalment should be about {_money(expected)} for {_money(principal)} at "
            f"{rate_pct:.2f}% over {tenure:.0f} months, but the letter states "
            f"{_money(stated)}",
            field="monthly_instalment", stated=_money(stated), expected=_money(expected),
        )], None
    return [], None


def _check_loan_repayment_count(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """LOAN.REPAYMENT_COUNT. Number of instalments must equal tenure in
    months."""
    values = _need(ctx, "LOAN.REPAYMENT_COUNT", "loan_tenure", "repayment_schedule")
    if values is None:
        return [], _skip_missing("LOAN.REPAYMENT_COUNT", "loan_tenure", "repayment_schedule")
    tenure, schedule = values
    tenure_num = _as_number(tenure)
    count = len(_rows(schedule))
    if tenure_num is None or count == 0:
        return [], _skip_missing("LOAN.REPAYMENT_COUNT", "loan_tenure/repayment_schedule (unparseable)")
    if abs(tenure_num - count) > 0.5:
        return [_finding(
            "LOAN.REPAYMENT_COUNT", "inconsistent",
            f"Stated tenure is {tenure_num:.0f} months, but {count} instalments are listed",
            field="loan_tenure", stated=f"{tenure_num:.0f}", expected=str(count),
        )], None
    return [], None


def _check_loan_fee(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """LOAN.FEE. Stated fee amount must equal fee percentage x principal."""
    values = _need(ctx, "LOAN.FEE", "loan_amount", "fee_percentage", "fee_amount")
    if values is None:
        return [], _skip_missing("LOAN.FEE", "loan_amount", "fee_percentage", "fee_amount")
    principal, fee_pct, stated_fee = (_as_number(v) for v in values)
    if principal is None or fee_pct is None or stated_fee is None:
        return [], _skip_missing("LOAN.FEE", "loan fee fields (unparseable)")
    expected = principal * fee_pct / 100
    if abs(expected - stated_fee) > max(_MONEY_TOLERANCE, expected * 0.01):
        return [_finding(
            "LOAN.FEE", "inconsistent",
            f"{fee_pct:.2f}% of {_money(principal)} is {_money(expected)}, but the stated fee "
            f"is {_money(stated_fee)}",
            field="fee_amount", stated=_money(stated_fee), expected=_money(expected),
        )], None
    return [], None


_LOAN_RATE_BANDS = {"term_loan": (2.0, 10.0), "overdraft": (5.0, 15.0), "trade_facility": (2.0, 12.0)}


def _check_loan_rate_band(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """LOAN.RATE_BAND. Interest rate must fall within a configurable band
    for the facility type."""
    values = _need(ctx, "LOAN.RATE_BAND", "interest_rate", "facility_type")
    if values is None:
        return [], _skip_missing("LOAN.RATE_BAND", "interest_rate", "facility_type")
    rate_raw, facility_type = values
    rate = _as_number(rate_raw)
    band = _LOAN_RATE_BANDS.get(str(facility_type).strip().lower())
    if rate is None or band is None:
        return [], _skip_missing("LOAN.RATE_BAND", "interest_rate/facility_type (unrecognised)")
    lo, hi = band
    if not lo <= rate <= hi:
        return [_finding(
            "LOAN.RATE_BAND", "implausible",
            f"Interest rate {rate:.2f}% is outside the {lo}-{hi}% plausible band for "
            f"{facility_type}",
            field="interest_rate", stated=f"{rate:.2f}%",
        )], None
    return [], None


def _check_loan_scheme_params(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """LOAN.SCHEME_PARAMS."""
    if not REFERENCE_DATA["loan_scheme_params"]:
        return [], _skip_reference("LOAN.SCHEME_PARAMS", "loan_scheme_params")
    values = _need(ctx, "LOAN.SCHEME_PARAMS", "scheme_name")
    if values is None:
        return [], _skip_missing("LOAN.SCHEME_PARAMS", "scheme_name")
    reference = REFERENCE_DATA["loan_scheme_params"].get(str(values[0]))
    if reference is None:
        return [], _skip_reference("LOAN.SCHEME_PARAMS", f"loan_scheme_params[{values[0]!r}]")
    findings = []
    stated_cap = _as_number(ctx.fields.get("loan_amount"))
    if stated_cap is not None and reference.get("amount_cap") and stated_cap > reference["amount_cap"]:
        findings.append(_finding(
            "LOAN.SCHEME_PARAMS", "inconsistent",
            f"Loan amount {_money(stated_cap)} exceeds the scheme's cap "
            f"({_money(reference['amount_cap'])})",
            field="loan_amount", stated=_money(stated_cap), expected=f"<= {_money(reference['amount_cap'])}",
        ))
    return findings, None


LIST_OF_LOANS_CHECKS: list[CheckFn] = [
    _check_loan_annuity, _check_loan_repayment_count, _check_loan_fee,
    _check_loan_rate_band, _check_loan_scheme_params,
]


# ---------------------------------------------------------------------------
# Proof of address
# ---------------------------------------------------------------------------


def _check_poa_usage_match(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """POA.USAGE_MATCH. Stated usage must equal the trend value for the
    billing month."""
    values = _need(ctx, "POA.USAGE_MATCH", "usage_this_period", "consumption_trend")
    if values is None:
        return [], _skip_missing("POA.USAGE_MATCH", "usage_this_period", "consumption_trend")
    stated_raw, trend = values
    stated = _as_number(stated_raw)
    trend_rows = _rows(trend)
    if stated is None or not trend_rows:
        return [], _skip_missing("POA.USAGE_MATCH", "usage_this_period/consumption_trend (unparseable)")
    latest = trend_rows[-1]
    trend_value = _as_number(latest.get("usage"))
    if trend_value is not None and abs(stated - trend_value) > max(0.5, stated * 0.02):
        return [_finding(
            "POA.USAGE_MATCH", "inconsistent",
            f"Stated usage ({stated:g}) does not match the latest consumption trend point "
            f"({trend_value:g})",
            field="usage_this_period", stated=f"{stated:g}", expected=f"{trend_value:g}",
        )], None
    return [], None


def _check_poa_balance_arithmetic(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """POA.BALANCE_ARITHMETIC. previous - payment = outstanding; outstanding
    + current charges = total payable."""
    findings = []
    ran_any = False
    v1 = _need(ctx, "POA.BALANCE_ARITHMETIC", "previous_balance", "payment_received", "outstanding_balance")
    if v1 is not None:
        ran_any = True
        previous, payment, outstanding = (_as_number(v) for v in v1)
        if previous is not None and payment is not None and outstanding is not None:
            expected = previous - payment
            if abs(expected - outstanding) > _MONEY_TOLERANCE:
                findings.append(_finding(
                    "POA.BALANCE_ARITHMETIC", "impossible",
                    f"Previous balance {_money(previous)} - payment {_money(payment)} = "
                    f"{_money(expected)}, but outstanding balance shows {_money(outstanding)}",
                    field="outstanding_balance", stated=_money(outstanding), expected=_money(expected),
                ))
    v2 = _need(ctx, "POA.BALANCE_ARITHMETIC", "outstanding_balance", "current_charges", "total_payable")
    if v2 is not None:
        ran_any = True
        outstanding, current, total = (_as_number(v) for v in v2)
        if outstanding is not None and current is not None and total is not None:
            expected = outstanding + current
            if abs(expected - total) > _MONEY_TOLERANCE:
                findings.append(_finding(
                    "POA.BALANCE_ARITHMETIC", "impossible",
                    f"Outstanding balance {_money(outstanding)} + current charges "
                    f"{_money(current)} = {_money(expected)}, but total payable shows "
                    f"{_money(total)}",
                    field="total_payable", stated=_money(total), expected=_money(expected),
                ))
    if not ran_any:
        return [], _skip_missing("POA.BALANCE_ARITHMETIC", "previous_balance/outstanding_balance/total_payable")
    return findings, None


def _check_poa_unit_rate(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """POA.UNIT_RATE. amount / usage must fall within a plausible tariff
    band."""
    values = _need(ctx, "POA.UNIT_RATE", "current_charges", "usage_this_period")
    if values is None:
        return [], _skip_missing("POA.UNIT_RATE", "current_charges", "usage_this_period")
    amount, usage = (_as_number(v) for v in values)
    if amount is None or not usage:
        return [], _skip_missing("POA.UNIT_RATE", "current_charges/usage_this_period (unparseable)")
    rate = amount / usage
    if not 0.05 <= rate <= 3.0:
        return [_finding(
            "POA.UNIT_RATE", "implausible",
            f"Implied unit rate ({rate:.3f}/unit) is outside a plausible utility tariff band",
            field="current_charges",
        )], None
    return [], None


PROOF_OF_ADDRESS_CHECKS: list[CheckFn] = [
    _check_poa_usage_match, _check_poa_balance_arithmetic, _check_poa_unit_rate,
]


# ---------------------------------------------------------------------------
# Certificate of incorporation
# ---------------------------------------------------------------------------


# Certificate of incorporation is entirely date-logic (event-after-issue,
# name-change-after-incorporation, UEN year-prefix-vs-incorporation-year) —
# every check moved to date_anomalies.py's CERTIFICATE_OF_INCORPORATION_DATE_CHECKS.
CERTIFICATE_OF_INCORPORATION_CHECKS: list[CheckFn] = []


# ---------------------------------------------------------------------------
# Certificate of incumbency (ACRA business profile)
# ---------------------------------------------------------------------------


def _check_acra_share_sum(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """ACRA.SHARE_SUM. Shareholders' shares must add up to issued
    shares."""
    values = _need(ctx, "ACRA.SHARE_SUM", "shareholders", "issued_shares")
    if values is None:
        return [], _skip_missing("ACRA.SHARE_SUM", "shareholders", "issued_shares")
    rows, issued_raw = values
    issued = _as_number(issued_raw)
    shares = [_as_number(r.get("shares")) for r in _rows(rows)]
    if issued is None or not shares or any(s is None for s in shares):
        return [], _skip_missing("ACRA.SHARE_SUM", "shareholders[].shares (unparseable)")
    total = sum(s for s in shares if s is not None)
    if abs(total - issued) > _MONEY_TOLERANCE:
        return [_finding(
            "ACRA.SHARE_SUM", "impossible",
            f"Shareholders' shares sum to {total:,.0f}, but issued shares states "
            f"{issued:,.0f}",
            field="issued_shares", stated=f"{issued:,.0f}", expected=f"{total:,.0f}",
        )], None
    return [], None


def _check_capital_per_share(code: str, ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    values = _need(ctx, code, "share_capital", "issued_shares")
    if values is None:
        return [], _skip_missing(code, "share_capital", "issued_shares")
    capital, shares = (_as_number(v) for v in values)
    if capital is None or not shares:
        return [], _skip_missing(code, "share_capital/issued_shares (unparseable)")
    per_share = capital / shares
    if not 0.01 <= per_share <= 1_000_000:
        return [_finding(
            code, "inconsistent",
            f"Share capital {_money(capital)} / {shares:,.0f} shares implies "
            f"{per_share:,.4f} per share, which is not internally consistent with a par-value "
            "or no-par-value structure",
            field="share_capital", stated=_money(capital),
        )], None
    return [], None


def _check_acra_capital_per_share(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """ACRA.CAPITAL_PER_SHARE."""
    return _check_capital_per_share("ACRA.CAPITAL_PER_SHARE", ctx)


def _check_acra_officers(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """ACRA.OFFICERS. At least one director and one secretary must be
    listed among the officers. The date-logic half of the original combined
    check (secretary appointed within 6 months of incorporation) moved to
    date_anomalies.py's ACRA.SECRETARY_TIMING; officer-appointment-vs-
    incorporation and AGM-filing-vs-financial-year-end date checks moved
    there too as ACRA.DATE_LOGIC / ACRA.FILING_VS_FYE."""
    values = _need(ctx, "ACRA.OFFICERS", "officers")
    if values is None:
        return [], _skip_missing("ACRA.OFFICERS", "officers")
    officers = _rows(values[0])
    findings = []
    if not any("director" in str(o.get("role", "")).lower() for o in officers):
        findings.append(_finding("ACRA.OFFICERS", "inconsistent", "No director is listed among the officers",
                                  field="officers"))
    if not any("secretary" in str(o.get("role", "")).lower() for o in officers):
        findings.append(_finding("ACRA.OFFICERS", "inconsistent", "No secretary is listed among the officers",
                                  field="officers"))
    return findings, None


CERTIFICATE_OF_INCUMBENCY_CHECKS: list[CheckFn] = [
    _check_acra_share_sum, _check_acra_capital_per_share, _check_acra_officers,
]


# ---------------------------------------------------------------------------
# Company constitution
# ---------------------------------------------------------------------------


def _check_const_subscriber_sum(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """CONST.SUBSCRIBER_SUM. Subscribers' shares must add up to issued
    shares. Only run when every subscriber has a share count — a partial
    list would always fail for an innocent reason."""
    values = _need(ctx, "CONST.SUBSCRIBER_SUM", "subscribers", "issued_shares")
    if values is None:
        return [], _skip_missing("CONST.SUBSCRIBER_SUM", "subscribers", "issued_shares")
    rows, issued_raw = values
    issued = _as_number(issued_raw)
    subscriber_rows = _rows(rows)
    shares = [_as_number(r.get("shares")) for r in subscriber_rows]
    if issued is None or not subscriber_rows or any(s is None for s in shares):
        return [], _skip_missing("CONST.SUBSCRIBER_SUM", "subscribers[].shares (incomplete)")
    total = sum(s for s in shares if s is not None)
    if abs(total - issued) > _MONEY_TOLERANCE:
        return [_finding(
            "CONST.SUBSCRIBER_SUM", "impossible",
            f"Subscriber shareholdings sum to {total:,.0f}, but the constitution states "
            f"{issued:,.0f} issued shares",
            field="issued_shares", stated=f"{issued:,.0f}", expected=f"{total:,.0f}",
        )], None
    return [], None


def _check_const_capital_per_share(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """CONST.CAPITAL_PER_SHARE."""
    return _check_capital_per_share("CONST.CAPITAL_PER_SHARE", ctx)


def _check_const_quorum(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """CONST.QUORUM. Quorum for general meetings must not exceed the
    number of subscribers."""
    values = _need(ctx, "CONST.QUORUM", "subscribers", "quorum")
    if values is None:
        return [], _skip_missing("CONST.QUORUM", "subscribers", "quorum")
    rows, quorum_raw = values
    quorum = _as_number(quorum_raw)
    count = len(_rows(rows))
    if quorum is None or count == 0:
        return [], _skip_missing("CONST.QUORUM", "quorum (unparseable)")
    if quorum > count:
        return [_finding(
            "CONST.QUORUM", "inconsistent",
            f"Quorum ({quorum:.0f}) is larger than the number of subscribers ({count})",
            field="quorum", stated=f"{quorum:.0f}", expected=f"<= {count}",
        )], None
    return [], None


COMPANY_CONSTITUTION_CHECKS: list[CheckFn] = [
    _check_const_subscriber_sum, _check_const_capital_per_share, _check_const_quorum,
]


# ---------------------------------------------------------------------------
# NRIC (document-specific date logic beyond the universal ID.* checks)
# ---------------------------------------------------------------------------


# NRIC's only document-specific check was date logic, moved to
# date_anomalies.py's NRIC_DATE_CHECKS. The universal ID.NRIC_CHECKSUM/
# ID.UEN_FORMAT checks (in GENERAL_CHECKS) still run against nric documents.
NRIC_CHECKS: list[CheckFn] = []


# ---------------------------------------------------------------------------
# Passport / MRZ
# ---------------------------------------------------------------------------


def _mrz_char_value(c: str) -> int:
    if c.isdigit():
        return int(c)
    if c == "<":
        return 0
    if c.isalpha():
        return ord(c.upper()) - ord("A") + 10
    return 0


def _mrz_check_digit(s: str) -> int:
    """ICAO 9303 TD3 check digit: weights 7,3,1 repeating."""
    weights = (7, 3, 1)
    return sum(_mrz_char_value(c) * weights[i % 3] for i, c in enumerate(s)) % 10


def _check_mrz_format(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """ID.MRZ_FORMAT. TD3 machine-readable zone is two lines of 44
    characters each."""
    values = _need(ctx, "ID.MRZ_FORMAT", "mrz")
    if values is None:
        return [], _skip_missing("ID.MRZ_FORMAT", "mrz")
    raw = values[0]
    lines = [ln for ln in str(raw).strip().splitlines() if ln.strip()]
    if len(lines) != 2 or any(len(ln) != 44 for ln in lines):
        shapes = ", ".join(f"{len(ln)} chars" for ln in lines) or "no lines"
        return [_finding(
            "ID.MRZ_FORMAT", "impossible",
            f"MRZ must be two lines of 44 characters each; got {len(lines)} line(s) ({shapes})",
            field="mrz", stated=str(raw)[:100],
        )], None
    return [], None


def _check_mrz_checkdigits(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """ID.MRZ_CHECKDIGITS. All five TD3 check digits must be valid."""
    values = _need(ctx, "ID.MRZ_CHECKDIGITS", "mrz")
    if values is None:
        return [], _skip_missing("ID.MRZ_CHECKDIGITS", "mrz")
    lines = [ln for ln in str(values[0]).strip().splitlines() if ln.strip()]
    if len(lines) != 2 or any(len(ln) != 44 for ln in lines):
        return [], _skip_missing("ID.MRZ_CHECKDIGITS", "mrz (wrong format, see ID.MRZ_FORMAT)")
    line2 = lines[1]

    fields = (
        ("passport number", line2[0:9], line2[9]),
        ("date of birth", line2[13:19], line2[19]),
        ("expiry date", line2[21:27], line2[27]),
        ("personal number", line2[28:42], line2[42]),
    )
    findings = []
    for label, value, check in fields:
        if value.replace("<", "") == "" and label == "personal number":
            continue  # optional field, all-filler is valid with check digit 0 too
        expected = _mrz_check_digit(value)
        if str(expected) != check:
            findings.append(_finding(
                "ID.MRZ_CHECKDIGITS", "impossible",
                f"MRZ check digit for {label} is invalid (expected {expected}, got {check})",
                field="mrz",
            ))

    composite_input = line2[0:10] + line2[13:20] + line2[21:43]
    composite_expected = _mrz_check_digit(composite_input)
    if str(composite_expected) != line2[43]:
        findings.append(_finding(
            "ID.MRZ_CHECKDIGITS", "impossible",
            f"MRZ composite check digit is invalid (expected {composite_expected}, got "
            f"{line2[43]})",
            field="mrz",
        ))
    return findings, None


def _mrz_field(line2: str, start: int, end: int) -> str:
    return line2[start:end].replace("<", "")


def _mrz_date(yymmdd: str) -> date | None:
    if len(yymmdd) != 6 or not yymmdd.isdigit():
        return None
    yy, mm, dd = int(yymmdd[0:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    # TD3 has no century marker; passports are valid ~10 years, so treat any
    # two-digit year as the interpretation nearest to today.
    for century in (1900, 2000):
        try:
            candidate = date(century + yy, mm, dd)
        except ValueError:
            continue
        if abs((candidate - date.today()).days) < 366 * 60:
            return candidate
    return None


def _check_mrz_vs_viz(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """ID.MRZ_VS_VIZ. MRZ contents must match the printed (visual) fields:
    name, passport number, date of birth, sex, expiry date, national ID."""
    values = _need(ctx, "ID.MRZ_VS_VIZ", "mrz")
    if values is None:
        return [], _skip_missing("ID.MRZ_VS_VIZ", "mrz")
    lines = [ln for ln in str(values[0]).strip().splitlines() if ln.strip()]
    if len(lines) != 2 or any(len(ln) != 44 for ln in lines):
        return [], _skip_missing("ID.MRZ_VS_VIZ", "mrz (wrong format, see ID.MRZ_FORMAT)")
    line2 = lines[1]

    findings = []
    mrz_passport_no = _mrz_field(line2, 0, 9)
    stated_passport_no = str(ctx.fields.get("passport_number") or "").replace(" ", "").upper()
    if stated_passport_no and mrz_passport_no and mrz_passport_no != stated_passport_no:
        findings.append(_finding(
            "ID.MRZ_VS_VIZ", "inconsistent",
            f"MRZ passport number ({mrz_passport_no}) does not match the printed passport "
            f"number ({stated_passport_no})",
            field="passport_number", stated=stated_passport_no, expected=mrz_passport_no,
        ))

    mrz_dob = _mrz_date(line2[13:19])
    stated_dob = _parse_field_date(ctx.fields.get("date_of_birth"))
    if mrz_dob and stated_dob and mrz_dob != stated_dob:
        findings.append(_finding(
            "ID.MRZ_VS_VIZ", "inconsistent",
            f"MRZ date of birth ({mrz_dob.isoformat()}) does not match the printed date of "
            f"birth ({stated_dob.isoformat()})",
            field="date_of_birth", stated=stated_dob.isoformat(), expected=mrz_dob.isoformat(),
        ))

    mrz_expiry = _mrz_date(line2[21:27])
    stated_expiry = _parse_field_date(ctx.fields.get("date_of_expiry"))
    if mrz_expiry and stated_expiry and mrz_expiry != stated_expiry:
        findings.append(_finding(
            "ID.MRZ_VS_VIZ", "inconsistent",
            f"MRZ expiry date ({mrz_expiry.isoformat()}) does not match the printed expiry "
            f"date ({stated_expiry.isoformat()})",
            field="date_of_expiry", stated=stated_expiry.isoformat(), expected=mrz_expiry.isoformat(),
        ))

    mrz_personal_no = _mrz_field(line2, 28, 42)
    stated_nric = str(ctx.fields.get("nric_number") or "").strip().upper()
    if stated_nric and mrz_personal_no and mrz_personal_no != stated_nric:
        findings.append(_finding(
            "ID.MRZ_VS_VIZ", "inconsistent",
            f"MRZ personal number ({mrz_personal_no}) does not match the printed national ID "
            f"number ({stated_nric})",
            field="nric_number", stated=stated_nric, expected=mrz_personal_no,
        ))
    return findings, None


PASSPORT_CHECKS: list[CheckFn] = [
    _check_mrz_format, _check_mrz_checkdigits, _check_mrz_vs_viz,
]


# ---------------------------------------------------------------------------
# FATCA/CRS declaration
# ---------------------------------------------------------------------------


def _check_fatca_tin_or_reason(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """FATCA.TIN_OR_REASON. Each tax residence row must have either a TIN
    or exactly one reason code for not having one."""
    values = _need(ctx, "FATCA.TIN_OR_REASON", "tax_residences")
    if values is None:
        return [], _skip_missing("FATCA.TIN_OR_REASON", "tax_residences")
    findings = []
    for i, row in enumerate(_rows(values[0])):
        tin = row.get("tin")
        reasons = row.get("no_tin_reason")
        has_tin = bool(tin and str(tin).strip())
        reason_count = len(reasons) if isinstance(reasons, list) else (1 if reasons else 0)
        if has_tin and reason_count:
            findings.append(_finding(
                "FATCA.TIN_OR_REASON", "inconsistent",
                f"Tax residence {i + 1} has both a TIN and a no-TIN reason code",
                field=f"tax_residences[{i}]",
            ))
        elif not has_tin and reason_count != 1:
            findings.append(_finding(
                "FATCA.TIN_OR_REASON", "inconsistent",
                f"Tax residence {i + 1} has no TIN and {reason_count} reason code(s) — exactly "
                "one is required",
                field=f"tax_residences[{i}]",
            ))
    return findings, None


def _check_fatca_tin_format(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """FATCA.TIN_FORMAT. A Singapore TIN must be valid UEN format."""
    values = _need(ctx, "FATCA.TIN_FORMAT", "tax_residences")
    if values is None:
        return [], _skip_missing("FATCA.TIN_FORMAT", "tax_residences")
    findings = []
    for i, row in enumerate(_rows(values[0])):
        if str(row.get("country", "")).strip().lower() not in ("singapore", "sg"):
            continue
        tin = row.get("tin")
        if tin and not (_UEN_RE.match(str(tin).upper()) or _UEN_BUSINESS_RE.match(str(tin).upper())):
            findings.append(_finding(
                "FATCA.TIN_FORMAT", "inconsistent",
                f"Tax residence {i + 1}'s Singapore TIN ({tin}) is not a valid UEN format",
                field=f"tax_residences[{i}].tin", stated=str(tin),
            ))
    return findings, None


def _check_fatca_entity_type(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """FATCA.ENTITY_TYPE. Exactly one entity type selected; a passive/
    investment type (D or M) requires the controlling persons section."""
    values = _need(ctx, "FATCA.ENTITY_TYPE", "entity_type")
    if values is None:
        return [], _skip_missing("FATCA.ENTITY_TYPE", "entity_type")
    entity_type = values[0]
    types = entity_type if isinstance(entity_type, list) else [entity_type]
    findings = []
    if len(types) != 1:
        findings.append(_finding(
            "FATCA.ENTITY_TYPE", "inconsistent",
            f"{len(types)} entity type(s) selected; exactly one is required",
            field="entity_type",
        ))
    elif str(types[0]).strip().upper() in ("D", "M"):
        controlling = _rows(ctx.fields.get("controlling_persons"))
        if not controlling:
            findings.append(_finding(
                "FATCA.ENTITY_TYPE", "inconsistent",
                f"Entity type {types[0]} requires the controlling persons section, which is "
                "empty",
                field="controlling_persons",
            ))
    return findings, None


def _check_fatca_us_person(ctx: _Ctx) -> tuple[list[NumericalFinding], SkippedCheck | None]:
    """FATCA.US_PERSON. "No" must come with no US TIN; "Yes" with one."""
    values = _need(ctx, "FATCA.US_PERSON", "specified_us_person")
    if values is None:
        return [], _skip_missing("FATCA.US_PERSON", "specified_us_person")
    answer = str(values[0]).strip().lower()
    us_tin = ctx.fields.get("us_tin")
    has_tin = bool(us_tin and str(us_tin).strip())
    if answer in ("no", "n") and has_tin:
        return [_finding(
            "FATCA.US_PERSON", "inconsistent",
            "Specified US Person is answered No, but a US TIN is provided",
            field="us_tin",
        )], None
    if answer in ("yes", "y") and not has_tin:
        return [_finding(
            "FATCA.US_PERSON", "inconsistent",
            "Specified US Person is answered Yes, but no US TIN is provided",
            field="us_tin",
        )], None
    return [], None


FATCA_CRS_CHECKS: list[CheckFn] = [
    _check_fatca_tin_or_reason, _check_fatca_tin_format, _check_fatca_entity_type,
    _check_fatca_us_person,
]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

DOCUMENT_TYPE_CHECKS: dict[str, list[CheckFn]] = {
    "bank_statement": BANK_STATEMENT_CHECKS,
    "source_of_wealth": SOURCE_OF_WEALTH_CHECKS,
    "notice_of_assessment": NOTICE_OF_ASSESSMENT_CHECKS,
    "payslip": PAYSLIP_CHECKS,
    "credit_report": CREDIT_REPORT_CHECKS,
    "list_of_loans": LIST_OF_LOANS_CHECKS,
    "proof_of_address": PROOF_OF_ADDRESS_CHECKS,
    "certificate_of_incorporation": CERTIFICATE_OF_INCORPORATION_CHECKS,
    "certificate_of_incumbency": CERTIFICATE_OF_INCUMBENCY_CHECKS,
    "company_constitution": COMPANY_CONSTITUTION_CHECKS,
    "nric": NRIC_CHECKS,
    "passport": PASSPORT_CHECKS,
    "fatca_crs_declaration": FATCA_CRS_CHECKS,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def check_numerical_validation(
    document_id: str,
    extracted_fields: list[ExtractedField] | None = None,
    document_type: str | None = None,
) -> NumericalValidationResult:
    """
    Runs every GEN.*/ID.* check (every document type) plus every check
    registered for `document_type` (see DOCUMENT_TYPE_CHECKS). A check
    whose required field(s) weren't extracted is recorded in
    `skipped_checks`, never silently dropped. AC7: Unable to Assess only
    when no numerical field could be read at all.

    Works from extracted_fields alone — no file download or re-ingestion.
    extracted_fields is trusted as already human-reviewed by the time this
    runs, so there's no need to re-derive or corroborate anything from the
    document's own raw text (see _apply_needs_review).
    """
    fields = _fields_by_name(extracted_fields)
    confidences = _confidence_by_name(extracted_fields)
    ctx = _Ctx(fields=fields, document_type=document_type, today=date.today())

    checks = list(GENERAL_CHECKS) + list(DOCUMENT_TYPE_CHECKS.get(document_type or "", []))
    findings: list[NumericalFinding] = []
    skipped: list[SkippedCheck] = []
    for check in checks:
        result_findings, skip = check(ctx)
        findings.extend(result_findings)
        if skip is not None:
            skipped.append(skip)

    findings = _apply_needs_review(findings, confidences)

    values_analysed = sum(
        1 for v in fields.values()
        if isinstance(v, (int, float)) or (isinstance(v, str) and _as_number(v) is not None)
    )

    if not findings and len(skipped) == len(checks) and not fields:
        return NumericalValidationResult(
            document_id=document_id, document_type=document_type, values_analysed=0,
            findings=[], skipped_checks=skipped, benford=_benford_unavailable("No fields extracted"),
            unable_to_assess=True,
            unable_to_assess_reason="No numerical values could be extracted from this document",
        )

    benford = benford_excl = None
    if document_type == "bank_statement":
        benford, benford_excl, benford_findings = _run_benford_pair(ctx)
        findings.extend(benford_findings)

    return NumericalValidationResult(
        document_id=document_id,
        document_type=document_type,
        values_analysed=values_analysed,
        findings=findings,
        skipped_checks=skipped,
        benford=benford or _benford_unavailable(
            "Benford's Law only runs on bank statement transactions" if document_type != "bank_statement"
            else "No transactions field extracted"
        ),
        benford_excluding_fees=benford_excl,
        unable_to_assess=False,
    )
