# numerical_validation.py
"""
Content Validation Specialist — extracts, reconciles and validates the
numerical information in a document, including a Benford's Law pass over
its leading-digit distribution.

Two sources of numbers, deliberately:

* The document's own text (via ingestion_service, the same native-PDF/OCR
  pipeline the rest of the pipeline uses) supplies volume and position —
  every figure on the page, with the page and surrounding line it came
  from. This is what format validation (AC1) and Benford's (AC5) run on,
  because both need breadth and Benford's in particular is meaningless on
  a handful of values.

* The document's already-extracted fields supply *structure* — a bank
  statement's transactions with their running balance, a portfolio's
  holdings with quantity/price/market value. Arithmetic reconciliation
  (AC2-AC4) runs on these, because only here do we know which number is
  supposed to equal which.

Every arithmetic rule below is grounded in a field shape that
extraction_prompts.EXTRA_FIELD_GUIDANCE actually specifies, not a guess at
what a document might contain. Notably absent: notice_of_assessment's
tax_computation. Its lines are a mixed sequence (chargeable income, minus
two exemption tiers, times 17%, minus a rebate — see
doc_gen_scripts/.../generate_noa.py's compute_noa_tax), so they do NOT sum
to tax_payable and a naive sum check there would fire on every genuine NOA.
"""
from __future__ import annotations

import calendar
import json
import math
import re
from typing import Any, Literal

from pydantic import BaseModel

from ..ingestion.ingestion_service import ingest_document
from .font_consistency_v2 import ExtractedField

# Benford's Law: P(leading digit = d) = log10(1 + 1/d).
BENFORD_EXPECTED: dict[int, float] = {
    d: math.log10(1 + 1 / d) * 100 for d in range(1, 10)
}

# Chi-square goodness-of-fit needs an expected count of at least 5 in every
# bin to be valid. Digit 9 is the rarest at 4.58%, so 5/0.0458 ~= 110 values
# is the floor; below it the distribution is reported but not judged (AC5's
# own "given sufficient extracted eligible numerical values" precondition).
BENFORD_MIN_SAMPLE = 120

# Chi-square critical value, 8 degrees of freedom (9 digits - 1), p = 0.01.
#
# Deliberately NOT Nigrini's mean-absolute-deviation bands, which are the
# more commonly cited first-digit test. Those bands assume audit-scale
# samples; measured against synthetic data that genuinely follows Benford,
# median MAD is 0.032 at n=50 and 0.016 at n=200 — both above the 0.015
# "nonconformity" line — so they would have flagged the majority of
# legitimate single documents. Chi-square has the sample size built into
# it: measured false-positive rate on true-Benford data is 0.5-1.5% across
# n=50..2000, matching its nominal 1%.
_CHI_SQUARE_CRITICAL_8DF_P01 = 20.090

# Money rounds to cents, and OCR/extraction can differ in the last place,
# so reconciliation allows a cent of slack rather than demanding exactness.
_MONEY_TOLERANCE = 0.011

# A quantity x unit-price product is rounded for display, so allow a
# slightly wider band than a pure addition check.
_PRODUCT_TOLERANCE = 0.51

# Guards against a pathological document (a huge table, an OCR run that
# produces thousands of fragments) turning this into a slow scan.
_MAX_VALUES = 5000

_CURRENCY_WORDS = r"SGD|USD|EUR|GBP|MYR|AUD|HKD|JPY|CNY|RMB|IDR|THB|PHP|INR"
_CURRENCY_SYMBOLS = r"\$|€|£|¥|₹"

# Dates are matched (and consumed) before plain numbers so "01/02/2026"
# isn't mistaken for three separate quantities. Both little-endian
# (dd/mm/yyyy) and ISO (yyyy-mm-dd) appear in this pipeline's documents,
# plus the "01-Jun-2026" style a statement period uses.
_MONTH_NAMES = (
    "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
)
_DATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b"), "dmy"),
    (
        re.compile(rf"\b(\d{{1,2}})[- ]({_MONTH_NAMES})[a-z]*[- ](\d{{4}})\b", re.IGNORECASE),
        "dMy",
    ),
]

# A number, optionally preceded by a currency marker, optionally wrapped in
# parentheses (accounting negative) and optionally followed by %. The digit
# body allows 3-digit Western grouping and 2-digit Indian grouping; a group
# of any other length is what _validate_formats treats as malformed.
_NUMBER_RE = re.compile(
    rf"""
    (?P<currency>{_CURRENCY_SYMBOLS}|\b(?:{_CURRENCY_WORDS})\b\s?)?
    \s*
    (?P<open>\()?
    (?P<sign>[-−])?
    (?P<body>\d+(?:,\d+)*(?:\.\d+)?)
    (?P<close>\))?
    (?P<percent>\s?%)?
    """,
    re.VERBOSE | re.IGNORECASE,
)

ValueKind = Literal["currency", "percentage", "date", "quantity"]


class NumericalValue(BaseModel):
    """One numeric token found in the document text, with where it sat."""

    raw: str
    # None for a date (a calendar date has no magnitude this check uses)
    # and for anything unparseable.
    value: float | None
    kind: ValueKind
    # AC1: whether the token is a well-formed instance of its kind. Only
    # dates can currently be malformed — see the digit-grouping note above.
    well_formed: bool = True
    page: int
    section: int
    location: str


class NumericalFinding(BaseModel):
    """
    AC3/AC4/AC6. `stated` is the value as the document gives it, `expected`
    the value this check computed for it — both present for a
    reconciliation mismatch, `expected` empty for a pure format problem.
    `page`/`location` are populated where the finding traces back to a
    place in the text; an arithmetic finding derived from extracted fields
    legitimately has neither, which is why AC6 says "where available".
    """

    finding_type: Literal["format", "arithmetic", "benford"]
    field: str | None = None
    stated: str | None = None
    expected: str | None = None
    page: int | None = None
    location: str | None = None
    severity: Literal["high", "medium", "low"]
    # Set where the finding needs framing the severity alone doesn't give —
    # a Benford result is a statement about the whole distribution, not an
    # accusation about any single figure, and a lead agent weighing it
    # alongside per-figure findings should know that.
    severity_note: str | None = None
    summary: str


class BenfordDigit(BaseModel):
    """AC5: one row of the leading-digit table."""

    digit: int
    count: int
    observed_percentage: float
    expected_percentage: float


class BenfordAnalysis(BaseModel):
    """
    AC5. `digits` always covers 1-9 (a digit that never occurs is reported
    with count 0, not omitted) so the caller gets a complete distribution.
    `unable_to_assess` here is independent of the overall result's: a
    document can have perfectly reconcilable arithmetic and still carry too
    few values for a meaningful Benford test.
    """

    sample_size: int
    digits: list[BenfordDigit]
    # Descriptive summary of the gap, reported for the lead agent's
    # benefit. NOT what the verdict is based on — see the note on
    # _CHI_SQUARE_CRITICAL_8DF_P01 for why MAD bands don't survive at
    # single-document sample sizes.
    mean_absolute_deviation: float | None = None
    chi_square: float | None = None
    chi_square_critical: float | None = None
    # False only when the distribution differs from Benford's by more than
    # chance at p < 0.01.
    conforms: bool | None = None
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None


class NumericalValidationResult(BaseModel):
    """
    Shared response for GET /documents/{id}/detection/numerical-validation
    and POST /extraction/test-numerical-validation. Nothing is persisted —
    this is a pure read/compute, like the spelling and layout checks.

    Empty `findings` with `unable_to_assess=false` means the checks ran and
    nothing failed to reconcile — not that the figures are proven genuine.
    """

    document_id: str
    values_analysed: int
    findings: list[NumericalFinding]
    benford: BenfordAnalysis
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None


def _benford_unavailable(reason: str, sample_size: int = 0) -> BenfordAnalysis:
    return BenfordAnalysis(
        sample_size=sample_size,
        digits=[
            BenfordDigit(
                digit=d, count=0, observed_percentage=0.0,
                expected_percentage=round(BENFORD_EXPECTED[d], 4),
            )
            for d in range(1, 10)
        ],
        unable_to_assess=True,
        unable_to_assess_reason=reason,
    )


def _empty_result(document_id: str, reason: str) -> NumericalValidationResult:
    return NumericalValidationResult(
        document_id=document_id,
        values_analysed=0,
        findings=[],
        benford=_benford_unavailable(reason),
        unable_to_assess=True,
        unable_to_assess_reason=reason,
    )


def _to_float(body: str) -> float | None:
    try:
        return float(body.replace(",", ""))
    except ValueError:
        return None


# Digit-grouping validation was considered and deliberately dropped: there
# is no locale-safe version of it. "2,5" is a European decimal comma, not a
# malformed thousands group, and "1,2345" is ordinary East-Asian 4-digit
# (万) grouping — both would have been flagged by a Western-3/Indian-2 rule,
# on documents that are perfectly well formed. An invalid calendar date is
# the only numeric-format error with no innocent reading in any locale, so
# it's the only one this check reports.


def _iter_text_spans(pages: list[dict]) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for page in pages:
        page_number = page.get("page_number", 0)
        elements = page.get("elements") or [{"text": page.get("text", "")}]
        for section, element in enumerate(elements):
            text = (element.get("text") or "").strip()
            if text:
                spans.append((page_number, section, text))
    return spans


def _valid_calendar_date(year: int, month: int, day: int) -> bool:
    # Year 0 doesn't exist in the Gregorian calendar and a 3-digit year
    # predates anything a KYC document could be about — both show up as
    # placeholder/default values in badly generated documents. No tighter
    # bound than that, because a date of birth legitimately reaches back a
    # century and a maturity date runs forward.
    if not 1000 <= year <= 9999:
        return False
    if not 1 <= month <= 12:
        return False
    return 1 <= day <= calendar.monthrange(year, month)[1]


def _valid_either_order(year: int, first: int, second: int) -> bool:
    """
    "12/25/2026" is day-first nonsense but a perfectly good US-format date,
    and nothing in the document tells us which convention it was written
    in. So a slash/dash date counts as valid if EITHER reading works, and
    is only reported when neither does (e.g. "32/25/2026").
    """
    return _valid_calendar_date(year, first, second) or _valid_calendar_date(
        year, second, first
    )


def _extract_dates(text: str) -> tuple[list[tuple[str, bool]], str]:
    """
    Returns the date tokens found (with whether they're a real calendar
    date) and the text with those tokens blanked out, so the number scan
    that follows doesn't re-read their components as separate figures.
    """
    found: list[tuple[str, bool]] = []
    remaining = text

    for pattern, order in _DATE_PATTERNS:
        for match in pattern.finditer(remaining):
            # Every group the patterns capture is guaranteed to be digits
            # (or, for dMy, a month name _month_number maps to 0 when
            # unrecognised, which _valid_calendar_date then rejects), so
            # there's nothing here that can raise.
            groups = match.groups()
            if order == "ymd":
                year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
                is_valid = _valid_calendar_date(year, month, day)
            elif order == "dmy":
                # Ambiguous between dd/mm and mm/dd — see
                # _valid_either_order.
                first, second, year = int(groups[0]), int(groups[1]), int(groups[2])
                is_valid = _valid_either_order(year, first, second)
            else:
                day, year = int(groups[0]), int(groups[2])
                is_valid = _valid_calendar_date(year, _month_number(groups[1]), day)
            found.append((match.group(0), is_valid))
        remaining = pattern.sub(lambda m: " " * len(m.group(0)), remaining)

    return found, remaining


def _month_number(name: str) -> int:
    lowered = name.lower()[:3]
    months = ["jan", "feb", "mar", "apr", "may", "jun",
              "jul", "aug", "sep", "oct", "nov", "dec"]
    return months.index(lowered) + 1 if lowered in months else 0


def _classify(match: re.Match[str]) -> ValueKind:
    if match.group("percent"):
        return "percentage"
    if match.group("currency") or "." in match.group("body"):
        return "currency"
    return "quantity"


def _parse_values(pages: list[dict]) -> list[NumericalValue]:
    """AC1: finds every numeric token in the document text and classifies
    it as a currency amount, percentage, date or plain quantity."""
    values: list[NumericalValue] = []

    for page_number, section, text in _iter_text_spans(pages):
        location = text if len(text) <= 120 else text[:117] + "..."

        dates, without_dates = _extract_dates(text)
        for raw, is_valid in dates:
            values.append(
                NumericalValue(
                    raw=raw,
                    value=None,
                    kind="date",
                    well_formed=is_valid,
                    page=page_number,
                    section=section,
                    location=location,
                )
            )

        for match in _NUMBER_RE.finditer(without_dates):
            # `body` is a required group, so a match always carries digits.
            body = match.group("body")
            parsed = _to_float(body)
            negative = bool(match.group("sign")) or bool(
                match.group("open") and match.group("close")
            )
            if parsed is not None and negative:
                parsed = -parsed
            values.append(
                NumericalValue(
                    raw=match.group(0).strip(),
                    value=parsed,
                    kind=_classify(match),
                    page=page_number,
                    section=section,
                    location=location,
                )
            )
            if len(values) >= _MAX_VALUES:
                return values

    return values


def _validate_formats(values: list[NumericalValue]) -> list[NumericalFinding]:
    """
    AC1/AC3. Only figures that can't be read under any convention are
    reported — in practice, a date that names no real day. A value that's
    merely unusual (a large percentage, an oddly precise amount, an
    unfamiliar digit grouping) has innocent readings and is left alone; see
    the digit-grouping note above for what was deliberately not built here.
    """
    return [
        NumericalFinding(
            finding_type="format",
            stated=value.raw,
            page=value.page,
            location=value.location,
            severity="medium",
            summary=f"{value.raw} is not a valid calendar date",
        )
        for value in values
        if not value.well_formed
    ]


def _decode(value: Any) -> Any:
    """
    Extracted field values come back from a JSON column as whatever the
    stored JSON was, and some are themselves JSON-encoded a second time —
    the same quirk spelling_anomaly._decode_field_text documents.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value


def _fields_by_name(fields: list[ExtractedField] | None) -> dict[str, Any]:
    return {field.field_name: _decode(field.field_value) for field in fields or []}


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(rf"[,\s]|{_CURRENCY_SYMBOLS}|{_CURRENCY_WORDS}", "", value, flags=re.I)
        cleaned = cleaned.strip()
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = "-" + cleaned[1:-1]
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _money(amount: float) -> str:
    return f"{amount:,.2f}"


def _rows(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _check_running_balance(by_name: dict[str, Any]) -> list[NumericalFinding]:
    """
    AC2/AC3/AC4. A bank statement's transactions carry a running balance
    (see EXTRA_FIELD_GUIDANCE's "bank_statement" entry: amount is negative
    for debits, balance is the running total after that line), so each
    balance must equal the previous one plus that row's amount. This is the
    check that catches an edited transaction: change an amount and the
    column below it no longer reconciles.
    """
    findings: list[NumericalFinding] = []
    rows = _rows(by_name.get("transactions"))

    previous: float | None = None
    for index, row in enumerate(rows):
        balance = _as_number(row.get("balance"))
        amount = _as_number(row.get("amount"))

        if previous is None or amount is None:
            # Nothing to compare against yet, or a line whose amount we
            # can't read — pick the thread back up from whatever balance
            # this row states, and drop it entirely if it states none.
            previous = balance
            continue

        expected = previous + amount

        if balance is None:
            # A line that doesn't print its own running total still moves
            # it, so carry the computed figure forward rather than
            # comparing the next line against a stale one.
            previous = expected
            continue

        if abs(expected - balance) > _MONEY_TOLERANCE:
            description = str(row.get("description") or "").strip()
            findings.append(
                NumericalFinding(
                    finding_type="arithmetic",
                    field=f"transactions[{index}].balance",
                    stated=_money(balance),
                    expected=_money(expected),
                    severity="high",
                    summary=(
                        f"Running balance does not reconcile on transaction {index + 1}"
                        + (f" ({description})" if description else "")
                        + f": {_money(previous)} {'-' if amount < 0 else '+'} "
                        f"{_money(abs(amount))} = {_money(expected)}, "
                        f"but the statement shows {_money(balance)}"
                    ),
                )
            )

        # Resync to the stated figure rather than the computed one, so a
        # single edited transaction produces one finding instead of one on
        # every line beneath it.
        previous = balance

    return findings


def _check_holdings(by_name: dict[str, Any]) -> list[NumericalFinding]:
    """
    AC2/AC3/AC4. A portfolio statement's holdings give quantity, closing
    price and market value per position (see EXTRA_FIELD_GUIDANCE's
    "source_of_wealth" entry), so the product must reconcile.
    """
    findings: list[NumericalFinding] = []

    for index, row in enumerate(_rows(by_name.get("holdings"))):
        quantity = _as_number(row.get("quantity"))
        price = _as_number(row.get("closing_price"))
        market_value = _as_number(row.get("market_value"))
        if quantity is None or price is None or market_value is None:
            continue

        expected = quantity * price
        if abs(expected - market_value) > max(_PRODUCT_TOLERANCE, abs(expected) * 0.01):
            name = str(row.get("name") or row.get("symbol") or "").strip()
            findings.append(
                NumericalFinding(
                    finding_type="arithmetic",
                    field=f"holdings[{index}].market_value",
                    stated=_money(market_value),
                    expected=_money(expected),
                    severity="high",
                    summary=(
                        f"Market value does not reconcile for holding {index + 1}"
                        + (f" ({name})" if name else "")
                        + f": {quantity:g} x {_money(price)} = {_money(expected)}, "
                        f"but the statement shows {_money(market_value)}"
                    ),
                )
            )

    return findings


def _check_balance_sheet_identity(by_name: dict[str, Any]) -> list[NumericalFinding]:
    """
    AC2/AC3/AC4. Assets minus liabilities equals net asset value — an
    accounting identity, not a house rule, so a mismatch is a real
    inconsistency rather than a stylistic difference.
    """
    assets = _as_number(by_name.get("total_assets"))
    liabilities = _as_number(by_name.get("total_liabilities"))
    net_assets = _as_number(by_name.get("net_asset_value"))
    if assets is None or liabilities is None or net_assets is None:
        return []

    expected = assets - liabilities
    if abs(expected - net_assets) <= max(_MONEY_TOLERANCE, abs(expected) * 0.001):
        return []

    return [
        NumericalFinding(
            finding_type="arithmetic",
            field="net_asset_value",
            stated=_money(net_assets),
            expected=_money(expected),
            severity="high",
            summary=(
                f"Net asset value does not reconcile: total assets {_money(assets)} - total "
                f"liabilities {_money(liabilities)} = {_money(expected)}, but the document "
                f"states {_money(net_assets)}"
            ),
        )
    ]


def _check_share_allocation(by_name: dict[str, Any]) -> list[NumericalFinding]:
    """
    AC2/AC3/AC4. A constitution lists each subscriber's shareholding (see
    EXTRA_FIELD_GUIDANCE's "company_constitution" entry) alongside the
    company's issued share count, so the parts must add to the whole. Only
    run when every subscriber has a share count — a partial list would
    always "fail" for an innocent reason.
    """
    issued = _as_number(by_name.get("issued_shares"))
    rows = _rows(by_name.get("subscribers"))
    if issued is None or not rows:
        return []

    shares = [_as_number(row.get("shares")) for row in rows]
    if any(share is None for share in shares):
        return []

    allocated = sum(share for share in shares if share is not None)
    if abs(allocated - issued) <= _MONEY_TOLERANCE:
        return []

    return [
        NumericalFinding(
            finding_type="arithmetic",
            field="issued_shares",
            stated=f"{issued:,.0f}",
            expected=f"{allocated:,.0f}",
            severity="high",
            summary=(
                f"Subscriber shareholdings sum to {allocated:,.0f}, but the constitution "
                f"states {issued:,.0f} issued shares"
            ),
        )
    ]


def _benford_eligible(values: list[NumericalValue]) -> list[float]:
    """
    Benford's Law describes naturally occurring magnitudes that are free to
    span orders of magnitude — classically, financial amounts. Only
    currency-classified values qualify here, and that restriction is load
    bearing rather than fussy: run against a real FATCA/CRS form, the
    unrestricted version reported a "nonconforming" distribution built
    almost entirely out of that form's clause and section numbers. Bounded
    or administratively-assigned numbering (clause numbers, page numbers,
    years, percentages, dates) has no reason to follow Benford, so
    including it manufactures deviation on perfectly genuine documents.

    The trade is deliberate: a document whose amounts are all whole
    currency units with no decimal part reads as "quantity" and drops out,
    so the test under-runs rather than over-reports. Reporting Unable to
    Assess is the safer failure here.
    """
    return [
        abs(value.value)
        for value in values
        if value.kind == "currency" and value.value is not None and value.value != 0
    ]


def _leading_digit(magnitude: float) -> int | None:
    text = f"{magnitude:.10e}"
    first = text[0]
    return int(first) if first.isdigit() and first != "0" else None


def _benford(values: list[NumericalValue]) -> tuple[BenfordAnalysis, list[NumericalFinding]]:
    """
    AC5: counts each leading digit 1-9 and records its occurrence count,
    observed percentage and Benford-expected percentage.

    The goodness-of-fit test and the finding that can come out of it go
    beyond AC5 (which only asks for the distribution to be recorded) —
    without them the table is data nobody acts on.
    """
    eligible = _benford_eligible(values)
    digits = [d for d in (_leading_digit(m) for m in eligible) if d is not None]

    if len(digits) < BENFORD_MIN_SAMPLE:
        return (
            _benford_unavailable(
                f"Only {len(digits)} eligible numerical value(s); Benford's Law needs at "
                f"least {BENFORD_MIN_SAMPLE} to be meaningful",
                sample_size=len(digits),
            ),
            [],
        )

    total = len(digits)
    rows: list[BenfordDigit] = []
    deviations: list[float] = []
    chi_square = 0.0
    for digit in range(1, 10):
        count = digits.count(digit)
        observed = count / total * 100
        expected = BENFORD_EXPECTED[digit]
        expected_count = total * expected / 100
        deviations.append(abs(observed - expected) / 100)
        chi_square += (count - expected_count) ** 2 / expected_count
        rows.append(
            BenfordDigit(
                digit=digit,
                count=count,
                observed_percentage=round(observed, 4),
                expected_percentage=round(expected, 4),
            )
        )

    conforms = chi_square <= _CHI_SQUARE_CRITICAL_8DF_P01
    analysis = BenfordAnalysis(
        sample_size=total,
        digits=rows,
        mean_absolute_deviation=round(sum(deviations) / 9, 6),
        chi_square=round(chi_square, 4),
        chi_square_critical=_CHI_SQUARE_CRITICAL_8DF_P01,
        conforms=conforms,
        unable_to_assess=False,
    )

    if conforms:
        return analysis, []

    worst = max(rows, key=lambda row: abs(row.observed_percentage - row.expected_percentage))
    return analysis, [
        NumericalFinding(
            finding_type="benford",
            field="leading_digit_distribution",
            stated=f"{worst.observed_percentage:.1f}% of values lead with {worst.digit}",
            expected=f"{worst.expected_percentage:.1f}%",
            severity="low",
            severity_note=(
                "Statistical, not per-figure: it says the spread of numbers is unusual, "
                "not which number is wrong"
            ),
            summary=(
                f"Leading-digit distribution across {total} values differs from Benford's Law "
                f"by more than chance (chi-square {chi_square:.1f} against a p<0.01 critical "
                f"value of {_CHI_SQUARE_CRITICAL_8DF_P01}); the largest gap is digit "
                f"{worst.digit}. A document legitimately dominated by one kind of figure — a "
                "single repeated fee, a price list — can deviate without anything being wrong"
            ),
        )
    ]


def check_numerical_validation(
    document_id: str,
    file_bytes: bytes,
    filename: str,
    extracted_fields: list[ExtractedField] | None = None,
) -> NumericalValidationResult:
    """
    AC1-AC7. Reads the document's text for figures to validate and count,
    and its already-extracted fields for figures that are supposed to
    reconcile against each other. AC7: Unable to Assess when no numerical
    value can be read out of the document at all.
    """
    try:
        ingestion = ingest_document(file_bytes, filename, _content_type(filename))
    except Exception as exc:  # noqa: BLE001 - surfaced as Unable to Assess
        return _empty_result(
            document_id, f"Text extraction failed: {type(exc).__name__}: {exc}"
        )

    values = _parse_values(ingestion.get("pages") or [])
    by_name = _fields_by_name(extracted_fields)

    arithmetic = (
        _check_running_balance(by_name)
        + _check_holdings(by_name)
        + _check_balance_sheet_identity(by_name)
        + _check_share_allocation(by_name)
    )

    # Extracted fields can still reconcile (or fail to) when the text layer
    # is unreadable — an image-only scan with a good extraction pass, say —
    # so only call the whole check unassessable when neither source gave us
    # anything.
    if not values and not arithmetic:
        return _empty_result(
            document_id, "No numerical values could be extracted from this document"
        )

    benford, benford_findings = _benford(values)

    return NumericalValidationResult(
        document_id=document_id,
        values_analysed=len(values),
        findings=_validate_formats(values) + arithmetic + benford_findings,
        benford=benford,
        unable_to_assess=False,
    )


def _content_type(filename: str) -> str:
    import mimetypes

    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"
