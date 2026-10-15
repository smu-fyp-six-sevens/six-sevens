"""Payslip CPF inputs, read from the line items when the payslip doesn't state them on their own.

Many payslips have no "Employee CPF" or "Ordinary wages" box: CPF is just a deduction line
("CPF", "Employee contribution") and the wages are the earnings lines. The CPF checks use
these when the dedicated field wasn't extracted.

Which earnings attract CPF, and how, follows CPF Board:
* Wages include basic pay, overtime, allowances, cash incentives, bonuses and commission;
  termination benefits, reimbursements and benefits in kind are not wages.
* Ordinary wages (OW) are wages "wholly and exclusively" for the month's work (salary,
  allowances, overtime); they are capped at the monthly OW ceiling.
* Additional wages (AW) are wages that are not, such as an annual bonus, leave pay or
  incentive payments; they are not capped monthly.

Extraction labels each earnings line with its "cpf_wage_type" (extraction_prompts.py); a
line it didn't label is classified from its description. A line that can't be classified
either way ("Benefits", "Commission": its frequency decides) is uncertain, and the checks
accept any reading of it rather than guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .common import _as_number, _rows

# A deduction line that is the employee's CPF. "Contribution" alone also names the self-help
# group funds (CDAC, SINDA, MBMF, ECF), so those are excluded below.
_CPF_LINE_RE = re.compile(r"\bcpf\b|provident fund|\b(?:employee'?s?|ee)\s+(?:share|contributions?)\b", re.IGNORECASE)
# Lines that aren't the employee's CPF: the employer's share (shown for information, not
# deducted) and the self-help group contributions.
_NOT_EMPLOYEE_CPF_RE = re.compile(r"employer|company|\ber\b|cdac|sinda|mbmf|\becf\b|self[- ]help|\bshg\b", re.IGNORECASE)

# Earnings classified from their description when extraction didn't label them.
_NOT_WAGES_RE = re.compile(
    r"reimburs|\bclaims?\b|expense|benefits? in kind|\bbik\b|termination|retrenchment|severance|"
    r"ex[- ]gratia.*(?:retrench|termination)", re.IGNORECASE,
)
_ADDITIONAL_RE = re.compile(
    r"bonus|\baws\b|13th|variable|performance|incentive|leave (?:pay|encash)|back ?pay|arrears|"
    r"ex[- ]gratia|long service|gratuity", re.IGNORECASE,
)
_ORDINARY_RE = re.compile(
    r"basic|salary|\bwages?\b|base pay|allowance|overtime|\bot\b|shift|transport|meal|housing|"
    r"on[- ]call|standby", re.IGNORECASE,
)
WAGE_TYPES = ("ordinary", "additional", "not_wages", "uncertain")


def _label(row: dict) -> str:
    return " ".join(str(row.get(key) or "") for key in ("description", "label"))


def wage_type(row: dict) -> str:
    """How CPF treats one earnings line: ordinary, additional, not_wages or uncertain."""
    labelled = str(row.get("cpf_wage_type") or "").strip().lower()
    if labelled in ("ordinary", "additional", "not_wages"):
        return labelled
    label = _label(row)
    if _NOT_WAGES_RE.search(label):
        return "not_wages"
    if _ADDITIONAL_RE.search(label):
        return "additional"
    if _ORDINARY_RE.search(label):
        return "ordinary"
    return "uncertain"


@dataclass(frozen=True)
class WageSplit:
    """The payslip's earnings by CPF treatment, and which lines went where."""
    ordinary: float
    additional: float
    uncertain: float
    lines: dict[str, list[str]]

    def describe(self) -> str:
        parts = [f"{kind.replace('_', ' ')}: {', '.join(labels)}" for kind, labels in self.lines.items() if labels]
        return "; ".join(parts)


def split_earnings(earnings_items: Any) -> WageSplit | None:
    """The earnings lines split by CPF treatment, or None if any amount can't be read."""
    rows = _rows(earnings_items)
    if not rows:
        return None
    totals: dict[str, float] = {kind: 0.0 for kind in WAGE_TYPES}
    lines: dict[str, list[str]] = {kind: [] for kind in WAGE_TYPES}
    for row in rows:
        amount = _as_number(row.get("amount"))
        if amount is None:
            return None
        kind = wage_type(row)
        totals[kind] += abs(amount)
        lines[kind].append(_label(row).strip() or "(unlabelled line)")
    return WageSplit(
        ordinary=round(totals["ordinary"], 2), additional=round(totals["additional"], 2),
        uncertain=round(totals["uncertain"], 2), lines=lines,
    )


@dataclass(frozen=True)
class WageScenario:
    """One reading of the month's wages: ordinary and additional wages, and where they came from."""
    ordinary: float
    additional: float
    basis: str


def wage_scenarios(
    ordinary_wages: float | None, additional_wages: float | None, earnings_items: Any,
) -> list[WageScenario]:
    """Every reading of the month's wages the payslip allows; empty if the wages can't be read.

    A stated ordinary_wages figure is one reading. The earnings lines give the others: their
    uncertain lines read as not wages, as ordinary wages, or as additional wages. A
    contribution is genuine if it fits any reading, so an uncertain line never causes a flag.
    """
    scenarios: list[WageScenario] = []
    if ordinary_wages is not None:
        scenarios.append(WageScenario(ordinary_wages, additional_wages or 0.0, "the stated ordinary wages"))
    split = split_earnings(earnings_items)
    if split is not None and (split.ordinary or split.additional or split.uncertain):
        extra = additional_wages if ordinary_wages is None and additional_wages else 0.0
        scenarios.append(WageScenario(split.ordinary, split.additional + extra, "the earnings lines"))
        if split.uncertain:
            scenarios.append(WageScenario(split.ordinary + split.uncertain, split.additional + extra,
                                          "the earnings lines, uncertain ones as ordinary wages"))
            scenarios.append(WageScenario(split.ordinary, split.additional + split.uncertain + extra,
                                          "the earnings lines, uncertain ones as additional wages"))
    return scenarios


def employee_cpf_from_deductions(deductions_items: Any) -> float | None:
    """The employee's CPF from the deduction lines.

    Extraction labels each deduction's "category"; where it did, only employee_cpf lines
    count. Otherwise the description decides. Sums every employee CPF line (a payslip can
    split ordinary and additional wage CPF into two lines). Deductions may be extracted as
    negatives.
    """
    rows = _rows(deductions_items)
    labelled = any(row.get("category") for row in rows)
    amounts = []
    for row in rows:
        amount = _as_number(row.get("amount"))
        if amount is None:
            continue
        if labelled:
            is_cpf = str(row.get("category") or "").strip().lower() == "employee_cpf"
        else:
            label = _label(row)
            is_cpf = bool(_CPF_LINE_RE.search(label)) and not _NOT_EMPLOYEE_CPF_RE.search(label)
        if is_cpf:
            amounts.append(abs(amount))
    return round(sum(amounts), 2) if amounts else None


def employee_cpf_input(employee_cpf: float | None, deductions_items: Any) -> tuple[float | None, str]:
    """The employee's CPF and the field it came from."""
    if employee_cpf is not None:
        return employee_cpf, "employee_cpf"
    return employee_cpf_from_deductions(deductions_items), "deductions_items"
