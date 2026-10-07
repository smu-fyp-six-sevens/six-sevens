"""Payslip CPF contributions against CPF Board's age-band rate tables (sg_reference/cpf.py)."""

from __future__ import annotations

import re
from typing import Any

from ..sg_reference.cpf import band_for_age, bands_in_force, contribution
from .common import REFERENCE_DATA, CheckResult, _as_number, _finding, _in_force_on, _money, _parse_field_date, _rows, skipped
from .registry import Num, check

# CPF rounds to whole dollars (total to the nearest dollar, employee share down), so a
# payslip can be a dollar off the exact percentage.
_DOLLAR_TOLERANCE = 1.0

_CPF_LINE_RE = re.compile(r"\bcpf\b", re.IGNORECASE)
# The employer's share isn't deducted from pay; a payslip that lists it shows it for information.
_EMPLOYER_LINE_RE = re.compile(r"employer|company", re.IGNORECASE)


def _employee_cpf_from_deductions(deductions_items: Any) -> float | None:
    """The employee's CPF from the deduction lines, when the payslip only itemises it there.

    Sums every CPF line that isn't the employer's (a payslip can split ordinary and
    additional wage CPF into two lines). Deductions may be extracted as negatives.
    """
    amounts = []
    for row in _rows(deductions_items):
        label = " ".join(str(row.get(key) or "") for key in ("description", "label"))
        amount = _as_number(row.get("amount"))
        if amount is not None and _CPF_LINE_RE.search(label) and not _EMPLOYER_LINE_RE.search(label):
            amounts.append(abs(amount))
    return round(sum(amounts), 2) if amounts else None


@check(code="PAY.CPF_RATE", applies=("payslip",))
def _check_cpf_rate(
    *, ordinary_wages: Num, employee_cpf: Num | None = None, deductions_items: Any = None,
    pay_date: Any = None, pay_period_end: Any = None,
    employer_cpf: Num | None = None, date_of_birth: Any = None, additional_wages: Num | None = None,
) -> CheckResult:
    """Employee (and employer) CPF must match CPF Board's rate for the wage and age band on the pay date."""
    source_field = "employee_cpf"
    if employee_cpf is None:
        employee_cpf, source_field = _employee_cpf_from_deductions(deductions_items), "deductions_items"
    if employee_cpf is None:
        return [], skipped(reason="Required field(s) not extracted: employee_cpf (or a CPF line in deductions_items)")
    when = _parse_field_date(pay_date) or _parse_field_date(pay_period_end)
    if when is None:
        return [], skipped(reason="Required field(s) not extracted: pay_date or pay_period_end")
    if additional_wages:
        return [], skipped(reason="Additional wages (bonus) are present; only ordinary-wage months are modelled")
    bands = bands_in_force(when)
    ceiling = _in_force_on(REFERENCE_DATA["cpf_ordinary_wage_ceiling"], when)
    if bands is None or ceiling is None:
        return [], skipped(reason=f"No CPF rate table configured for {when.isoformat()}")

    dob = _parse_field_date(date_of_birth)
    candidates = (band_for_age(bands, dob, when),) if dob else bands
    expected = {band: contribution(band, ordinary_wages, ceiling) for band in candidates}

    def _fits(total: int, employee: int) -> bool:
        if abs(employee_cpf - employee) > _DOLLAR_TOLERANCE:
            return False
        return employer_cpf is None or abs(employer_cpf - (total - employee)) <= _DOLLAR_TOLERANCE

    if any(_fits(total, employee) for total, employee in expected.values()):
        return [], None

    highest = max(employee for _, employee in expected.values())
    shares = "; ".join(
        f"{band.label}: employee {_money(employee)}, employer {_money(total - employee)}"
        for band, (total, employee) in expected.items()
    )
    age_basis = f"for an employee {candidates[0].label}" if dob else "for any age band"
    if employee_cpf > highest + _DOLLAR_TOLERANCE:
        return [_finding(
            "PAY.CPF_RATE", "impossible",
            f"Employee CPF {_money(employee_cpf)} on ordinary wages {_money(ordinary_wages)} is more than CPF's "
            f"rate allows {age_basis} on {when.isoformat()} (at most {_money(highest)})",
            field=source_field, stated=_money(employee_cpf), expected=f"<= {_money(highest)}",
        )], None
    return [_finding(
        "PAY.CPF_RATE", "inconsistent",
        f"CPF on ordinary wages {_money(ordinary_wages)} (employee {_money(employee_cpf)}"
        + (f", employer {_money(employer_cpf)}" if employer_cpf is not None else "")
        + f") doesn't match CPF's rates {age_basis} on {when.isoformat()} ({shares})",
        field=source_field, stated=_money(employee_cpf),
        severity_note="1st and 2nd-year Singapore PRs pay lower graduated rates, which can explain a "
                      "contribution below every band shown",
    )], None
