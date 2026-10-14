"""Payslip CPF contributions against CPF Board's rate tables and wage ceilings (sg_reference/cpf.py)."""

from __future__ import annotations

from typing import Any

from ...numerical_validation.payslip_inputs import employee_cpf_input, ordinary_wages_input
from ...numerical_validation.registry import Num, check
from ..sg_reference.cpf import band_for_age, bands_in_force, ceiling_on, contribution
from ...numerical_validation.common import (
    CheckResult,
    _finding,
    _money,
    _MONEY_TOLERANCE,
    _parse_field_date,
    _skip_reference,
    skipped,
)


# CPF rounds to whole dollars (total to the nearest dollar, employee share down), so a
# payslip can be a dollar off the exact percentage.
_DOLLAR_TOLERANCE = 1.0


@check(code="PAY.CPF_RATE", applies=("payslip",), group="external", source="CPF Board contribution rate tables")
def _check_cpf_rate(
    *, ordinary_wages: Num | None = None, earnings_items: Any = None,
    employee_cpf: Num | None = None, deductions_items: Any = None,
    pay_date: Any = None, pay_period_end: Any = None,
    employer_cpf: Num | None = None, date_of_birth: Any = None, additional_wages: Num | None = None,
) -> CheckResult:
    """Employee (and employer) CPF must match CPF Board's rate for the wage and age band on the pay date."""
    employee_cpf, source_field = employee_cpf_input(employee_cpf, deductions_items)
    if employee_cpf is None:
        return [], skipped(reason="Required field(s) not extracted: employee_cpf (or a CPF line in deductions_items)")
    ordinary_wages, _ = ordinary_wages_input(ordinary_wages, earnings_items)
    if ordinary_wages is None:
        return [], skipped(reason="Required field(s) not extracted: ordinary_wages (or earnings_items without a bonus)")
    when = _parse_field_date(pay_date) or _parse_field_date(pay_period_end)
    if when is None:
        return [], skipped(reason="Required field(s) not extracted: pay_date or pay_period_end")
    if additional_wages:
        return [], skipped(reason="Additional wages (bonus) are present; only ordinary-wage months are modelled")
    bands = bands_in_force(when)
    ceiling = ceiling_on(when)
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


@check(code="PAY.CPF_CEILING", applies=("payslip",), group="external", source="CPF Board ordinary wage ceilings")
def _check_cpf_ceiling(
    *, pay_date: Any, employee_cpf: Num | None = None, deductions_items: Any = None,
    ordinary_wages: Num | None = None, earnings_items: Any = None,
) -> CheckResult:
    """Employee CPF must not exceed 20% of the lower of ordinary wages and the ceiling for the pay year."""
    employee_cpf, cpf_field = employee_cpf_input(employee_cpf, deductions_items)
    ordinary_wages, _ = ordinary_wages_input(ordinary_wages, earnings_items)
    if employee_cpf is None or ordinary_wages is None:
        return [], skipped(reason="Required field(s) not extracted: employee_cpf and ordinary_wages "
                                  "(or a CPF deduction line and earnings lines)")
    pay_date_val = _parse_field_date(pay_date)
    if pay_date_val is None:
        return [], skipped(reason="pay_date could not be read as a date")
    ceiling = ceiling_on(pay_date_val)
    if ceiling is None:
        return [], _skip_reference("PAY.CPF_CEILING", f"cpf_ordinary_wage_ceiling[{pay_date_val.isoformat()}]")
    max_contribution = 0.20 * min(ordinary_wages, ceiling)
    if employee_cpf > max_contribution + _MONEY_TOLERANCE:
        return [_finding(
            "PAY.CPF_CEILING", "impossible",
            f"Employee CPF contribution {_money(employee_cpf)} exceeds 20% of the lower of ordinary "
            f"wages and the ceiling ({_money(max_contribution)})",
            field=cpf_field, stated=_money(employee_cpf), expected=f"<= {_money(max_contribution)}",
        )], None
    return [], None

