"""Pure arithmetic behind the statement cast tools.

Callers pass plain numbers and roles. Expenses are taken by size, so the sign a
statement prints doesn't matter. Income lines add, and skipped lines are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .common import _MONEY_TOLERANCE, _materiality_note, _money
from .models import NumericalFinding

Role = Literal["income", "expense", "skip"]


@dataclass(frozen=True)
class CastLine:
    label: str
    role: Role
    amount: float

    @property
    def effect(self) -> float:
        if self.role == "income":
            return abs(self.amount)
        if self.role == "expense":
            return -abs(self.amount)
        return 0.0


def _finding(check_code: str, field: str, summary: str, *, stated: float, expected: float,
             needs_review: bool) -> NumericalFinding:
    return NumericalFinding(
        check_code=check_code,
        severity="impossible",
        field=field,
        stated=_money(stated),
        expected=_money(expected),
        needs_review=needs_review,
        severity_note=_materiality_note(expected - stated, expected),
        summary=summary,
    )


def profit_cast_finding(
    year: str, revenue: float, profit: float, lines: list[CastLine], *,
    profit_field: str, needs_review: bool,
) -> NumericalFinding | None:
    """Revenue plus each line's effect must equal profit for the year."""
    expected = revenue + sum(line.effect for line in lines)
    if abs(expected - profit) <= _MONEY_TOLERANCE:
        return None
    breakdown = " ".join(
        f"{'+' if line.effect > 0 else '-'} {line.label} {_money(abs(line.effect))}" for line in lines
    )
    return _finding(
        "FS.IS_CAST", profit_field,
        f"Income statement ({year}) does not cast: revenue {_money(revenue)} {breakdown} = "
        f"{_money(expected)}, but profit for the year shows {_money(profit)}",
        stated=profit, expected=expected, needs_review=needs_review,
    )


def section_total_finding(
    section: str, total_field: str, amounts: list[float], stated: float, *, needs_review: bool,
) -> NumericalFinding | None:
    """The rows of one balance sheet section must sum to its stated total."""
    total = sum(amounts)
    if abs(total - stated) <= _MONEY_TOLERANCE:
        return None
    return _finding(
        "FS.BS_CAST", total_field,
        f"{section.capitalize()} items sum to {_money(total)}, but the stated total is {_money(stated)}",
        stated=stated, expected=total, needs_review=needs_review,
    )


def identity_finding(
    year: str, assets_field: str, assets: float, liabilities: float, equity: float, *, needs_review: bool,
) -> NumericalFinding | None:
    """Total assets must equal total liabilities plus total equity."""
    expected = liabilities + equity
    if abs(assets - expected) <= _MONEY_TOLERANCE:
        return None
    return _finding(
        "FS.BALANCES", assets_field,
        f"Total assets ({year}) {_money(assets)} does not equal total liabilities {_money(liabilities)} "
        f"+ total equity {_money(equity)} ({_money(expected)})",
        stated=assets, expected=expected, needs_review=needs_review,
    )
