"""A notice of assessment's tax chain against IRAS's corporate income tax rules (sg_reference/corporate_tax.py)."""

from __future__ import annotations

import re
from typing import Any

from ...numerical_validation.models import NumericalFinding
from ...numerical_validation.registry import Num, check
from ..sg_reference.corporate_tax import TAX_RATE, Exemption, Rebate, exemptions_for, rebates_for
from ...numerical_validation.common import (
    CheckResult,
    field_number,
    new_finding,
    format_money,
    MONEY_TOLERANCE,
    table_rows,
    _skip_reference,
)


def _compute_noa(eci: float, exemption: Exemption, rebate: Rebate | None) -> dict[str, float]:
    first = min(eci, exemption.first_cap)
    nxt = min(max(eci - exemption.first_cap, 0), exemption.next_cap)
    exempt_first = round(first * exemption.first_pct)
    exempt_next = round(nxt * exemption.next_pct)
    after = eci - exempt_first - exempt_next
    tax = round(after * TAX_RATE, 2)
    rebate_amount = round(min(tax * rebate.pct, rebate.cap), 2) if rebate else 0.0
    # A company that received the CIT Rebate Cash Grant has the rebate printed net of it (nothing if
    # the rebate is no more than the grant), so both readings are genuine.
    grant = rebate.cash_grant if rebate else 0
    rebate_net = round(rebate_amount - grant, 2) if rebate_amount > grant else 0.0
    return {"exempt_first": exempt_first, "exempt_next": exempt_next, "after": after,
            "tax": tax, "rebate": rebate_amount, "payable": round(tax - rebate_amount, 2),
            "rebate_net": rebate_net, "payable_net": round(tax - rebate_net, 2)}


def _noa_year(year_of_assessment: Any) -> int | None:
    match = re.search(r"\d{4}", str(year_of_assessment or ""))
    return int(match.group(0)) if match else None


def _noa_findings(
    eci: float, tax_payable: float, year: int, lines: list[tuple[str, float | None]],
    exemption: Exemption, rebate: Rebate | None,
) -> list[NumericalFinding]:
    """The chain checked under one exemption scheme and one rebate version."""
    computed = _compute_noa(eci, exemption, rebate)
    findings = []

    def _find_line(pattern: str) -> float | None:
        regex = re.compile(pattern)
        return next((amount for label, amount in lines if regex.search(label)), None)

    for code_label, pattern, expected in (
        ("first", r"^first\b", computed["exempt_first"]),
        ("next", r"^next\b", computed["exempt_next"]),
    ):
        stated_tier = _find_line(pattern)
        if stated_tier is not None and abs(stated_tier - expected) > MONEY_TOLERANCE:
            findings.append(new_finding(
                "NOA.EXEMPTION_TIERS", "impossible",
                f"The \"{code_label}\" exemption tier should be {format_money(expected)} for ECI {format_money(eci)} under the "
                f"YA {year} {exemption.name}, but the computation shows {format_money(stated_tier)}",
                field="tax_computation", stated=format_money(stated_tier), expected=format_money(expected),
            ))

    stated_after = _find_line(r"chargeable income after")
    if stated_after is not None and abs(stated_after - computed["after"]) > MONEY_TOLERANCE:
        findings.append(new_finding(
            "NOA.CHARGEABLE_AFTER_EXEMPTION", "impossible",
            f"Chargeable income after exemption should be {format_money(computed['after'])} for ECI {format_money(eci)}, but the "
            f"computation shows {format_money(stated_after)}",
            field="tax_computation", stated=format_money(stated_after), expected=format_money(computed["after"]),
        ))

    stated_tax = _find_line(r"^tax on\b") or _find_line(r"^tax assessed\b")
    if stated_tax is not None and abs(stated_tax - computed["tax"]) > MONEY_TOLERANCE:
        findings.append(new_finding(
            "NOA.TAX_AT_RATE", "impossible",
            f"Tax at {TAX_RATE:.0%} should be {format_money(computed['tax'])}, but the computation shows "
            f"{format_money(stated_tax)}",
            field="tax_computation", stated=format_money(stated_tax), expected=format_money(computed["tax"]),
        ))

    def _matches_either(stated: float, *expected: float) -> bool:
        return any(abs(stated - e) <= MONEY_TOLERANCE for e in expected)

    stated_rebate = _find_line(r"\brebate\b")
    if stated_rebate is not None and not _matches_either(abs(stated_rebate), computed["rebate"], computed["rebate_net"]):
        if rebate is None:
            terms = f"YA {year} has no CIT Rebate"
        else:
            net_note = (f" or {format_money(computed['rebate_net'])} net of the {format_money(rebate.cash_grant)} cash grant"
                        if rebate.cash_grant else "")
            terms = (f"Tax rebate should be {format_money(computed['rebate'])}{net_note} ({rebate.pct:.0%}, capped at "
                     f"{format_money(rebate.cap)} for YA {year})")
        findings.append(new_finding(
            "NOA.REBATE", "impossible",
            f"{terms}, but the computation shows {format_money(stated_rebate)}",
            field="tax_computation", stated=format_money(stated_rebate), expected=format_money(computed["rebate"]),
        ))

    if not _matches_either(tax_payable, computed["payable"], computed["payable_net"]):
        findings.append(new_finding(
            "NOA.PAYABLE", "impossible",
            f"Tax payable should be {format_money(computed['payable'])} for ECI {format_money(eci)} under the YA {year} "
            f"{exemption.name}" + (" and CIT Rebate" if rebate else "") + f", but the notice states {format_money(tax_payable)}",
            field="tax_payable", stated=format_money(tax_payable), expected=format_money(computed["payable"]),
        ))

    # An amended assessment nets the new figure against the previous one:
    # payable - previous = additional tax due (positive) or discharged (negative).
    previous = _find_line(r"previous assessment")
    settled = _find_line(r"discharged")
    additional = _find_line(r"additional tax|balance payable")
    if previous is not None and (settled is not None or additional is not None):
        difference = tax_payable - abs(previous)
        stated_diff = -abs(settled) if settled is not None else abs(additional or 0.0)
        if abs(difference - stated_diff) > MONEY_TOLERANCE:
            findings.append(new_finding(
                "NOA.AMENDMENT", "impossible",
                f"Tax payable {format_money(tax_payable)} less the previous assessment {format_money(abs(previous))} is "
                f"{format_money(difference)}, but the notice states "
                f"{'a discharge of ' + format_money(abs(stated_diff)) if settled is not None else 'additional tax of ' + format_money(stated_diff)}",
                field="tax_computation", stated=format_money(abs(stated_diff)), expected=format_money(abs(difference)),
            ))
    return findings


@check(
    code="NOA.PAYABLE", applies=("notice_of_assessment",), group="external",
    source="IRAS corporate income tax rules",
)
def _check_noa_chain(
    *, eci: Num, tax_payable: Num, year_of_assessment: Any, tax_computation: Any = None,
) -> CheckResult:
    """The tax computation runs as one chain from ECI to payable tax: exemption tiers, chargeable income after
    exemption, tax at the rate, rebate, payable, and any amendment, under IRAS's rules for the year of
    assessment (sg_reference/corporate_tax.py).

    A company can be on the partial or the start-up tax exemption, and YA 2026 has two genuine rebate
    versions: the notice passes if any combination fits, and otherwise is reported against the one that fits
    best."""
    year = _noa_year(year_of_assessment)
    rebates = rebates_for(year) if year is not None else None
    if year is None or rebates is None:
        return [], _skip_reference("NOA.PAYABLE", f"corporate tax rules for YA {year_of_assessment}")

    lines = [(str(r.get("line", "")).strip().lower(), field_number(r, "amount")) for r in table_rows(tax_computation)]
    candidates = [
        _noa_findings(eci, tax_payable, year, lines, exemption, rebate)
        for exemption in exemptions_for(year)
        for rebate in (rebates or (None,))
    ]
    # min() keeps the first of equals: the partial exemption and the current rebate win ties.
    return min(candidates, key=len), None
