"""CPF contribution rates for Singapore Citizens and 3rd-year-onwards PRs, private sector.

Source: CPF Board's "CPF Contribution Rate Table from 1 January <year>" (Table 1):
  2024 https://www.cpf.gov.sg/content/dam/web/employer/employer-obligations/documents/CPF_contribution_rates_from_1_Jan_2024.pdf
  2025 https://www.cpf.gov.sg/content/dam/web/employer/employer-obligations/documents/CPF_contribution_rates_from_1_Jan_2025.pdf
  2026 https://www.cpf.gov.sg/content/dam/web/employer/employer-obligations/documents/CPFcontributionratesfrom1Jan2026.pdf

1st and 2nd-year PRs pay lower graduated rates (separate tables), so a contribution
below every band here can be genuine; one above every band cannot.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class CpfBand:
    label: str
    above_age: int | None  # the band applies once the employee is above this age (None = 55 & below)
    total_pct: float  # total contribution on ordinary wages above $750
    employee_pct: float  # employee's share on ordinary wages above $750
    low_wage_pct: float  # total contribution on total wages of $50-$750 (employer pays all of it up to $500)
    phase_in: float  # employee's share on wages of $500-$750 is phase_in x (wages - 500)


def _bands(rates: list[tuple[float, float, float, float]]) -> tuple[CpfBand, ...]:
    labels = (("55 & below", None), ("above 55 to 60", 55), ("above 60 to 65", 60),
              ("above 65 to 70", 65), ("above 70", 70))
    return tuple(CpfBand(label, age, *r) for (label, age), r in zip(labels, rates))


# Effective date -> bands, in age order: (total %, employee %, low-wage %, phase-in factor).
CPF_RATE_TABLES: dict[str, tuple[CpfBand, ...]] = {
    "2024-01-01": _bands([(0.37, 0.20, 0.17, 0.6), (0.31, 0.16, 0.15, 0.48), (0.22, 0.105, 0.115, 0.315),
                          (0.165, 0.075, 0.09, 0.225), (0.125, 0.05, 0.075, 0.15)]),
    "2025-01-01": _bands([(0.37, 0.20, 0.17, 0.6), (0.325, 0.17, 0.155, 0.51), (0.235, 0.115, 0.12, 0.345),
                          (0.165, 0.075, 0.09, 0.225), (0.125, 0.05, 0.075, 0.15)]),
    "2026-01-01": _bands([(0.37, 0.20, 0.17, 0.6), (0.34, 0.18, 0.16, 0.54), (0.25, 0.125, 0.125, 0.375),
                          (0.165, 0.075, 0.09, 0.225), (0.125, 0.05, 0.075, 0.15)]),
}


def bands_in_force(on: date) -> tuple[CpfBand, ...] | None:
    applicable = [k for k in CPF_RATE_TABLES if date.fromisoformat(k) <= on]
    return CPF_RATE_TABLES[max(applicable)] if applicable else None


def _round_half_up(amount: float) -> int:
    # CPF rounds the total to the nearest dollar, 50 cents and above rounding up.
    return math.floor(amount + 0.5 + 1e-9)


def contribution(band: CpfBand, ordinary_wages: float, ceiling: float) -> tuple[int, int]:
    """(total, employee share) in whole dollars for a month with ordinary wages only.

    CPF's steps: total rounded to the nearest dollar, employee share rounded down,
    employer share is the difference.
    """
    if ordinary_wages <= 50:
        return 0, 0
    if ordinary_wages <= 500:
        return _round_half_up(band.low_wage_pct * ordinary_wages), 0
    if ordinary_wages <= 750:
        employee = band.phase_in * (ordinary_wages - 500)
        return _round_half_up(band.low_wage_pct * ordinary_wages + employee), math.floor(employee + 1e-9)
    capped = min(ordinary_wages, ceiling)
    return _round_half_up(band.total_pct * capped), math.floor(band.employee_pct * capped + 1e-9)


def band_for_age(bands: tuple[CpfBand, ...], date_of_birth: date, pay_date: date) -> CpfBand:
    """The band for an employee on a pay date.

    A new rate applies from the first day of the month after the month of the
    employee's 55th, 60th, 65th or 70th birthday.
    """
    def _passed(age: int) -> bool:
        year = date_of_birth.year + age
        month = date_of_birth.month
        return (year, month) < (pay_date.year, pay_date.month)

    chosen = bands[0]
    for band in bands[1:]:
        if band.above_age is not None and _passed(band.above_age):
            chosen = band
    return chosen
