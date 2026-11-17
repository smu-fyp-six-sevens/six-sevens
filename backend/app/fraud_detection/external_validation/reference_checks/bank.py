"""A bank statement's identifiers and account number against the bank's own published details (sg_reference/banks.py)."""

from __future__ import annotations

import re
from typing import Any

from ....identifiers import alnum
from ...numerical_validation.registry import check
from ..sg_reference.banks import bank_reference
from ...numerical_validation.common import (
    CheckResult,
    new_finding,
    _skip_reference,
    skipped,
)


# What each identifier looks like, after dropping spaces and punctuation. A value of another shape (a phone
# number, an address) is a field bound to the wrong role, not evidence about the bank, so it's skipped.
_UEN_SHAPE = r"\d{8,9}[A-Z]|[TSR]\d{2}[A-Z]{2}\d{4}[A-Z]"
_IDENTIFIER_SHAPES = {
    # UEN: business (8 digits + letter), local company (9 digits + letter), other entities (T08LL0001A).
    "registration_no": re.compile(rf"^(?:{_UEN_SHAPE})$"),
    # IRAS's pre-UEN GST number (MR-8500180-3, M2-0012345-6), or the UEN for newer registrants.
    "gst_no": re.compile(rf"^(?:M[A-Z0-9]\d{{7}}[A-Z0-9]|{_UEN_SHAPE})$"),
    # SWIFT BIC: 4-letter bank code, 2-letter country, 2-character location, optional 3-character branch.
    "bic": re.compile(r"^[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?$"),
}


# A label printed in front of the identifier, e.g. "Co. Reg. No.", "GST Registration No", "SWIFT Code", "UEN".
_IDENTIFIER_LABEL_RE = re.compile(
    r"^\s*(?:co\.?\s*reg(?:istration)?|company\s+reg(?:istration)?|gst\s*reg(?:istration)?|swift|bic|uen)"
    r"(?:[\s.]*(?:no|number|code))?[\s.:]*",
    re.IGNORECASE,
)


def _identifier_in(stated: Any, shape: re.Pattern[str]) -> str | None:
    """The identifier in a printed value, spaces and punctuation dropped: the whole value ("MR 8500180 3"),
    the part after a colon, or the value without its label ("Co. Reg. No. 196800306E"). Never a single word
    picked out of other text: an ordinary word such as "OVERSEAS" has the shape of a BIC."""
    text = str(stated)
    candidates = [text, text.rsplit(":", 1)[-1], _IDENTIFIER_LABEL_RE.sub("", text, count=1)]
    for candidate in candidates:
        normalised = alnum(candidate)
        if shape.match(normalised):
            return normalised
    return None


@check(
    code="BS.ACCOUNT_NUMBER_FORMAT", applies=("bank_statement",), group="external",
    source="the bank's published account number format",
)
def _check_account_number_format(*, bank_name: Any, account_number: Any) -> CheckResult:
    """The account number has the digit count the named bank publishes for its accounts."""
    reference = bank_reference(bank_name)
    lengths = reference.get("account_digits") if reference else None
    if not lengths:
        return [], skipped(reason=f"No published account number format configured for {bank_name}")
    digits = re.sub(r"\D", "", str(account_number))
    if len(digits) in lengths:
        return [], None
    allowed = " or ".join(str(n) for n in lengths)
    return [new_finding(
        "BS.ACCOUNT_NUMBER_FORMAT", "inconsistent",
        f"Account number {account_number} has {len(digits)} digits, but {bank_name} account numbers have {allowed}",
        field="account_number", stated=str(account_number), expected=f"{allowed} digits",
    )], None


@check(
    code="BS.BANK_IDENTIFIERS", applies=("bank_statement",), group="external",
    source="the bank's published identifiers",
)
def _check_bank_identifiers(
    *, bank_name: Any, bank_registration_no: Any = None, bank_gst_no: Any = None, bic_code: Any = None,
) -> CheckResult:
    """The bank's registration number, GST number and SWIFT BIC printed on the statement must match the
    reference table for the named bank. A mismatch is a fabricated or mistyped identifier."""
    reference = bank_reference(bank_name)
    if reference is None:
        return [], _skip_reference("BS.BANK_IDENTIFIERS", f"bank_identifiers[{bank_name!r}]")
    findings = []
    ran_any = False
    wrong_shape = []
    for field_name, stated, key, label, length in (
        ("bank_registration_no", bank_registration_no, "registration_no", "registration number", None),
        ("bank_gst_no", bank_gst_no, "gst_no", "GST registration number", None),
        # BIC: compare bank + country + location only; the optional 3-letter branch suffix doesn't change the institution.
        ("bic_code", bic_code, "bic", "SWIFT BIC", 8),
    ):
        expected = reference.get(key)
        if not stated or not expected:
            continue
        stated_full = _identifier_in(stated, _IDENTIFIER_SHAPES[key])
        if stated_full is None:
            wrong_shape.append(f"{field_name} ({str(stated)[:40]!r} isn't shaped like a {label})")
            continue
        ran_any = True
        stated_norm = stated_full[:length]
        expected_norm = alnum(str(expected))[:length]
        if stated_norm != expected_norm:
            findings.append(new_finding(
                "BS.BANK_IDENTIFIERS", "impossible",
                f"The statement prints {bank_name}'s {label} as {stated}, but the bank's actual {label} is {expected}",
                field=field_name, stated=str(stated), expected=str(expected),
            ))
    if not ran_any:
        reason = "no printed identifier has a reference value to compare against"
        if wrong_shape:
            reason = "not compared: " + "; ".join(wrong_shape)
        return [], skipped(reason=reason)
    return findings, None


# Verified against real extracted transaction descriptions (e.g. "4933-XXXX-XXXX-6411"): 4 digits, two masked
# groups, 4 digits. The second alternative covers a straight run of mask characters ending in the last 4 digits.
