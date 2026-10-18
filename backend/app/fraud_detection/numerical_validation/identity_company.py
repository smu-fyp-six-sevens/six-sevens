"""Numerical checks for general identifiers, identity documents, company documents and FATCA."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .common import (
    officer_role,
    skipped,
    _field_number,
    _MONEY_TOLERANCE,
    CheckResult,
    _as_number,
    _finding,
    _money,
    _parse_field_date,
    _rows,
)
from ..shared.field_values import also_printed_in, describe_path, text_values
from .models import NumericalFinding
from .registry import Num, check


# ---------------------------------------------------------------------------
# GEN.* / ID.* — checks that apply to every document type
# ---------------------------------------------------------------------------


# Real, published algorithm (verified against samliew/singapore-nric, a maintained npm library): weights 2-7-6-5-
# 4-3-2 over the 7 digits, an offset added before the modulus (4 for T/G, 3 for M, 0 for S/F), remainder mod 11,
# and, only for the M series, the index is reflected as 10 - index before the table lookup.
_NRIC_WEIGHTS = (2, 7, 6, 5, 4, 3, 2)
_NRIC_TABLE_ST = "JZIHGFEDCBA"
_NRIC_TABLE_FG = "XWUTRQPNMLK"
_NRIC_TABLE_M = "KLJNPQRTUWX"
_NRIC_RE = re.compile(r"\b([STFGM])(\d{7})([A-Z])\b", re.IGNORECASE)
# A field or column that holds an ID number, as opposed to free text that happens to mention one.
_ID_KEY_RE = re.compile(r"nric|fin\b|identification|(?:^|_)id(?:$|_)|passport|personal_no", re.IGNORECASE)


def _nric_expected_letter(prefix: str, digits: str) -> str | None:
    prefix = prefix.upper()
    weighted = sum(int(d) * w for d, w in zip(digits, _NRIC_WEIGHTS))
    offset = 4 if prefix in ("T", "G") else 3 if prefix == "M" else 0
    index = (offset + weighted) % 11
    if prefix == "M":
        return _NRIC_TABLE_M[10 - index]
    if prefix in ("S", "T"):
        return _NRIC_TABLE_ST[index]
    if prefix in ("F", "G"):
        return _NRIC_TABLE_FG[index]
    return None


def _extract_nric_candidates(fields: dict[str, Any]) -> dict[str, list[str]]:
    """Every NRIC/FIN-shaped value in the document, nested ones included (a shareholder table's
    identification_no), each once, with every field path it appears at (top-level fields first)."""
    candidates: dict[str, list[str]] = {}
    keys: dict[str, str] = {}
    for value in text_values(fields):
        keys.setdefault(value.path, value.key)
        for match in _NRIC_RE.finditer(value.text):
            paths = candidates.setdefault(match.group(0).upper(), [])
            if value.path not in paths:
                paths.append(value.path)
    # Report a number at the field that holds it (subscriber_2_id) before free text that mentions it (notes).
    for paths in candidates.values():
        paths.sort(key=lambda path: not _ID_KEY_RE.search(keys[path]))
    return candidates


@check(code="ID.NRIC_CHECKSUM")
def _check_nric_checksum(*, fields: dict[str, Any]) -> CheckResult:
    """Any value anywhere in the document that looks like an NRIC or FIN must carry a valid check letter.
    The same number printed in several places is checked, and reported, once."""
    candidates = _extract_nric_candidates(fields)
    if not candidates:
        return [], skipped(reason="no NRIC-shaped value in any field")

    findings = []
    for value, paths in sorted(candidates.items()):
        prefix, digits, letter = value[0], value[1:8], value[8]
        expected = _nric_expected_letter(prefix, digits)
        if expected and expected != letter:
            findings.append(_finding(
                "ID.NRIC_CHECKSUM", "impossible",
                f"{value} in {describe_path(fields, paths[0])} has an invalid check letter — expected {expected}, "
                f"got {letter}{also_printed_in(fields, paths[1:])}",
                field=paths[0], stated=value, expected=f"{value[:8]}{expected}", also_fields=paths[1:],
            ))
    return findings, None


_UEN_RE = re.compile(r"^(\d{4})(\d{5}|-\d{4})[A-Z]$", re.IGNORECASE)
_UEN_BUSINESS_RE = re.compile(r"^\d{8}[A-Z]$", re.IGNORECASE)


@check(code="ID.UEN_FORMAT")
def _check_uen_format(*, uen: Any, today: date) -> CheckResult:
    """A UEN is 9 or 10 characters ending in a letter, with a plausible year prefix. Businesses registered before
    UEN existed use an 8-digit registration number instead, which this doesn't apply to."""
    uen = str(uen).strip().upper()
    if _UEN_BUSINESS_RE.match(uen):
        return [], None

    match = _UEN_RE.match(uen)
    if not match:
        return [_finding(
            "ID.UEN_FORMAT", "impossible", f"{uen} does not match any valid UEN format",
            field="uen", stated=uen,
        )], None

    year_prefix = int(match.group(1))
    if not 1965 <= year_prefix <= today.year:
        return [_finding(
            "ID.UEN_FORMAT", "impossible",
            f"{uen}'s year prefix ({year_prefix}) is not a plausible registration year",
            field="uen", stated=uen,
        )], None
    return [], None


def _is_forward_looking_date_field(name: str) -> bool:
    """Fields describing a scheduled point in the future by design (review, due, expiry, renewal, maturity).
    The "must not be after today" rule doesn't apply to these; they're still checked for being parseable."""
    return bool(re.search(
        r"expiry|expires|due|renewal|review|valid_until|maturity|next_|contract_end",
        name, re.IGNORECASE,
    ))


def _looks_like_date_field(name: str) -> bool:
    """Shared with date_anomalies.py: the name pattern for date fields. date_of_birth is left out because the
    NRIC and passport age checks cover it."""
    return bool(re.search(r"date|_at$|_on$", name, re.IGNORECASE)) and name != "date_of_birth"


def _looks_like_amount_field(name: str) -> bool:
    # Identifiers and labels that merely mention an amount word (tax_reference_number, payment_date, tax_type)
    # aren't amounts.
    if re.search(r"number|_no$|reference|_ref|_id$|code|date|_type$|name|period", name, re.IGNORECASE):
        return False
    return bool(re.search(r"amount|balance|pay$|_pay|capital|value|tax|eci|income|price|rate$", name, re.IGNORECASE))


@check(code="GEN.AMOUNT_PARSE")
def _check_amount_parse(*, fields: dict[str, Any]) -> CheckResult:
    """Every amount-like field must parse as a number. A value that doesn't is usually an OCR artefact, so it's
    marked for review rather than treated as a confident anomaly."""
    amount_fields = {
        name: value for name, value in fields.items()
        if isinstance(value, (str, int, float)) and _looks_like_amount_field(name)
    }
    if not amount_fields:
        return [], skipped(reason="no amount-like fields")

    findings = []
    for name, raw in amount_fields.items():
        if _as_number(raw) is None:
            findings.append(NumericalFinding(
                check_code="GEN.AMOUNT_PARSE", severity="signal", field=name, stated=str(raw),
                needs_review=True,
                summary=f"{name} ({raw!r}) does not parse as a numeric amount",
            ))
    return findings, None


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
    """ICAO 9303 TD3 check digit: weights 7, 3, 1 repeating."""
    weights = (7, 3, 1)
    return sum(_mrz_char_value(c) * weights[i % 3] for i, c in enumerate(s)) % 10


_TD3_LINE_LENGTH = 44
# How far a line may be off 44 and still be a miscounted run of filler.
_MRZ_FILLER_SLACK = 2
_MRZ_FILLER_RUN_RE = re.compile(r"<{3,}")


def _fit_mrz_filler(line: str) -> str:
    """
    A line one or two characters off 44 whose last run of "<" filler is
    long: the run was miscounted while transcribing (about 25 identical
    characters after a short name), so it is resized to make the line 44.
    Filler is padding that no check digit covers, so this changes nothing
    the checks rely on; letters and digits are never touched, and a line
    further off is left for ID.MRZ_FORMAT to report.
    """
    gap = _TD3_LINE_LENGTH - len(line)
    runs = list(_MRZ_FILLER_RUN_RE.finditer(line))
    if gap == 0 or abs(gap) > _MRZ_FILLER_SLACK or not runs:
        return line
    last = runs[-1]
    width = len(last.group(0)) + gap
    if width < 1:
        return line
    return line[: last.start()] + "<" * width + line[last.end():]


def _mrz_lines(raw: Any) -> list[str]:
    """The two TD3 lines, however extraction separated them. A real MRZ has no whitespace, so any space or newline
    is a line break. 88 characters once whitespace is removed means a valid two-line MRZ. A line whose only fault
    is a miscounted run of filler is set back to 44 (see _fit_mrz_filler)."""
    compact = "".join(str(raw).split())
    if len(compact) == 88:
        return [compact[:44], compact[44:]]
    lines = [ln.strip() for ln in str(raw).strip().splitlines() if ln.strip()]
    return [_fit_mrz_filler(ln) for ln in lines] if len(lines) == 2 else lines


# Letters and digits OCR reads one for the other: the printed national ID
# "S8934521I" comes back as "S89345211". Identifiers are compared with these
# collapsed on both sides, so only a genuinely different number mismatches.
_OCR_CONFUSABLE = str.maketrans({"O": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2"})


def _same_identifier(a: str, b: str) -> bool:
    return a.upper().translate(_OCR_CONFUSABLE) == b.upper().translate(_OCR_CONFUSABLE)


def _mrz_field(line2: str, start: int, end: int) -> str:
    return line2[start:end].replace("<", "")


def _mrz_date(yymmdd: str) -> date | None:
    if len(yymmdd) != 6 or not yymmdd.isdigit():
        return None
    yy, mm, dd = int(yymmdd[0:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    # TD3 has no century marker, so take the interpretation nearest to today.
    for century in (1900, 2000):
        try:
            candidate = date(century + yy, mm, dd)
        except ValueError:
            continue
        if abs((candidate - date.today()).days) < 366 * 60:
            return candidate
    return None


def _td3_line2(mrz: Any) -> str | None:
    """The second TD3 line, or None if the MRZ isn't two lines of 44 characters."""
    lines = _mrz_lines(mrz)
    if len(lines) != 2 or any(len(ln) != 44 for ln in lines):
        return None
    return lines[1]


@check(code="ID.MRZ_FORMAT", applies=("passport",))
def _check_mrz_format(*, mrz: Any) -> CheckResult:
    """The TD3 machine-readable zone is two lines of 44 characters each."""
    lines = _mrz_lines(mrz)
    if len(lines) != 2 or any(len(ln) != 44 for ln in lines):
        shapes = ", ".join(f"{len(ln)} chars" for ln in lines) or "no lines"
        return [_finding(
            "ID.MRZ_FORMAT", "impossible",
            f"MRZ must be two lines of 44 characters each; got {len(lines)} line(s) ({shapes})",
            field="mrz", stated=str(mrz)[:100],
        )], None
    return [], None


@check(code="ID.MRZ_CHECKDIGITS", applies=("passport",))
def _check_mrz_checkdigits(*, mrz: Any) -> CheckResult:
    """All five TD3 check digits must be valid."""
    line2 = _td3_line2(mrz)
    if line2 is None:
        return [], skipped(reason="MRZ is not two 44-character lines")

    fields = (
        ("passport number", line2[0:9], line2[9]),
        ("date of birth", line2[13:19], line2[19]),
        ("expiry date", line2[21:27], line2[27]),
        ("personal number", line2[28:42], line2[42]),
    )
    findings = []
    for label, value, digit in fields:
        if value.replace("<", "") == "" and label == "personal number":
            continue  # optional field; an all-filler value is valid with check digit 0 too
        expected = _mrz_check_digit(value)
        if str(expected) != digit:
            findings.append(_finding(
                "ID.MRZ_CHECKDIGITS", "impossible",
                f"MRZ check digit for {label} is invalid (expected {expected}, got {digit})",
                field="mrz",
            ))

    composite_input = line2[0:10] + line2[13:20] + line2[21:43]
    composite_expected = _mrz_check_digit(composite_input)
    if str(composite_expected) != line2[43]:
        findings.append(_finding(
            "ID.MRZ_CHECKDIGITS", "impossible",
            f"MRZ composite check digit is invalid (expected {composite_expected}, got {line2[43]})",
            field="mrz",
        ))
    return findings, None


@check(code="ID.MRZ_VS_VIZ", applies=("passport",))
def _check_mrz_vs_viz(
    *, mrz: Any, date_of_birth: Any = None, date_of_expiry: Any = None, nric_number: Any = None,
    passport_number: Any = None,
) -> CheckResult:
    """MRZ contents must match the printed fields: passport number, date of birth, expiry and national ID."""
    line2 = _td3_line2(mrz)
    if line2 is None:
        return [], skipped(reason="MRZ is not two 44-character lines")

    findings = []
    mrz_passport_no = _mrz_field(line2, 0, 9)
    stated_passport_no = str(passport_number or "").replace(" ", "").upper()
    if stated_passport_no and mrz_passport_no and not _same_identifier(mrz_passport_no, stated_passport_no):
        findings.append(_finding(
            "ID.MRZ_VS_VIZ", "inconsistent",
            f"MRZ passport number ({mrz_passport_no}) does not match the printed passport number ({stated_passport_no})",
            field="passport_number", stated=stated_passport_no, expected=mrz_passport_no,
        ))

    mrz_dob = _mrz_date(line2[13:19])
    stated_dob = _parse_field_date(date_of_birth)
    if mrz_dob and stated_dob and mrz_dob != stated_dob:
        findings.append(_finding(
            "ID.MRZ_VS_VIZ", "inconsistent",
            f"MRZ date of birth ({mrz_dob.isoformat()}) does not match the printed date of birth "
            f"({stated_dob.isoformat()})",
            field="date_of_birth", stated=stated_dob.isoformat(), expected=mrz_dob.isoformat(),
        ))

    mrz_expiry = _mrz_date(line2[21:27])
    stated_expiry = _parse_field_date(date_of_expiry)
    if mrz_expiry and stated_expiry and mrz_expiry != stated_expiry:
        findings.append(_finding(
            "ID.MRZ_VS_VIZ", "inconsistent",
            f"MRZ expiry date ({mrz_expiry.isoformat()}) does not match the printed expiry date "
            f"({stated_expiry.isoformat()})",
            field="date_of_expiry", stated=stated_expiry.isoformat(), expected=mrz_expiry.isoformat(),
        ))

    mrz_personal_no = _mrz_field(line2, 28, 42)
    stated_nric = str(nric_number or "").strip().upper()
    if stated_nric and mrz_personal_no and not _same_identifier(mrz_personal_no, stated_nric):
        findings.append(_finding(
            "ID.MRZ_VS_VIZ", "inconsistent",
            f"MRZ personal number ({mrz_personal_no}) does not match the printed national ID number ({stated_nric})",
            field="nric_number", stated=stated_nric, expected=mrz_personal_no,
        ))
    return findings, None


# ---------------------------------------------------------------------------
# Certificate of incumbency (ACRA business profile)
# ---------------------------------------------------------------------------


@check(code="ACRA.SHARE_SUM", applies=("certificate_of_incumbency",))
def _check_acra_share_sum(*, shareholders: Any, issued_shares: Num) -> CheckResult:
    """Shareholders' shares must add up to issued shares."""
    shares = [_field_number(r, "shares") for r in _rows(shareholders)]
    numeric_shares = [s for s in shares if s is not None]
    if not shares or len(numeric_shares) != len(shares):
        return [], skipped(reason="shareholder share counts not all readable")
    total = sum(numeric_shares)
    if abs(total - issued_shares) > _MONEY_TOLERANCE:
        return [_finding(
            "ACRA.SHARE_SUM", "impossible",
            f"Shareholders' shares sum to {total:,.0f}, but issued shares states {issued_shares:,.0f}",
            field="issued_shares", stated=f"{issued_shares:,.0f}", expected=f"{total:,.0f}",
        )], None
    return [], None


def _capital_per_share(code: str, share_capital: float, issued_shares: float) -> CheckResult:
    """Share capital divided by shares must be a plausible per-share amount."""
    if not issued_shares:
        return [], skipped(reason="issued shares is zero")
    per_share = share_capital / issued_shares
    if not 0.01 <= per_share <= 1_000_000:
        return [_finding(
            code, "inconsistent",
            f"Share capital {_money(share_capital)} / {issued_shares:,.0f} shares implies {per_share:,.4f} per share, "
            "which is not internally consistent with a par-value or no-par-value structure",
            field="share_capital", stated=_money(share_capital),
        )], None
    return [], None


@check(code="ACRA.CAPITAL_PER_SHARE", applies=("certificate_of_incumbency",))
def _check_acra_capital_per_share(*, share_capital: Num, issued_shares: Num) -> CheckResult:
    """Share capital per share must be plausible."""
    return _capital_per_share("ACRA.CAPITAL_PER_SHARE", share_capital, issued_shares)


@check(code="ACRA.OFFICERS", applies=("certificate_of_incumbency",))
def _check_acra_officers(*, officers: Any) -> CheckResult:
    """At least one director and one secretary must be listed among the officers. The date-logic checks for
    officers live in date_anomalies.py."""
    rows = _rows(officers)
    findings = []
    if not any("director" in officer_role(o).lower() for o in rows):
        findings.append(_finding("ACRA.OFFICERS", "inconsistent", "No director is listed among the officers",
                                 field="officers"))
    if not any("secretary" in officer_role(o).lower() for o in rows):
        findings.append(_finding("ACRA.OFFICERS", "inconsistent", "No secretary is listed among the officers",
                                 field="officers"))
    return findings, None


# ---------------------------------------------------------------------------
# Company constitution
# ---------------------------------------------------------------------------


@check(code="CONST.SUBSCRIBER_SUM", applies=("company_constitution",))
def _check_const_subscriber_sum(*, subscribers: Any, issued_shares: Num) -> CheckResult:
    """Subscribers' shares must add up to issued shares. Only run when every subscriber has a share count: a
    partial list would always fail for an innocent reason."""
    subscriber_rows = _rows(subscribers)
    shares = [_field_number(r, "shares") for r in subscriber_rows]
    numeric_shares = [s for s in shares if s is not None]
    if not subscriber_rows or len(numeric_shares) != len(shares):
        return [], skipped(reason="subscriber share counts incomplete")
    total = sum(numeric_shares)
    if abs(total - issued_shares) > _MONEY_TOLERANCE:
        return [_finding(
            "CONST.SUBSCRIBER_SUM", "impossible",
            f"Subscriber shareholdings sum to {total:,.0f}, but the constitution states {issued_shares:,.0f} "
            "issued shares",
            field="issued_shares", stated=f"{issued_shares:,.0f}", expected=f"{total:,.0f}",
        )], None
    return [], None


@check(code="CONST.CAPITAL_PER_SHARE", applies=("company_constitution",))
def _check_const_capital_per_share(*, share_capital: Num, issued_shares: Num) -> CheckResult:
    """Share capital per share must be plausible."""
    return _capital_per_share("CONST.CAPITAL_PER_SHARE", share_capital, issued_shares)


@check(code="CONST.QUORUM", applies=("company_constitution",))
def _check_const_quorum(*, subscribers: Any, quorum: Num) -> CheckResult:
    """Quorum for general meetings must not exceed the number of subscribers."""
    count = len(_rows(subscribers))
    if count == 0:
        return [], skipped(reason="no subscribers listed")
    if quorum > count:
        return [_finding(
            "CONST.QUORUM", "inconsistent",
            f"Quorum ({quorum:.0f}) is larger than the number of subscribers ({count})",
            field="quorum", stated=f"{quorum:.0f}", expected=f"<= {count}",
        )], None
    return [], None


# ---------------------------------------------------------------------------
# FATCA/CRS declaration
# ---------------------------------------------------------------------------


@check(code="FATCA.TIN_OR_REASON", applies=("fatca_crs_declaration",))
def _check_fatca_tin_or_reason(*, tax_residences: Any) -> CheckResult:
    """Each tax residence row must have either a TIN, or exactly one reason code for not having one."""
    findings = []
    for i, row in enumerate(_rows(tax_residences)):
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
                f"Tax residence {i + 1} has no TIN and {reason_count} reason code(s) — exactly one is required",
                field=f"tax_residences[{i}]",
            ))
    return findings, None


@check(code="FATCA.TIN_FORMAT", applies=("fatca_crs_declaration",))
def _check_fatca_tin_format(*, tax_residences: Any) -> CheckResult:
    """A Singapore TIN must be in UEN format."""
    findings = []
    for i, row in enumerate(_rows(tax_residences)):
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


@check(code="FATCA.ENTITY_TYPE", applies=("fatca_crs_declaration",))
def _check_fatca_entity_type(*, entity_type: Any, controlling_persons: Any = None) -> CheckResult:
    """Exactly one entity type is selected. A passive or investment type (D or M) requires the controlling persons
    section."""
    types = entity_type if isinstance(entity_type, list) else [entity_type]
    findings = []
    if len(types) != 1:
        findings.append(_finding(
            "FATCA.ENTITY_TYPE", "inconsistent",
            f"{len(types)} entity type(s) selected; exactly one is required",
            field="entity_type",
        ))
    elif str(types[0]).strip().upper() in ("D", "M"):
        if not _rows(controlling_persons):
            findings.append(_finding(
                "FATCA.ENTITY_TYPE", "inconsistent",
                f"Entity type {types[0]} requires the controlling persons section, which is empty",
                field="controlling_persons",
            ))
    return findings, None


@check(code="FATCA.US_PERSON", applies=("fatca_crs_declaration",))
def _check_fatca_us_person(*, specified_us_person: Any, us_tin: Any = None) -> CheckResult:
    """A 'No' answer must come with no US TIN, and a 'Yes' with one."""
    answer = str(specified_us_person).strip().lower()
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
