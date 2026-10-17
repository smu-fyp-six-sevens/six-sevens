"""Payslip CPF contributions against CPF Board's age-band rate tables (sg_reference/cpf.py).

One assessment, three checks: each wrong contribution is reported once, by the check that
names what went wrong.

* fits a reading of the month's wages (payslip_inputs.wage_scenarios) at some age band: fine
* PAY.CPF_CEILING  more than CPF allows with the OW ceiling, but not more than it would
                   without it: the ceiling wasn't applied (impossible)
* PAY.CPF_BASE     at an age band's rate on basic pay alone, short of the full ordinary wages
                   (allowances, overtime) the contribution is due on (inconsistent)
* PAY.CPF_RATE     anything else: more than any band allows even uncapped (impossible), or a
                   contribution that matches no band (inconsistent)

Before the configured rate tables, only the most any employee can pay (20%) is known: the
ceiling and over-the-rate cases are still caught, the others are skipped.
"""

from __future__ import annotations

import functools
import json
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ...numerical_validation.common import CheckResult, _finding, _money, _parse_field_date, skipped
from ...numerical_validation.models import SkippedCheck
from ...numerical_validation.payslip_inputs import (
    WageScenario, basic_pay_input, employee_cpf_input, split_earnings, wage_scenarios,
)
from ...numerical_validation.registry import Num, check
from ..sg_reference.cpf import CpfBand, band_for_age, bands_in_force, ceiling_on, contribution

# CPF rounds to whole dollars (total to the nearest dollar, employee share down), so a
# payslip can be a dollar off the exact percentage.
_DOLLAR_TOLERANCE = 1.0
# For a date before the configured rate tables: the 55-and-below band, the highest rates in every
# table (employee 20%, total 37%), bounds what any employee can pay.
_HIGHEST_RATE_BAND = CpfBand("of any age", None, 0.37, 0.20, 0.17, 0.6)


@dataclass(frozen=True)
class PayslipCpfInputs:
    """Everything the CPF assessment reads from a payslip, passed explicitly. Its identity (equality and
    hash) is `key`, a canonical form of all of them, so one payslip's assessment is worked out once and
    shared by the three checks; the inputs themselves (which include lists) stay out of the hash."""
    key: str
    employee_cpf: float | None = field(compare=False)
    deductions_items: Any = field(compare=False, repr=False)
    ordinary_wages: float | None = field(compare=False)
    additional_wages: float | None = field(compare=False)
    earnings_items: Any = field(compare=False, repr=False)
    pay_date: Any = field(compare=False)
    pay_period_end: Any = field(compare=False)
    employer_cpf: float | None = field(compare=False)
    date_of_birth: Any = field(compare=False)
    basic_pay: float | None = field(compare=False)

    @classmethod
    def of(
        cls, *, employee_cpf: float | None, deductions_items: Any, ordinary_wages: float | None,
        additional_wages: float | None, earnings_items: Any, pay_date: Any, pay_period_end: Any,
        employer_cpf: float | None, date_of_birth: Any, basic_pay: float | None,
    ) -> "PayslipCpfInputs":
        values = dict(
            employee_cpf=employee_cpf, deductions_items=deductions_items, ordinary_wages=ordinary_wages,
            additional_wages=additional_wages, earnings_items=earnings_items, pay_date=pay_date,
            pay_period_end=pay_period_end, employer_cpf=employer_cpf, date_of_birth=date_of_birth, basic_pay=basic_pay,
        )
        return cls(key=json.dumps(values, sort_keys=True, default=str), **values)


@dataclass(frozen=True)
class CpfAssessment:
    outcome: str  # "fits", "over_ceiling", "basic_only", "over_rate", "mismatch" or "rates_unknown"
    employee_cpf: float
    source_field: str
    when: date
    ceiling: float
    bands: tuple[CpfBand, ...]
    age_known: bool
    main: WageScenario
    capped_max: int
    uncapped_max: int
    expected: dict[tuple[WageScenario, CpfBand], tuple[int, int]]
    lines_note: str
    basic_note: str

    @property
    def age_basis(self) -> str:
        return f"for an employee {self.bands[0].label}" if self.age_known else "for any age band"


def _wages_text(scenario: WageScenario) -> str:
    text = f"ordinary wages {_money(scenario.ordinary)}"
    return text + (f" and additional wages {_money(scenario.additional)}" if scenario.additional else "")


def _basic_only_band(employee_cpf: float, basic_pay: float | None, bands: tuple[CpfBand, ...]) -> CpfBand | None:
    """The age band whose employee rate on basic pay alone gives this contribution, if any."""
    if not basic_pay:
        return None
    return next((b for b in bands if abs(b.employee_pct * basic_pay - employee_cpf) <= _DOLLAR_TOLERANCE), None)


@functools.lru_cache(maxsize=64)
def assess_cpf(inputs: PayslipCpfInputs) -> CpfAssessment | SkippedCheck:
    """The CPF assessment of one payslip. Cached on the inputs: the three checks each ask for it, and it is
    worked out once. (A SkippedCheck is shared too; the runner copies it before stamping its check code.)"""
    employee_cpf, deductions_items = inputs.employee_cpf, inputs.deductions_items
    ordinary_wages, additional_wages, earnings_items = inputs.ordinary_wages, inputs.additional_wages, inputs.earnings_items
    pay_date, pay_period_end = inputs.pay_date, inputs.pay_period_end
    employer_cpf, date_of_birth = inputs.employer_cpf, inputs.date_of_birth
    basic_pay = basic_pay_input(inputs.basic_pay, earnings_items)
    stated, source_field = employee_cpf_input(employee_cpf, deductions_items)
    if stated is None:
        return skipped(reason="Required field(s) not extracted: employee_cpf (or a CPF line in deductions_items)")
    scenarios = wage_scenarios(ordinary_wages, additional_wages, earnings_items)
    if not scenarios:
        return skipped(reason="Required field(s) not extracted: ordinary_wages (or readable earnings_items)")
    when = _parse_field_date(pay_date) or _parse_field_date(pay_period_end)
    if when is None:
        return skipped(reason="Required field(s) not extracted: pay_date or pay_period_end")
    ceiling = ceiling_on(when)
    if ceiling is None:
        return skipped(reason=f"CPF ordinary wage ceiling not configured for {when.isoformat()}")
    in_force = bands_in_force(when)
    dob = _parse_field_date(date_of_birth)
    if in_force is None:
        # The ceiling table goes back further than the rate tables: the most any employee pays is
        # still known, so a contribution above it is caught; a lower one can't be judged.
        bands: tuple[CpfBand, ...] = (_HIGHEST_RATE_BAND,)
    else:
        bands = (band_for_age(in_force, dob, when),) if dob else in_force
    expected = {(s, b): contribution(b, s.ordinary, ceiling, s.additional) for s in scenarios for b in bands}
    capped_max = max(employee for _, employee in expected.values())
    uncapped_max = max(contribution(b, s.ordinary, math.inf, s.additional)[1] for s in scenarios for b in bands)

    def _fits(total: int, employee: int) -> bool:
        if abs(stated - employee) > _DOLLAR_TOLERANCE:
            return False
        return employer_cpf is None or abs(employer_cpf - (total - employee)) <= _DOLLAR_TOLERANCE

    basic_band = _basic_only_band(stated, basic_pay, bands)
    if stated > capped_max + _DOLLAR_TOLERANCE:
        outcome = "over_ceiling" if stated <= uncapped_max + _DOLLAR_TOLERANCE else "over_rate"
    elif in_force is None:
        outcome = "rates_unknown"
    elif any(_fits(total, employee) for total, employee in expected.values()):
        outcome = "fits"
    elif basic_band is not None and basic_pay is not None and basic_pay < scenarios[0].ordinary:
        outcome = "basic_only"
    else:
        outcome = "mismatch"

    split = split_earnings(earnings_items)
    return CpfAssessment(
        outcome=outcome, employee_cpf=stated, source_field=source_field, when=when, ceiling=ceiling,
        bands=bands, age_known=dob is not None, main=scenarios[0], capped_max=capped_max,
        uncapped_max=uncapped_max, expected=expected,
        lines_note=f" Earnings read as {split.describe()}." if split else "",
        basic_note=(
            f" It equals {basic_band.employee_pct:.1%} of basic pay ({_money(basic_pay)}) alone."
            if basic_band is not None and basic_pay else ""
        ),
    )


@check(code="PAY.CPF_CEILING", applies=("payslip",), group="external", source="CPF Board ordinary wage ceilings")
def _check_cpf_ceiling(
    *, ordinary_wages: Num | None = None, earnings_items: Any = None,
    employee_cpf: Num | None = None, deductions_items: Any = None,
    pay_date: Any = None, pay_period_end: Any = None,
    employer_cpf: Num | None = None, date_of_birth: Any = None, additional_wages: Num | None = None,
    basic_pay: Num | None = None,
) -> CheckResult:
    """The employee's CPF is due on ordinary wages only up to the OW ceiling for the pay date."""
    result = assess_cpf(PayslipCpfInputs.of(
        employee_cpf=employee_cpf, deductions_items=deductions_items, ordinary_wages=ordinary_wages,
        additional_wages=additional_wages, earnings_items=earnings_items, pay_date=pay_date,
        pay_period_end=pay_period_end, employer_cpf=employer_cpf, date_of_birth=date_of_birth, basic_pay=basic_pay,
    ))
    if isinstance(result, SkippedCheck):
        return [], result
    if result.outcome != "over_ceiling":
        return [], None
    return [_finding(
        "PAY.CPF_CEILING", "impossible",
        f"Employee CPF {_money(result.employee_cpf)} is more than CPF allows on {_wages_text(result.main)} "
        f"{result.age_basis} (at most {_money(result.capped_max)}): ordinary wages count only up to the "
        f"{_money(result.ceiling)} ceiling in force on {result.when.isoformat()}.{result.basic_note}{result.lines_note}",
        field=result.source_field, stated=_money(result.employee_cpf), expected=f"<= {_money(result.capped_max)}",
    )], None


@check(code="PAY.CPF_BASE", applies=("payslip",), group="external", source="CPF Board contribution rate tables")
def _check_cpf_base(
    *, ordinary_wages: Num | None = None, earnings_items: Any = None,
    employee_cpf: Num | None = None, deductions_items: Any = None,
    pay_date: Any = None, pay_period_end: Any = None,
    employer_cpf: Num | None = None, date_of_birth: Any = None, additional_wages: Num | None = None,
    basic_pay: Num | None = None,
) -> CheckResult:
    """CPF is due on all ordinary wages (basic pay plus allowances and overtime), not basic pay alone.
    Basic pay is the stated field, else the earnings lines labelled as basic pay."""
    result = assess_cpf(PayslipCpfInputs.of(
        employee_cpf=employee_cpf, deductions_items=deductions_items, ordinary_wages=ordinary_wages,
        additional_wages=additional_wages, earnings_items=earnings_items, pay_date=pay_date,
        pay_period_end=pay_period_end, employer_cpf=employer_cpf, date_of_birth=date_of_birth, basic_pay=basic_pay,
    ))
    if isinstance(result, SkippedCheck):
        return [], result
    if result.outcome == "rates_unknown":
        return [], skipped(reason=f"No CPF rate table configured for {result.when.isoformat()}; only the "
                                  "most an employee can pay was checked (PAY.CPF_CEILING)")
    if result.outcome != "basic_only":
        return [], None
    return [_finding(
        "PAY.CPF_BASE", "inconsistent",
        f"Employee CPF {_money(result.employee_cpf)} is not calculated on {_wages_text(result.main)}."
        f"{result.basic_note}{result.lines_note}",
        field=result.source_field, stated=_money(result.employee_cpf),
    )], None


@check(code="PAY.CPF_RATE", applies=("payslip",), group="external", source="CPF Board contribution rate tables")
def _check_cpf_rate(
    *, ordinary_wages: Num | None = None, earnings_items: Any = None,
    employee_cpf: Num | None = None, deductions_items: Any = None,
    pay_date: Any = None, pay_period_end: Any = None,
    employer_cpf: Num | None = None, date_of_birth: Any = None, additional_wages: Num | None = None,
    basic_pay: Num | None = None,
) -> CheckResult:
    """Employee (and employer) CPF must match CPF Board's rate for the month's wages and the age band on the
    pay date, under some reading of the wages the payslip allows."""
    result = assess_cpf(PayslipCpfInputs.of(
        employee_cpf=employee_cpf, deductions_items=deductions_items, ordinary_wages=ordinary_wages,
        additional_wages=additional_wages, earnings_items=earnings_items, pay_date=pay_date,
        pay_period_end=pay_period_end, employer_cpf=employer_cpf, date_of_birth=date_of_birth, basic_pay=basic_pay,
    ))
    if isinstance(result, SkippedCheck):
        return [], result
    if result.outcome == "rates_unknown":
        return [], skipped(reason=f"No CPF rate table configured for {result.when.isoformat()}; only the "
                                  "most an employee can pay was checked (PAY.CPF_CEILING)")
    if result.outcome == "over_rate":
        return [_finding(
            "PAY.CPF_RATE", "impossible",
            f"Employee CPF {_money(result.employee_cpf)} on {_wages_text(result.main)} is more than CPF's rate "
            f"allows {result.age_basis} on {result.when.isoformat()}, even before the wage ceiling (at most "
            f"{_money(result.uncapped_max)}).{result.lines_note}",
            field=result.source_field, stated=_money(result.employee_cpf), expected=f"<= {_money(result.capped_max)}",
        )], None
    if result.outcome != "mismatch":
        return [], None
    shares = "; ".join(
        f"{band.label}: employee {_money(employee)}, employer {_money(total - employee)}"
        for (scenario, band), (total, employee) in result.expected.items() if scenario == result.main
    )
    return [_finding(
        "PAY.CPF_RATE", "inconsistent",
        f"CPF on {_wages_text(result.main)} (employee {_money(result.employee_cpf)}"
        + (f", employer {_money(employer_cpf)}" if employer_cpf is not None else "")
        + f") doesn't match CPF's rates {result.age_basis} on {result.when.isoformat()} ({shares}).{result.lines_note}",
        field=result.source_field, stated=_money(result.employee_cpf),
        severity_note="1st and 2nd-year Singapore PRs pay lower graduated rates, which can explain a "
                      "contribution below every band shown",
    )], None
