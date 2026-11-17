"""GST charged on a bill against IRAS's rate for the bill date (sg_reference/gst.py)."""

from __future__ import annotations

from typing import Any

from ...numerical_validation.registry import Num, check
from ..sg_reference.gst import possible_rates
from ...numerical_validation.common import (
    CheckResult,
    new_finding,
    format_money,
    parse_field_date,
    skipped,
)


@check(code="POA.GST", applies=("proof_of_address",), group="external", source="IRAS GST rates")
def _check_gst(
    *, gst_amount: Num, charges_before_gst: Num, bill_date: Any = None, billing_period_end: Any = None,
) -> CheckResult:
    """GST on a utility bill must be the rate in force on the bill date times the charges before GST."""
    when = parse_field_date(bill_date) or parse_field_date(billing_period_end)
    if when is None:
        return [], skipped(reason="Required field(s) not extracted: bill_date or billing_period_end")
    rates = possible_rates(when)
    if not rates or charges_before_gst <= 0:
        return [], skipped(reason=f"No GST rate configured for {when.isoformat()}")
    # Bills compute GST per line and round each, so allow a few cents.
    tolerance = max(0.05, 0.005 * gst_amount)
    if any(abs(gst_amount - rate * charges_before_gst) <= tolerance for rate in rates):
        return [], None
    applied = gst_amount / charges_before_gst
    expected = sorted(rates)[-1]
    return [new_finding(
        "POA.GST", "inconsistent",
        f"GST of {format_money(gst_amount)} is {applied:.2%} of the charges ({format_money(charges_before_gst)}), but the "
        f"GST rate on {when.isoformat()} is {' or '.join(f'{r:.0%}' for r in sorted(rates))}",
        field="gst_amount", stated=format_money(gst_amount), expected=format_money(expected * charges_before_gst),
    )], None
