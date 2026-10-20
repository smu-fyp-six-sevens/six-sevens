"""
Where a per-document finding's figure should also appear in another document. The planner is shown
each match as a lead to test against that document (never to restate), so a figure that looks off on
one document is checked against the one that should agree with it.
"""

from __future__ import annotations

from typing import Any

# check code -> (the document type that should carry the figure, its fields, what to compare)
_PAYSLIP_LEAD = ("bank_statement", ("transactions",), "a salary credit near the pay date")
_LEADS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "FS.TAX_EXPENSE_RATE": (
        "notice_of_assessment", ("tax_payable", "eci", "chargeable_income_after_exemption"),
        "the tax assessed on the same profit",
    ),
    "PAY.NET": _PAYSLIP_LEAD,
    "PAY.EARNINGS_SUM": _PAYSLIP_LEAD,
}


def _check_code(finding: dict[str, Any]) -> str | None:
    details = finding.get("details")
    code = finding.get("check_code") or (details.get("check_code") if isinstance(details, dict) else None)
    return code if isinstance(code, str) else None


def lead_for(finding: dict[str, Any], docs: dict[str, Any]) -> str | None:
    """A line telling the planner which document to test the finding's figure against, when the case has one."""
    entry = _LEADS.get(_check_code(finding) or "")
    if entry is None:
        return None
    peer_type, names, what = entry
    keys = [key for key, view in docs.items() if view.document_type == peer_type]
    if not keys:
        return None
    return (
        f"lead: test this figure against {', '.join(keys)} ({', '.join(names)}): {what}. "
        "Cite both documents; do not restate the finding."
    )
