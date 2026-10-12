"""Bank statement account number against the issuing bank's published format."""

from __future__ import annotations

import re
from typing import Any

from .common import CheckResult, _bank_reference, _finding, skipped
from .registry import check


@check(code="BS.ACCOUNT_NUMBER_FORMAT", applies=("bank_statement",))
def _check_account_number_format(*, bank_name: Any, account_number: Any) -> CheckResult:
    """The account number has the digit count the named bank publishes for its accounts."""
    reference = _bank_reference(bank_name)
    lengths = reference.get("account_digits") if reference else None
    if not lengths:
        return [], skipped(reason=f"No published account number format configured for {bank_name}")
    digits = re.sub(r"\D", "", str(account_number))
    if len(digits) in lengths:
        return [], None
    allowed = " or ".join(str(n) for n in lengths)
    return [_finding(
        "BS.ACCOUNT_NUMBER_FORMAT", "inconsistent",
        f"Account number {account_number} has {len(digits)} digits, but {bank_name} account numbers have {allowed}",
        field="account_number", stated=str(account_number), expected=f"{allowed} digits",
    )], None
