# date_anomalies.py
"""
Content Validation Specialist — "Detect Date Anomalies".

As a loan officer, I want the Content Validation Specialist to identify
inconsistent or suspicious dates within submitted documents so that I can
detect potentially altered or fabricated information.

This module owns every date-focused check split out of
numerical_validation.py (that module's `git log` covers their original
design/verification notes — the check bodies here are unchanged, only
relocated) plus one new capability that module never had: extracting every
identifiable date with a human label (AC1/AC2 below).

AC1: every identifiable date is extracted from supported documents.
AC2: each date is associated with a human label where one is known
     (statement date, transaction date, incorporation date, invoice date,
     reporting period, etc.) — an unmapped field name falls back to a
     de-underscored version of its own name.
AC3: logically inconsistent dates (e.g. an end date before a start date)
     are flagged.
AC4: dates that conflict with the stated reporting period or document date
     are flagged.
AC5 (page/text-location attribution) is not implemented — dropped by
     product decision; findings and extracted dates aren't attributed to a
     page or location.
AC6: the original date and the conflicting/expected date are recorded on
     every finding where applicable (`stated` / `expected`).
AC7: unable_to_assess=True when no date could be reliably extracted at all.

Two forms of "this check didn't produce a verdict", identical to
numerical_validation.py:
* SKIPPED — the field(s) a check needs were not extracted. Recorded in
  `skipped_checks` with a reason, never silently dropped.
* NEEDS_REVIEW — the check ran and something failed, but the failing
  value's field has low extraction confidence, so the failure might be an
  extraction artefact rather than a genuine anomaly.

Works from extracted_fields alone — no file download or re-ingestion.
extracted_fields is trusted as already human-reviewed by the time these
checks run, so there's no raw-text corroboration pass (there used to be
one; it required re-ingesting the file on every check run just to
sanity-check a value a human has already verified — not worth the cost).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Callable

from pydantic import BaseModel

from .font_consistency import ExtractedField
from .numerical_validation.common import officer_appointed, officer_role, _apply_needs_review, _as_number, _confidence_by_name, _fields_by_name, _parse_field_date, _rows, _skip_missing
from .numerical_validation.identity_company import _UEN_RE, _is_forward_looking_date_field, _looks_like_date_field
from .numerical_validation.models import Severity, SkippedCheck
from .numerical_validation.statements import _noa_year


class _Ctx(BaseModel):
    """The fields of one document, as the date checks read them."""

    model_config = {"arbitrary_types_allowed": True}
    fields: dict[str, Any]
    document_type: str | None
    today: date


def _need(ctx: _Ctx, code: str, *names: str) -> list[Any] | None:
    """The values for `names` if every one is present and non-empty, else None."""
    values = []
    for name in names:
        value = ctx.fields.get(name)
        if value is None or value == "" or value == []:
            return None
        values.append(value)
    return values

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class DateFinding(BaseModel):
    check_code: str
    severity: Severity
    field: str | None = None
    stated: str | None = None
    expected: str | None = None
    needs_review: bool = False
    severity_note: str | None = None
    summary: str


class ExtractedDate(BaseModel):
    """AC1/AC2: one date pulled out of the document's extracted fields,
    labelled with a human-readable description."""
    field: str
    label: str
    value: str  # ISO date
    raw_value: str
    confidence: float | None = None


class DateAnomalyResult(BaseModel):
    """
    Response for GET /documents/{id}/detection/date-anomalies. Nothing is
    persisted — a pure read/compute, like the spelling/layout/numerical
    checks.
    """

    document_id: str
    document_type: str | None = None
    dates_extracted: list[ExtractedDate]
    findings: list[DateFinding]
    skipped_checks: list[SkippedCheck]
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None


# ---------------------------------------------------------------------------
# Check registry plumbing (mirrors numerical_validation.py's, but for
# DateFinding)
# ---------------------------------------------------------------------------


def _finding(
    code: str, severity: Severity, summary: str, *,
    field: str | None = None, stated: str | None = None, expected: str | None = None,
    severity_note: str | None = None,
) -> DateFinding:
    return DateFinding(
        check_code=code, severity=severity, summary=summary,
        field=field, stated=stated, expected=expected, severity_note=severity_note,
    )


CheckFn = Callable[[_Ctx], "tuple[list[DateFinding], SkippedCheck | None]"]


# ---------------------------------------------------------------------------
# AC1/AC2/AC5 — labelled date extraction with page/text-location
# ---------------------------------------------------------------------------

# Field name -> human label (AC2's own examples plus every date field this
# pipeline's checks read). An unmapped date-like field falls back to its own
# de-underscored name.
_DATE_FIELD_LABELS: dict[str, str] = {
    "statement_period_start": "statement period start",
    "statement_period_end": "statement period end",
    "incorporation_date": "incorporation date",
    "bill_date": "invoice/bill date",
    "due_date": "payment due date",
    "billing_period_end": "billing period end",
    "contract_end_date": "contract end date",
    "financial_year_end": "reporting period end",
    "signing_date": "financial statements signing date",
    "noa_date": "notice of assessment date",
    "previous_assessment_date": "previous assessment date",
    "pay_date": "pay date",
    "pay_period_end": "pay period end",
    "report_date": "credit report date",
    "earliest_account_date": "earliest account date",
    "declaration_date": "FATCA/CRS declaration date",
    "form_version_date": "form version date",
    "date_of_birth": "date of birth",
    "date_of_issue": "document issue date",
    "date_of_expiry": "document expiry date",
    "offer_date": "loan offer date",
    "acceptance_date": "loan acceptance date",
    "certificate_date": "certificate issue date",
    "company_former_name_change_date": "company name change date",
    "last_agm_date": "last AGM date",
    "last_annual_return_date": "last annual return date",
    "adoption_date": "constitution adoption date",
    "execution_date": "constitution execution date",
    "document_date": "document date",
    "statement_period": "reporting period",
}

# Row-array field -> [(subfield, label)] — dates nested inside a list of
# rows (transactions, accounts, officers, ...), also covered by AC2.
_ROW_DATE_LABELS: dict[str, list[tuple[str, str]]] = {
    "transactions": [("date", "transaction date")],
    "accounts": [("date_opened", "account opening date"), ("date_closed", "account closing date")],
    "officers": [("date_appointed", "officer appointment date")],
    "balance_history": [("date", "balance history date")],
}


def _looks_like_date_field_for_extraction(name: str) -> bool:
    """Broader than numerical_validation._looks_like_date_field: that one
    excludes date_of_birth (specific to GEN.DATE_VALID's not-in-the-future
    rule) and doesn't match period-bound field names like
    statement_period_end/financial_year_end that don't contain the word
    "date" — AC1 wants every identifiable date, all of those included."""
    return bool(re.search(r"date|_at$|_on$|_start$|_end$", name, re.IGNORECASE))


def _humanize_field_name(name: str) -> str:
    return name.replace("_", " ").strip()


def extract_labeled_dates(ctx: _Ctx, confidences: dict[str, float]) -> list[ExtractedDate]:
    """AC1/AC2: every parseable date-like field, top-level or nested in a
    row list, with a human label."""
    extracted: list[ExtractedDate] = []

    for name, value in ctx.fields.items():
        if not isinstance(value, str) or not _looks_like_date_field_for_extraction(name):
            continue
        parsed = _parse_field_date(value)
        if parsed is None:
            continue
        extracted.append(ExtractedDate(
            field=name, label=_DATE_FIELD_LABELS.get(name, _humanize_field_name(name)),
            value=parsed.isoformat(), raw_value=value,
            confidence=confidences.get(name),
        ))

    for row_field, subfields in _ROW_DATE_LABELS.items():
        rows = _rows(ctx.fields.get(row_field))
        for i, row in enumerate(rows):
            for subfield, label in subfields:
                raw = row.get(subfield)
                if raw is None and subfield == "date_appointed":
                    # The column may be transcribed as date_of_appointment, appointment_date, ...
                    raw = officer_appointed(row)
                if not isinstance(raw, str):
                    continue
                parsed = _parse_field_date(raw)
                if parsed is None:
                    continue
                extracted.append(ExtractedDate(
                    field=f"{row_field}[{i}].{subfield}", label=label,
                    value=parsed.isoformat(), raw_value=raw,
                    confidence=confidences.get(row_field),
                ))

    return extracted


def _has_any_date_like_field(ctx: _Ctx) -> bool:
    """Distinguishes "no dates in this document at all" (AC7's Unable to
    Assess) from "a date field is present but failed to parse" — the
    latter is exactly what GEN.DATE_VALID exists to flag as a finding, not
    something to silently wave off as unassessable."""
    for name, value in ctx.fields.items():
        if isinstance(value, str) and _looks_like_date_field_for_extraction(name):
            return True
    for row_field in _ROW_DATE_LABELS:
        if _rows(ctx.fields.get(row_field)):
            return True
    return False


# ---------------------------------------------------------------------------
# GEN.* / ID.* — checks that apply to every document type
# (relocated from numerical_validation.py, unchanged)
# ---------------------------------------------------------------------------


def _check_date_valid(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """GEN.DATE_VALID. Every date-like field must parse. Fields that
    aren't inherently forward-looking (see _is_forward_looking_date_field
    — due dates, expiry dates, review/renewal dates and the like are
    supposed to be in the future) must also not fall after the submission
    date (today)."""
    date_fields = {
        name: value for name, value in ctx.fields.items()
        if isinstance(value, str) and _looks_like_date_field(name)
    }
    if not date_fields:
        return [], _skip_missing("GEN.DATE_VALID", "(no date-like fields)")

    findings = []
    for name, raw in date_fields.items():
        parsed = _parse_field_date(raw)
        if parsed is None:
            findings.append(_finding(
                "GEN.DATE_VALID", "impossible", f"{name} ({raw!r}) does not parse as a date",
                field=name, stated=str(raw),
            ))
        elif parsed > ctx.today and not _is_forward_looking_date_field(name):
            findings.append(_finding(
                "GEN.DATE_VALID", "impossible",
                f"{name} ({parsed.isoformat()}) is after today's date "
                f"({ctx.today.isoformat()}) — nothing dated later than today can be genuine "
                "yet",
                field=name, stated=parsed.isoformat(), expected=f"on or before {ctx.today.isoformat()}",
            ))
    return findings, None


def _check_nric_birthyear(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """ID.NRIC_BIRTHYEAR. For S/T numbers belonging to people born in 1968
    or later, the two digits after the prefix must match the birth year."""
    values = _need(ctx, "ID.NRIC_BIRTHYEAR", "nric_number", "date_of_birth")
    if values is None:
        return [], _skip_missing("ID.NRIC_BIRTHYEAR", "nric_number", "date_of_birth")
    nric, dob_raw = values
    dob = _parse_field_date(dob_raw)
    if not isinstance(nric, str) or dob is None:
        return [], _skip_missing("ID.NRIC_BIRTHYEAR", "nric_number", "date_of_birth")
    match = _NRIC_RE.match(nric.strip().upper())
    if not match or match.group(1) not in ("S", "T") or dob.year < 1968:
        return [], None
    stated_year_digits = match.group(2)[:2]
    expected_digits = f"{dob.year % 100:02d}"
    if stated_year_digits != expected_digits:
        return [_finding(
            "ID.NRIC_BIRTHYEAR", "inconsistent",
            f"NRIC {nric} implies birth year ..{stated_year_digits}, but date of birth is "
            f"{dob.isoformat()} (..{expected_digits})",
            field="nric_number", stated=nric, expected=f"..{expected_digits}..",
        )], None
    return [], None


def _check_nric_prefix_vs_birth(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """ID.PREFIX_VS_BIRTH. S is for births before 2000, T from 2000 on."""
    values = _need(ctx, "ID.PREFIX_VS_BIRTH", "nric_number", "date_of_birth")
    if values is None:
        return [], _skip_missing("ID.PREFIX_VS_BIRTH", "nric_number", "date_of_birth")
    nric, dob_raw = values
    dob = _parse_field_date(dob_raw)
    if not isinstance(nric, str) or dob is None:
        return [], _skip_missing("ID.PREFIX_VS_BIRTH", "nric_number", "date_of_birth")
    match = _NRIC_RE.match(nric.strip().upper())
    if not match or match.group(1) not in ("S", "T"):
        return [], None
    prefix = match.group(1)
    expected_prefix = "S" if dob.year < 2000 else "T"
    if prefix != expected_prefix:
        return [_finding(
            "ID.PREFIX_VS_BIRTH", "inconsistent",
            f"NRIC prefix {prefix} does not match date of birth {dob.isoformat()} "
            f"(expected {expected_prefix})",
            field="nric_number", stated=prefix, expected=expected_prefix,
        )], None
    return [], None


_NRIC_RE = re.compile(r"\b([STFGM])(\d{7})([A-Z])\b", re.IGNORECASE)


GENERAL_DATE_CHECKS: list[CheckFn] = [
    _check_date_valid,
    _check_nric_birthyear,
    _check_nric_prefix_vs_birth,
]


# ---------------------------------------------------------------------------
# Bank statement
# ---------------------------------------------------------------------------


def _check_period_bounds(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """BS.PERIOD_BOUNDS. Every transaction falls within the statement
    period, in a consistent order."""
    values = _need(ctx, "BS.PERIOD_BOUNDS", "transactions", "statement_period_start", "statement_period_end")
    if values is None:
        return [], _skip_missing(
            "BS.PERIOD_BOUNDS", "transactions", "statement_period_start", "statement_period_end"
        )
    rows, start_raw, end_raw = values
    start, end = _parse_field_date(start_raw), _parse_field_date(end_raw)
    if start is None or end is None:
        return [], _skip_missing("BS.PERIOD_BOUNDS", "statement_period_start/.end")

    findings = []
    dates: list[date] = []
    for i, row in enumerate(_rows(rows)):
        txn_date = _parse_field_date(row.get("date"))
        if txn_date is None:
            continue
        dates.append(txn_date)
        if not (start <= txn_date <= end):
            findings.append(_finding(
                "BS.PERIOD_BOUNDS", "inconsistent",
                f"Transaction {i + 1} is dated {txn_date.isoformat()}, outside the statement "
                f"period {start.isoformat()} to {end.isoformat()}",
                field=f"transactions[{i}].date", stated=txn_date.isoformat(),
                expected=f"{start.isoformat()} .. {end.isoformat()}",
            ))

    if len(dates) >= 3:
        ascending = sum(1 for a, b in zip(dates, dates[1:]) if b >= a)
        descending = sum(1 for a, b in zip(dates, dates[1:]) if b <= a)
        if max(ascending, descending) / (len(dates) - 1) < 0.8:
            findings.append(_finding(
                "BS.PERIOD_BOUNDS", "inconsistent",
                "Transaction dates are not in a consistent ascending or descending order",
                field="transactions",
            ))
    return findings, None


BANK_STATEMENT_DATE_CHECKS: list[CheckFn] = [_check_period_bounds]


# ---------------------------------------------------------------------------
# Source of wealth (financial statements)
# ---------------------------------------------------------------------------


def _check_fs_date_logic(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """FS.DATE_LOGIC. Signing date must fall after the financial year end,
    within a reasonable window."""
    values = _need(ctx, "FS.DATE_LOGIC", "financial_year_end", "signing_date")
    if values is None:
        return [], _skip_missing("FS.DATE_LOGIC", "financial_year_end", "signing_date")
    fye, signing = (_parse_field_date(v) for v in values)
    if fye is None or signing is None:
        return [], _skip_missing("FS.DATE_LOGIC", "financial_year_end/signing_date (unparseable)")
    if signing < fye:
        return [_finding(
            "FS.DATE_LOGIC", "inconsistent",
            f"Signing date {signing.isoformat()} falls before the financial year end "
            f"{fye.isoformat()}",
            field="signing_date", stated=signing.isoformat(),
        )], None
    if (signing - fye).days > 365:
        return [_finding(
            "FS.DATE_LOGIC", "inconsistent",
            f"Signing date {signing.isoformat()} is more than a year after the financial year "
            f"end {fye.isoformat()}",
            field="signing_date", stated=signing.isoformat(),
        )], None
    return [], None


SOURCE_OF_WEALTH_DATE_CHECKS: list[CheckFn] = [_check_fs_date_logic]


# ---------------------------------------------------------------------------
# Notice of assessment
# ---------------------------------------------------------------------------


def _check_noa_date_logic(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """NOA.DATE_LOGIC. Dated in or after its year of assessment, and after
    any previous assessment it refers to."""
    values = _need(ctx, "NOA.DATE_LOGIC", "noa_date", "year_of_assessment")
    if values is None:
        return [], _skip_missing("NOA.DATE_LOGIC", "noa_date", "year_of_assessment")
    noa_date_raw, ya_raw = values
    noa_date_val = _parse_field_date(noa_date_raw)
    year = _noa_year(ctx.fields.get("year_of_assessment"))
    if noa_date_val is None or year is None:
        return [], _skip_missing("NOA.DATE_LOGIC", "noa_date/year_of_assessment (unparseable)")

    findings = []
    if noa_date_val.year < year:
        findings.append(_finding(
            "NOA.DATE_LOGIC", "inconsistent",
            f"Notice is dated {noa_date_val.isoformat()}, before its own year of assessment "
            f"({year})",
            field="noa_date", stated=noa_date_val.isoformat(),
        ))
    previous = _parse_field_date(ctx.fields.get("previous_assessment_date"))
    if previous is not None and noa_date_val < previous:
        findings.append(_finding(
            "NOA.DATE_LOGIC", "inconsistent",
            f"Notice is dated {noa_date_val.isoformat()}, before the previous assessment it "
            f"refers to ({previous.isoformat()})",
            field="noa_date", stated=noa_date_val.isoformat(),
        ))
    return findings, None


NOTICE_OF_ASSESSMENT_DATE_CHECKS: list[CheckFn] = [_check_noa_date_logic]


# ---------------------------------------------------------------------------
# Payslip
# ---------------------------------------------------------------------------


def _check_payslip_date_logic(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """PAY.DATE_LOGIC. Payment date falls within or shortly after the pay
    period."""
    values = _need(ctx, "PAY.DATE_LOGIC", "pay_period_end", "pay_date")
    if values is None:
        return [], _skip_missing("PAY.DATE_LOGIC", "pay_period_end", "pay_date")
    period_end, pay_date_val = (_parse_field_date(v) for v in values)
    if period_end is None or pay_date_val is None:
        return [], _skip_missing("PAY.DATE_LOGIC", "pay_period_end/pay_date (unparseable)")
    if not (period_end <= pay_date_val <= period_end + timedelta(days=45)):
        return [_finding(
            "PAY.DATE_LOGIC", "inconsistent",
            f"Payment date {pay_date_val.isoformat()} is not within 45 days after the pay "
            f"period end ({period_end.isoformat()})",
            field="pay_date", stated=pay_date_val.isoformat(),
        )], None
    return [], None


PAYSLIP_DATE_CHECKS: list[CheckFn] = [_check_payslip_date_logic]


# ---------------------------------------------------------------------------
# Credit report
# ---------------------------------------------------------------------------


def _check_cr_earliest_account(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """CR.EARLIEST_ACCOUNT. The stated earliest account date must equal
    the earliest opening date among the accounts."""
    values = _need(ctx, "CR.EARLIEST_ACCOUNT", "accounts", "earliest_account_date")
    if values is None:
        return [], _skip_missing("CR.EARLIEST_ACCOUNT", "accounts", "earliest_account_date")
    rows, stated_raw = values
    opening_dates = [d for d in (_parse_field_date(r.get("date_opened")) for r in _rows(rows)) if d]
    stated = _parse_field_date(stated_raw)
    if not opening_dates or stated is None:
        return [], _skip_missing("CR.EARLIEST_ACCOUNT", "accounts[].date_opened (unparseable)")
    earliest = min(opening_dates)
    if stated != earliest:
        return [_finding(
            "CR.EARLIEST_ACCOUNT", "inconsistent",
            f"Stated earliest account date ({stated.isoformat()}) does not match the earliest "
            f"account opening date among the rows ({earliest.isoformat()})",
            field="earliest_account_date", stated=stated.isoformat(), expected=earliest.isoformat(),
        )], None
    return [], None


def _check_cr_age_at_opening(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """CR.AGE_AT_OPENING. At least 21 for a credit card, 18 for any other
    credit, using the DOB printed in the report itself."""
    values = _need(ctx, "CR.AGE_AT_OPENING", "accounts", "date_of_birth")
    if values is None:
        return [], _skip_missing("CR.AGE_AT_OPENING", "accounts", "date_of_birth")
    rows, dob_raw = values
    dob = _parse_field_date(dob_raw)
    if dob is None:
        return [], _skip_missing("CR.AGE_AT_OPENING", "date_of_birth (unparseable)")
    findings = []
    for i, row in enumerate(_rows(rows)):
        opened = _parse_field_date(row.get("date_opened"))
        if opened is None:
            continue
        age_days = (opened - dob).days
        min_age = 21 if "card" in str(row.get("product_type") or "").lower() else 18
        if age_days < min_age * 365.25:
            findings.append(_finding(
                "CR.AGE_AT_OPENING", "impossible",
                f"Account {i + 1} was opened at age {age_days / 365.25:.1f}, below the "
                f"minimum of {min_age} for this product type",
                field=f"accounts[{i}].date_opened",
            ))
    return findings, None


def _check_cr_date_logic(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """
    CR.DATE_LOGIC. Closing follows opening; no account date is in the
    future (checked against today directly — not just the report's own
    date — so this still catches it even if report_date wasn't extracted);
    all dates on or before the enquiry date.

    A date that's actually in the future (vs today) is reported as
    impossible rather than merely inconsistent-with-the-report-date: it
    can't be real regardless of what the report itself claims elsewhere.
    The report-date comparison only fires when the stronger future check
    didn't already catch that same date, to avoid flagging the same fact
    twice under two severities.
    """
    values = _need(ctx, "CR.DATE_LOGIC", "accounts")
    if values is None:
        return [], _skip_missing("CR.DATE_LOGIC", "accounts")
    enquiry_date = _parse_field_date(ctx.fields.get("report_date"))
    findings = []
    for i, row in enumerate(_rows(values[0])):
        opened, closed = _parse_field_date(row.get("date_opened")), _parse_field_date(row.get("date_closed"))
        if opened and closed and closed < opened:
            findings.append(_finding(
                "CR.DATE_LOGIC", "inconsistent",
                f"Account {i + 1}'s close date ({closed.isoformat()}) is before its open date "
                f"({opened.isoformat()})",
                field=f"accounts[{i}].date_closed",
            ))
        for d, label, field_name in ((opened, "opening", "date_opened"), (closed, "closing", "date_closed")):
            if d is None:
                continue
            if d > ctx.today:
                findings.append(_finding(
                    "CR.DATE_LOGIC", "impossible",
                    f"Account {i + 1}'s {label} date ({d.isoformat()}) is in the future "
                    f"(today is {ctx.today.isoformat()})",
                    field=f"accounts[{i}].{field_name}", stated=d.isoformat(),
                ))
            elif enquiry_date and d > enquiry_date:
                findings.append(_finding(
                    "CR.DATE_LOGIC", "inconsistent",
                    f"Account {i + 1}'s {label} date ({d.isoformat()}) is after the report "
                    f"date ({enquiry_date.isoformat()})",
                    field=f"accounts[{i}].{field_name}", stated=d.isoformat(),
                    expected=f"on or before {enquiry_date.isoformat()}",
                ))
    return findings, None


CREDIT_REPORT_DATE_CHECKS: list[CheckFn] = [
    _check_cr_earliest_account, _check_cr_age_at_opening, _check_cr_date_logic,
]


# ---------------------------------------------------------------------------
# List of loans
# ---------------------------------------------------------------------------


def _check_loan_date_logic(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """LOAN.DATE_LOGIC. Acceptance on or after offer, within validity."""
    values = _need(ctx, "LOAN.DATE_LOGIC", "offer_date", "acceptance_date")
    if values is None:
        return [], _skip_missing("LOAN.DATE_LOGIC", "offer_date", "acceptance_date")
    offer, acceptance = (_parse_field_date(v) for v in values)
    if offer is None or acceptance is None:
        return [], _skip_missing("LOAN.DATE_LOGIC", "offer_date/acceptance_date (unparseable)")
    if acceptance < offer:
        return [_finding(
            "LOAN.DATE_LOGIC", "inconsistent",
            f"Acceptance date {acceptance.isoformat()} is before the offer date "
            f"({offer.isoformat()})",
            field="acceptance_date", stated=acceptance.isoformat(),
        )], None
    validity_days = _as_number(ctx.fields.get("offer_validity_days")) or 30
    if (acceptance - offer).days > validity_days:
        return [_finding(
            "LOAN.DATE_LOGIC", "inconsistent",
            f"Acceptance is {(acceptance - offer).days} days after the offer, beyond its "
            f"{validity_days:.0f}-day validity",
            field="acceptance_date", stated=acceptance.isoformat(),
        )], None
    return [], None


LIST_OF_LOANS_DATE_CHECKS: list[CheckFn] = [_check_loan_date_logic]


# ---------------------------------------------------------------------------
# Proof of address
# ---------------------------------------------------------------------------


def _check_poa_period_logic(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """POA.PERIOD_LOGIC. Billing period ends on or before the bill date."""
    values = _need(ctx, "POA.PERIOD_LOGIC", "billing_period_end", "bill_date")
    if values is None:
        return [], _skip_missing("POA.PERIOD_LOGIC", "billing_period_end", "bill_date")
    period_end, bill_date_val = (_parse_field_date(v) for v in values)
    if period_end is None or bill_date_val is None:
        return [], _skip_missing("POA.PERIOD_LOGIC", "billing_period_end/bill_date (unparseable)")
    if period_end > bill_date_val:
        return [_finding(
            "POA.PERIOD_LOGIC", "impossible",
            f"Billing period ends ({period_end.isoformat()}) after the bill date "
            f"({bill_date_val.isoformat()})",
            field="billing_period_end", stated=period_end.isoformat(),
        )], None
    return [], None


def _check_poa_due_date(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """POA.DUE_DATE. Due date after bill date, within a normal window."""
    values = _need(ctx, "POA.DUE_DATE", "bill_date", "due_date")
    if values is None:
        return [], _skip_missing("POA.DUE_DATE", "bill_date", "due_date")
    bill_date_val, due_date_val = (_parse_field_date(v) for v in values)
    if bill_date_val is None or due_date_val is None:
        return [], _skip_missing("POA.DUE_DATE", "bill_date/due_date (unparseable)")
    if not (bill_date_val < due_date_val <= bill_date_val + timedelta(days=60)):
        return [_finding(
            "POA.DUE_DATE", "inconsistent",
            f"Due date ({due_date_val.isoformat()}) is not within a normal window after the "
            f"bill date ({bill_date_val.isoformat()})",
            field="due_date", stated=due_date_val.isoformat(),
        )], None
    return [], None


def _check_poa_contract_term(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """POA.CONTRACT_TERM. A contract end date far beyond a typical
    contract term."""
    values = _need(ctx, "POA.CONTRACT_TERM", "bill_date", "contract_end_date")
    if values is None:
        return [], _skip_missing("POA.CONTRACT_TERM", "bill_date", "contract_end_date")
    bill_date_val, end_date_val = (_parse_field_date(v) for v in values)
    if bill_date_val is None or end_date_val is None:
        return [], _skip_missing("POA.CONTRACT_TERM", "bill_date/contract_end_date (unparseable)")
    if (end_date_val - bill_date_val).days > 365 * 5:
        return [_finding(
            "POA.CONTRACT_TERM", "signal",
            f"Contract end date ({end_date_val.isoformat()}) is more than 5 years beyond the "
            f"bill date — unusually long for a utility contract",
            field="contract_end_date",
        )], None
    return [], None


PROOF_OF_ADDRESS_DATE_CHECKS: list[CheckFn] = [
    _check_poa_period_logic, _check_poa_due_date, _check_poa_contract_term,
]


# ---------------------------------------------------------------------------
# Certificate of incorporation
# ---------------------------------------------------------------------------


def _check_coi_event_after_issue(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """COI.EVENT_AFTER_ISSUE. No event (e.g. a name change) may be dated
    after the certificate's own issue date."""
    values = _need(ctx, "COI.EVENT_AFTER_ISSUE", "certificate_date")
    if values is None:
        return [], _skip_missing("COI.EVENT_AFTER_ISSUE", "certificate_date")
    issue_date = _parse_field_date(values[0])
    if issue_date is None:
        return [], _skip_missing("COI.EVENT_AFTER_ISSUE", "certificate_date (unparseable)")
    name_change = _parse_field_date(ctx.fields.get("company_former_name_change_date"))
    if name_change and name_change > issue_date:
        return [_finding(
            "COI.EVENT_AFTER_ISSUE", "impossible",
            f"Name change ({name_change.isoformat()}) is dated after the certificate's own "
            f"issue date ({issue_date.isoformat()})",
            field="company_former_name_change_date", stated=name_change.isoformat(),
        )], None
    return [], None


def _check_coi_date_logic(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """COI.DATE_LOGIC. Any name change must be dated after incorporation."""
    values = _need(ctx, "COI.DATE_LOGIC", "incorporation_date")
    if values is None:
        return [], _skip_missing("COI.DATE_LOGIC", "incorporation_date")
    incorporation = _parse_field_date(values[0])
    name_change = _parse_field_date(ctx.fields.get("company_former_name_change_date"))
    if incorporation is None:
        return [], _skip_missing("COI.DATE_LOGIC", "incorporation_date (unparseable)")
    if name_change and name_change < incorporation:
        return [_finding(
            "COI.DATE_LOGIC", "inconsistent",
            f"Name change ({name_change.isoformat()}) is dated before incorporation "
            f"({incorporation.isoformat()})",
            field="company_former_name_change_date", stated=name_change.isoformat(),
        )], None
    return [], None


def _check_coi_uen_year(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """COI.UEN_YEAR. UEN's year prefix must equal the incorporation
    year."""
    values = _need(ctx, "COI.UEN_YEAR", "uen", "incorporation_date")
    if values is None:
        return [], _skip_missing("COI.UEN_YEAR", "uen", "incorporation_date")
    uen_raw, incorporation_raw = values
    incorporation = _parse_field_date(incorporation_raw)
    uen = str(uen_raw).strip().upper()
    match = _UEN_RE.match(uen)
    if incorporation is None or not match:
        return [], _skip_missing("COI.UEN_YEAR", "uen/incorporation_date (unparseable)")
    year_prefix = int(match.group(1))
    if year_prefix != incorporation.year:
        return [_finding(
            "COI.UEN_YEAR", "inconsistent",
            f"UEN's year prefix ({year_prefix}) does not match the incorporation year "
            f"({incorporation.year})",
            field="uen", stated=str(year_prefix), expected=str(incorporation.year),
        )], None
    return [], None


CERTIFICATE_OF_INCORPORATION_DATE_CHECKS: list[CheckFn] = [
    _check_coi_event_after_issue, _check_coi_date_logic, _check_coi_uen_year,
]


# ---------------------------------------------------------------------------
# Certificate of incumbency (ACRA business profile)
# ---------------------------------------------------------------------------


def _check_acra_date_logic(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """ACRA.DATE_LOGIC. Officer appointment dates and the status date fall
    on/after incorporation; last AGM/annual return on/before document
    date."""
    values = _need(ctx, "ACRA.DATE_LOGIC", "incorporation_date")
    if values is None:
        return [], _skip_missing("ACRA.DATE_LOGIC", "incorporation_date")
    incorporation = _parse_field_date(values[0])
    if incorporation is None:
        return [], _skip_missing("ACRA.DATE_LOGIC", "incorporation_date (unparseable)")

    findings = []
    for officer in _rows(ctx.fields.get("officers")):
        appointed = _parse_field_date(officer_appointed(officer))
        if appointed and appointed < incorporation:
            findings.append(_finding(
                "ACRA.DATE_LOGIC", "inconsistent",
                f"Officer appointment ({appointed.isoformat()}) is before incorporation "
                f"({incorporation.isoformat()})",
                field="officers",
            ))

    document_date = _parse_field_date(ctx.fields.get("document_date"))
    if document_date:
        for name in ("last_agm_date", "last_annual_return_date"):
            value = _parse_field_date(ctx.fields.get(name))
            if value and value > document_date:
                findings.append(_finding(
                    "ACRA.DATE_LOGIC", "inconsistent",
                    f"{name} ({value.isoformat()}) is after the document date "
                    f"({document_date.isoformat()})",
                    field=name,
                ))
    return findings, None


def _check_acra_filing_vs_fye(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """ACRA.FILING_VS_FYE. Last AGM/annual return timing must fit the
    stated financial year end."""
    values = _need(ctx, "ACRA.FILING_VS_FYE", "financial_year_end", "last_agm_date")
    if values is None:
        return [], _skip_missing("ACRA.FILING_VS_FYE", "financial_year_end", "last_agm_date")
    fye, agm = (_parse_field_date(v) for v in values)
    if fye is None or agm is None:
        return [], _skip_missing("ACRA.FILING_VS_FYE", "financial_year_end/last_agm_date (unparseable)")
    if not (fye <= agm <= fye + timedelta(days=180)):
        return [_finding(
            "ACRA.FILING_VS_FYE", "implausible",
            f"Last AGM ({agm.isoformat()}) does not fall within 6 months after the financial "
            f"year end ({fye.isoformat()})",
            field="last_agm_date",
        )], None
    return [], None


def _check_acra_secretary_timing(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """ACRA.SECRETARY_TIMING. A secretary must be appointed within 6 months
    of incorporation. Split out of the original ACRA.OFFICERS check — the
    officer-presence half (at least one director/secretary listed) stays in
    numerical_validation.py since it isn't date logic; this is the timing
    half."""
    values = _need(ctx, "ACRA.SECRETARY_TIMING", "officers", "incorporation_date")
    if values is None:
        return [], _skip_missing("ACRA.SECRETARY_TIMING", "officers", "incorporation_date")
    rows, incorporation_raw = values
    incorporation = _parse_field_date(incorporation_raw)
    if incorporation is None:
        return [], _skip_missing("ACRA.SECRETARY_TIMING", "incorporation_date (unparseable)")
    secretary = next((o for o in _rows(rows) if "secretary" in officer_role(o).lower()), None)
    if secretary is None:
        return [], None  # ACRA.OFFICERS already reports a missing secretary
    appointed = _parse_field_date(officer_appointed(secretary))
    if appointed and (appointed - incorporation).days > 180:
        return [_finding(
            "ACRA.SECRETARY_TIMING", "inconsistent",
            f"Secretary appointed {(appointed - incorporation).days} days after "
            "incorporation, beyond the usual 6-month requirement",
            field="officers",
        )], None
    return [], None


CERTIFICATE_OF_INCUMBENCY_DATE_CHECKS: list[CheckFn] = [
    _check_acra_date_logic, _check_acra_filing_vs_fye, _check_acra_secretary_timing,
]


# ---------------------------------------------------------------------------
# Company constitution
# ---------------------------------------------------------------------------


def _check_const_date_logic(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """CONST.DATE_LOGIC. Adoption/execution dates fall on or after
    incorporation."""
    values = _need(ctx, "CONST.DATE_LOGIC", "incorporation_date")
    if values is None:
        return [], _skip_missing("CONST.DATE_LOGIC", "incorporation_date")
    incorporation = _parse_field_date(values[0])
    if incorporation is None:
        return [], _skip_missing("CONST.DATE_LOGIC", "incorporation_date (unparseable)")
    findings = []
    for name in ("adoption_date", "execution_date"):
        value = _parse_field_date(ctx.fields.get(name))
        if value and value < incorporation:
            findings.append(_finding(
                "CONST.DATE_LOGIC", "inconsistent",
                f"{name} ({value.isoformat()}) is before incorporation ({incorporation.isoformat()})",
                field=name, stated=value.isoformat(),
            ))
    return findings, None


COMPANY_CONSTITUTION_DATE_CHECKS: list[CheckFn] = [_check_const_date_logic]


# ---------------------------------------------------------------------------
# NRIC (document-specific date logic beyond the universal ID.* checks)
# ---------------------------------------------------------------------------


def _check_nric_date_logic(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """ID.DATE_LOGIC (NRIC). Issue date after date of birth, at a
    plausible age, and not in the future."""
    values = _need(ctx, "ID.DATE_LOGIC", "date_of_issue", "date_of_birth")
    if values is None:
        return [], _skip_missing("ID.DATE_LOGIC", "date_of_issue", "date_of_birth")
    issue, dob = (_parse_field_date(v) for v in values)
    if issue is None or dob is None:
        return [], _skip_missing("ID.DATE_LOGIC", "date_of_issue/date_of_birth (unparseable)")
    findings = []
    if issue < dob:
        findings.append(_finding(
            "ID.DATE_LOGIC", "impossible",
            f"Issue date ({issue.isoformat()}) is before date of birth ({dob.isoformat()})",
            field="date_of_issue", stated=issue.isoformat(),
        ))
    elif (issue - dob).days < 365:
        findings.append(_finding(
            "ID.DATE_LOGIC", "implausible",
            f"NRIC was issued at under 1 year old ({issue.isoformat()} vs birth "
            f"{dob.isoformat()})",
            field="date_of_issue", stated=issue.isoformat(),
        ))
    if issue > ctx.today:
        findings.append(_finding(
            "ID.DATE_LOGIC", "impossible", f"Issue date ({issue.isoformat()}) is in the future",
            field="date_of_issue", stated=issue.isoformat(),
        ))
    return findings, None


NRIC_DATE_CHECKS: list[CheckFn] = [_check_nric_date_logic]


# ---------------------------------------------------------------------------
# Passport
# ---------------------------------------------------------------------------


def _check_passport_validity_period(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """ID.VALIDITY_PERIOD. Issue-to-expiry period must fit the passport
    type — 10 years for an adult Singapore passport."""
    values = _need(ctx, "ID.VALIDITY_PERIOD", "date_of_issue", "date_of_expiry")
    if values is None:
        return [], _skip_missing("ID.VALIDITY_PERIOD", "date_of_issue", "date_of_expiry")
    issue, expiry = (_parse_field_date(v) for v in values)
    if issue is None or expiry is None:
        return [], _skip_missing("ID.VALIDITY_PERIOD", "date_of_issue/date_of_expiry (unparseable)")
    years = (expiry - issue).days / 365.25
    if abs(years - 10) > 0.1:
        return [_finding(
            "ID.VALIDITY_PERIOD", "inconsistent",
            f"Passport validity is {years:.1f} years — an adult Singapore passport is issued "
            "for 10 years",
            field="date_of_expiry", stated=expiry.isoformat(),
        )], None
    return [], None


def _check_passport_expired(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """ID.EXPIRED."""
    values = _need(ctx, "ID.EXPIRED", "date_of_expiry")
    if values is None:
        return [], _skip_missing("ID.EXPIRED", "date_of_expiry")
    expiry = _parse_field_date(values[0])
    if expiry is None:
        return [], _skip_missing("ID.EXPIRED", "date_of_expiry (unparseable)")
    if expiry < ctx.today:
        return [_finding(
            "ID.EXPIRED", "signal", f"Passport expired on {expiry.isoformat()}",
            field="date_of_expiry", stated=expiry.isoformat(),
        )], None
    return [], None


PASSPORT_DATE_CHECKS: list[CheckFn] = [_check_passport_validity_period, _check_passport_expired]


# ---------------------------------------------------------------------------
# FATCA/CRS declaration
# ---------------------------------------------------------------------------


def _check_fatca_date_logic(ctx: _Ctx) -> tuple[list[DateFinding], SkippedCheck | None]:
    """FATCA.DATE_LOGIC. Declaration on or after the form version date,
    not in the future."""
    values = _need(ctx, "FATCA.DATE_LOGIC", "declaration_date")
    if values is None:
        return [], _skip_missing("FATCA.DATE_LOGIC", "declaration_date")
    declaration = _parse_field_date(values[0])
    if declaration is None:
        return [], _skip_missing("FATCA.DATE_LOGIC", "declaration_date (unparseable)")
    findings = []
    if declaration > ctx.today:
        findings.append(_finding(
            "FATCA.DATE_LOGIC", "inconsistent",
            f"Declaration date ({declaration.isoformat()}) is in the future",
            field="declaration_date", stated=declaration.isoformat(),
        ))
    form_version = _parse_field_date(ctx.fields.get("form_version_date"))
    if form_version and declaration < form_version:
        findings.append(_finding(
            "FATCA.DATE_LOGIC", "inconsistent",
            f"Declaration date ({declaration.isoformat()}) is before the form's own version "
            f"date ({form_version.isoformat()})",
            field="declaration_date", stated=declaration.isoformat(),
        ))
    return findings, None


FATCA_CRS_DATE_CHECKS: list[CheckFn] = [_check_fatca_date_logic]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

DATE_DOCUMENT_TYPE_CHECKS: dict[str, list[CheckFn]] = {
    "bank_statement": BANK_STATEMENT_DATE_CHECKS,
    "source_of_wealth": SOURCE_OF_WEALTH_DATE_CHECKS,
    "notice_of_assessment": NOTICE_OF_ASSESSMENT_DATE_CHECKS,
    "payslip": PAYSLIP_DATE_CHECKS,
    "credit_report": CREDIT_REPORT_DATE_CHECKS,
    "list_of_loans": LIST_OF_LOANS_DATE_CHECKS,
    "proof_of_address": PROOF_OF_ADDRESS_DATE_CHECKS,
    "certificate_of_incorporation": CERTIFICATE_OF_INCORPORATION_DATE_CHECKS,
    "certificate_of_incumbency": CERTIFICATE_OF_INCUMBENCY_DATE_CHECKS,
    "company_constitution": COMPANY_CONSTITUTION_DATE_CHECKS,
    "nric": NRIC_DATE_CHECKS,
    "passport": PASSPORT_DATE_CHECKS,
    "fatca_crs_declaration": FATCA_CRS_DATE_CHECKS,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def check_date_anomalies(
    document_id: str,
    extracted_fields: list[ExtractedField] | None = None,
    document_type: str | None = None,
) -> DateAnomalyResult:
    """
    AC1/AC2: extracts and labels every identifiable date. Then runs every
    GEN.*/ID.* date check plus every check registered for `document_type`
    (AC3/AC4). A check whose required field(s) weren't extracted is
    recorded in `skipped_checks`, never silently dropped. AC7: Unable to
    Assess when no date could be reliably extracted at all.

    Works from extracted_fields alone — no file download or re-ingestion.
    extracted_fields is trusted as already human-reviewed by the time this
    runs, so there's no need to re-derive or corroborate anything from the
    document's own raw text (see _apply_needs_review).
    """
    fields = _fields_by_name(extracted_fields)
    confidences = _confidence_by_name(extracted_fields)
    ctx = _Ctx(fields=fields, document_type=document_type, today=date.today())

    dates_extracted = extract_labeled_dates(ctx, confidences)

    if not dates_extracted and not _has_any_date_like_field(ctx):
        return DateAnomalyResult(
            document_id=document_id, document_type=document_type,
            dates_extracted=[], findings=[], skipped_checks=[],
            unable_to_assess=True,
            unable_to_assess_reason="No dates could be reliably extracted from this document",
        )

    checks = list(GENERAL_DATE_CHECKS) + list(DATE_DOCUMENT_TYPE_CHECKS.get(document_type or "", []))
    findings: list[DateFinding] = []
    skipped: list[SkippedCheck] = []
    for check in checks:
        result_findings, skip = check(ctx)
        findings.extend(result_findings)
        if skip is not None:
            skipped.append(skip)

    findings = _apply_needs_review(findings, confidences)

    return DateAnomalyResult(
        document_id=document_id,
        document_type=document_type,
        dates_extracted=dates_extracted,
        findings=findings,
        skipped_checks=skipped,
        unable_to_assess=False,
    )
