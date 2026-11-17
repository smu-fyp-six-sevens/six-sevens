"""
Measures that relate documents to each other, computed by code: whether a payslip's net pay arrives in a bank
statement, a company's revenue against the money its statements show coming in, the income the NOA assessed against
the profit the accounts report, counterparties that both pay and are paid, how concentrated the receipts are, which
months the statements cover, and loan repayments on a statement.

Pure (fields in, facts out) and shared by three readers: the evidence tools (tools/cross_checks.py), the planner's
candidate list (playbook.py) and the supporting-document rules (risk/supporting_documents.py). Nothing here decides
a finding: a measure is a lead the agent tests, or a reason to ask the client for a document.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Any

from ... import identifiers
from ...fraud_detection.dates.date_parsing import date_in_period
from ...fraud_detection.numerical_validation.common import as_number, parse_field_date, table_rows
from ...fraud_detection.shared.field_values import decode_field_value
from .evidence import signed_amount, transaction_rows

# A credit within this share of the net pay counts as the salary (rounding, a small deduction after the payslip).
SALARY_TOLERANCE = 0.01
# How far from the pay date a salary may land: banks credit a few days either side.
SALARY_DAYS_BEFORE, SALARY_DAYS_AFTER = 5, 10
# Receipts this concentrated on one counterparty are worth a contract or invoices behind them.
CONCENTRATION_SHARE = 0.5
CONCENTRATION_MIN_CREDITS = 6
# A counterparty that both pays and is paid at least this much each way.
ROUND_TRIP_MIN_EACH_WAY = 10_000.0

_LOAN_RE = re.compile(r"\b(LOAN|INSTAL?MENT|REPAYMENT|FINANCING|HIRE PURCHASE|MORTGAGE)\b", re.IGNORECASE)
_REVENUE_RE = re.compile(r"\b(revenue|turnover|sales)\b", re.IGNORECASE)
_PROFIT_BEFORE_TAX_RE = re.compile(r"profit\s+before\s+(income\s+)?tax", re.IGNORECASE)
_NOT_A_TOTAL_RE = re.compile(r"\b(cost|other|deferred|unearned)\b", re.IGNORECASE)


def value(fields: dict[str, Any], name: str) -> Any:
    return decode_field_value(fields.get(name))


def number(fields: dict[str, Any], name: str) -> float | None:
    return as_number(value(fields, name))


def period(fields: dict[str, Any]) -> tuple[date | None, date | None]:
    return parse_field_date(value(fields, "statement_period_start")), parse_field_date(value(fields, "statement_period_end"))


@dataclass(frozen=True)
class Movement:
    """One statement row as the measures read it."""

    index: int
    day: date | None
    amount: float
    description: str
    counterparty: str


def movements(fields: dict[str, Any]) -> list[Movement]:
    """A statement's rows with an amount, dated from the statement's period where they print no year."""
    # Imported here: supporting_documents imports this module, and its counterparty reader lives there.
    from ..risk.supporting_documents import _counterparty_from_description

    start, end = period(fields)
    out: list[Movement] = []
    for index, row in enumerate(transaction_rows(fields)):
        amount = signed_amount(row)
        if amount is None:
            continue
        description = str(row.get("description") or "").strip()
        name = str(row.get("counterparty") or _counterparty_from_description(description) or "").strip()
        out.append(Movement(index, date_in_period(row.get("date"), start, end), amount, description, name))
    return out


def _month(day: date) -> str:
    return f"{day.year}-{day.month:02d}"


def covered_months(fields: dict[str, Any]) -> list[str]:
    """The calendar months a statement covers: its stated period, else the months its dated rows fall in."""
    start, end = period(fields)
    if start and end and start <= end:
        months, cursor = [], date(start.year, start.month, 1)
        while cursor <= end:
            months.append(_month(cursor))
            cursor = date(cursor.year + cursor.month // 12, cursor.month % 12 + 1, 1)
        return months
    return sorted({_month(m.day) for m in movements(fields) if m.day})


def missing_months(month_lists: list[list[str]]) -> list[str]:
    """Months between the first and last covered month that no statement covers."""
    covered = sorted({m for months in month_lists for m in months})
    if len(covered) < 2:
        return []
    first, last = (date(int(m[:4]), int(m[5:]), 1) for m in (covered[0], covered[-1]))
    missing, cursor = [], first
    while cursor <= last:
        if _month(cursor) not in covered:
            missing.append(_month(cursor))
        cursor = date(cursor.year + cursor.month // 12, cursor.month % 12 + 1, 1)
    return missing


def salary_credit(payslip: dict[str, Any], statement: dict[str, Any]) -> dict[str, Any]:
    """Whether the payslip's net pay arrives on the statement near the pay date: the matching credits, or the nearest."""
    net = number(payslip, "net_pay")
    if net is None or net <= 0:
        return {"available": False, "reason": "The payslip has no net pay."}
    pay_day = parse_field_date(value(payslip, "pay_date")) or parse_field_date(value(payslip, "pay_period_end"))
    credits = [m for m in movements(statement) if m.amount > 0]
    if not credits:
        return {"available": False, "reason": "The statement has no credits."}
    window = (
        (pay_day - timedelta(days=SALARY_DAYS_BEFORE), pay_day + timedelta(days=SALARY_DAYS_AFTER)) if pay_day else None
    )
    in_window = [m for m in credits if window is None or (m.day is not None and window[0] <= m.day <= window[1])]
    matches = [m for m in in_window if abs(m.amount - net) <= max(1.0, net * SALARY_TOLERANCE)]
    nearest = min(in_window or credits, key=lambda m: abs(m.amount - net))
    start, end = period(statement)
    return {
        "available": True,
        "net_pay": net,
        "pay_date": pay_day.isoformat() if pay_day else None,
        "pay_date_in_statement_period": (
            None if pay_day is None or start is None or end is None else start <= pay_day <= end + timedelta(days=SALARY_DAYS_AFTER)
        ),
        "credits_near_pay_date": len(in_window),
        "matching_credits": [_shown(m) for m in matches],
        "nearest_credit": _shown(nearest),
        "nearest_difference": round(nearest.amount - net, 2),
    }


def _shown(m: Movement) -> dict[str, Any]:
    return {"row": m.index, "date": m.day.isoformat() if m.day else None, "amount": m.amount,
            "counterparty": m.counterparty or None}


def revenue(statement_of_accounts: dict[str, Any]) -> float | None:
    """The year's revenue: the field, else the income statement's revenue line."""
    direct = number(statement_of_accounts, "revenue")
    if direct is not None:
        return direct
    for row in table_rows(value(statement_of_accounts, "income_statement_items")):
        label = str(row.get("label") or "")
        if _REVENUE_RE.search(label) and not _NOT_A_TOTAL_RE.search(label):
            amount = as_number(row.get("amount"))
            if amount is not None:
                return abs(amount)
    return None


def profit_before_tax(statement_of_accounts: dict[str, Any]) -> float | None:
    for row in table_rows(value(statement_of_accounts, "income_statement_items")):
        if _PROFIT_BEFORE_TAX_RE.search(str(row.get("label") or "")):
            amount = as_number(row.get("amount"))
            if amount is not None:
                return amount
    return number(statement_of_accounts, "net_profit") or number(statement_of_accounts, "profit_for_the_year")


def revenue_against_credits(statement_of_accounts: dict[str, Any], statements: list[dict[str, Any]]) -> dict[str, Any]:
    """The accounts' revenue against the statements' credits, annualised over the months they cover."""
    annual = revenue(statement_of_accounts)
    if annual is None or annual <= 0:
        return {"available": False, "reason": "The financial statement shows no revenue."}
    months = sorted({m for s in statements for m in covered_months(s)})
    credits = sum(m.amount for s in statements for m in movements(s) if m.amount > 0)
    if not months or credits <= 0:
        return {"available": False, "reason": "The statements show no credits over a known period."}
    annualised = credits / len(months) * 12
    return {
        "available": True, "revenue": annual, "financial_year_end": value(statement_of_accounts, "financial_year_end"),
        "months_of_statements": len(months), "credits_in_those_months": round(credits, 2),
        "credits_annualised": round(annualised, 2), "annualised_credits_to_revenue": round(annualised / annual, 3),
    }


def assessed_against_profit(notice: dict[str, Any], statement_of_accounts: dict[str, Any]) -> dict[str, Any]:
    """The income the NOA assessed (ECI) against the profit before tax in the accounts, with the years each covers."""
    assessed = number(notice, "eci")
    profit = profit_before_tax(statement_of_accounts)
    if assessed is None or profit is None:
        return {"available": False, "reason": "The NOA has no ECI, or the accounts no profit before tax."}
    return {
        "available": True, "assessed_income": assessed, "profit_before_tax": profit,
        "year_of_assessment": value(notice, "year_of_assessment"),
        "financial_year_end": value(statement_of_accounts, "financial_year_end"),
        "assessed_to_profit": round(assessed / profit, 3) if profit else None,
        "note": "A year of assessment taxes the income of the year before it.",
    }


def round_trips(statement: dict[str, Any], own_name: str | None = None) -> list[dict[str, Any]]:
    """Counterparties that both pay into and are paid from the account, at least ROUND_TRIP_MIN_EACH_WAY each way."""
    flows: dict[str, dict[str, float]] = defaultdict(lambda: {"in": 0.0, "out": 0.0, "count": 0})
    names: dict[str, str] = {}
    for m in movements(statement):
        if not m.counterparty or identifiers.mentions_company(m.counterparty, own_name):
            continue
        key = identifiers.company_key(m.counterparty) or m.counterparty.casefold()
        names.setdefault(key, m.counterparty)
        flows[key]["in" if m.amount > 0 else "out"] += abs(m.amount)
        flows[key]["count"] += 1
    out = [
        {"counterparty": names[k], "paid_in": round(f["in"], 2), "paid_out": round(f["out"], 2), "count": int(f["count"])}
        for k, f in flows.items() if f["in"] >= ROUND_TRIP_MIN_EACH_WAY and f["out"] >= ROUND_TRIP_MIN_EACH_WAY
    ]
    return sorted(out, key=lambda r: -min(r["paid_in"], r["paid_out"]))


def concentration(statement: dict[str, Any], own_name: str | None = None) -> dict[str, Any]:
    """The share of receipts from the largest paying counterparty (own-account transfers left out)."""
    totals: dict[str, float] = defaultdict(float)
    names: dict[str, str] = {}
    count = 0
    for m in movements(statement):
        if m.amount <= 0 or identifiers.mentions_company(m.description, own_name):
            continue
        count += 1
        name = m.counterparty or "(unnamed)"
        key = identifiers.company_key(name) or name.casefold()
        names.setdefault(key, name)
        totals[key] += m.amount
    volume = sum(totals.values())
    if not totals or volume <= 0:
        return {"available": False, "reason": "The statement has no receipts."}
    top_key = max(totals, key=lambda k: totals[k])
    return {
        "available": True, "credits": count, "total_credits": round(volume, 2),
        "top_counterparty": names[top_key], "top_total": round(totals[top_key], 2),
        "top_share": round(totals[top_key] / volume, 3),
    }


def loan_repayments(statement: dict[str, Any]) -> list[Movement]:
    """Debits whose description names a loan, instalment or repayment."""
    return [m for m in movements(statement) if m.amount < 0 and _LOAN_RE.search(m.description)]


def large_one_off_credits(statement: dict[str, Any], *, floor: float) -> list[Movement]:
    """Credits at least `floor` and five times the median credit: money that doesn't look like trading."""
    credits = [m for m in movements(statement) if m.amount > 0]
    if len(credits) < 5:
        return []
    typical = median(m.amount for m in credits)
    return [m for m in credits if m.amount >= max(floor, 5 * typical)]
