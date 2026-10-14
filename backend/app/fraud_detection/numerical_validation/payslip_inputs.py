"""Payslip CPF inputs, read from the line items when the payslip doesn't state them on their own.

Many payslips have no "Employee CPF" or "Ordinary wages" box: CPF is just a deduction line
("CPF", "Employee contribution") and the wages are the earnings lines. The CPF checks use
these when the dedicated field wasn't extracted.
"""

from __future__ import annotations

import re
from typing import Any

from .common import _as_number, _rows

# A deduction line that is the employee's CPF. "Contribution" alone also names the self-help
# group funds (CDAC, SINDA, MBMF, ECF), so those are excluded below.
_CPF_LINE_RE = re.compile(r"\bcpf\b|provident fund|\b(?:employee'?s?|ee)\s+(?:share|contributions?)\b", re.IGNORECASE)
# Lines that aren't the employee's CPF: the employer's share (shown for information, not
# deducted) and the self-help group contributions.
_NOT_EMPLOYEE_CPF_RE = re.compile(r"employer|company|\ber\b|cdac|sinda|mbmf|\becf\b|self[- ]help|\bshg\b", re.IGNORECASE)
# Earnings that are additional wages, not ordinary wages, for CPF.
_ADDITIONAL_WAGE_RE = re.compile(r"bonus|\baws\b|13th|incentive|commission|back ?pay|arrears|leave encash", re.IGNORECASE)


def _label(row: dict) -> str:
    return " ".join(str(row.get(key) or "") for key in ("description", "label"))


def employee_cpf_from_deductions(deductions_items: Any) -> float | None:
    """The employee's CPF from the deduction lines.

    Sums every employee CPF line (a payslip can split ordinary and additional wage CPF into
    two lines). Deductions may be extracted as negatives.
    """
    amounts = []
    for row in _rows(deductions_items):
        label, amount = _label(row), _as_number(row.get("amount"))
        if amount is not None and _CPF_LINE_RE.search(label) and not _NOT_EMPLOYEE_CPF_RE.search(label):
            amounts.append(abs(amount))
    return round(sum(amounts), 2) if amounts else None


def ordinary_wages_from_earnings(earnings_items: Any) -> float | None:
    """The earnings lines' total, when none of them is an additional wage (bonus, commission...)."""
    rows = _rows(earnings_items)
    amounts = [_as_number(row.get("amount")) for row in rows]
    if not rows or any(a is None for a in amounts) or any(_ADDITIONAL_WAGE_RE.search(_label(r)) for r in rows):
        return None
    return round(sum(a for a in amounts if a is not None), 2)


def employee_cpf_input(employee_cpf: float | None, deductions_items: Any) -> tuple[float | None, str]:
    """The employee's CPF and the field it came from."""
    if employee_cpf is not None:
        return employee_cpf, "employee_cpf"
    return employee_cpf_from_deductions(deductions_items), "deductions_items"


def ordinary_wages_input(ordinary_wages: float | None, earnings_items: Any) -> tuple[float | None, str]:
    """Ordinary wages and the field they came from."""
    if ordinary_wages is not None:
        return ordinary_wages, "ordinary_wages"
    return ordinary_wages_from_earnings(earnings_items), "earnings_items"
