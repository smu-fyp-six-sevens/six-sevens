"""Numerical checks for payslips, credit reports, loan lists and proof of address."""

from __future__ import annotations

import re
from typing import Any

from .common import (
    skipped,
    _field_number,
    _MONEY_TOLERANCE,
    CheckResult,
    _as_number,
    _finding,
    _money,
    _rows,
)

from .registry import Num, check


# ---------------------------------------------------------------------------
# Payslip
# ---------------------------------------------------------------------------


@check(code="PAY.NET", applies=("payslip",))
def _check_payslip_sums(
    *, net_pay: Num, earnings_items: Any = None, deductions_items: Any = None,
    total_earnings: Num | None = None, total_deductions: Num | None = None,
) -> CheckResult:
    """Earnings lines sum to total earnings, deductions to total deductions, and earnings minus deductions to net pay."""
    if earnings_items is None and deductions_items is None:
        return [], skipped(reason="Required field(s) not extracted: earnings_items, deductions_items")
    findings = []
    if earnings_items is not None and total_earnings is not None:
        amounts = [_as_amount(r) for r in _rows(earnings_items)]
        numeric_amounts = [a for a in amounts if a is not None]
        if amounts and len(numeric_amounts) == len(amounts):
            total = sum(numeric_amounts)
            if abs(total - total_earnings) > _MONEY_TOLERANCE:
                findings.append(_finding(
                    "PAY.EARNINGS_SUM", "impossible",
                    f"Earnings lines sum to {_money(total)}, but total earnings shows {_money(total_earnings)}",
                    field="total_earnings", stated=_money(total_earnings), expected=_money(total),
                ))
    if total_deductions is not None:
        # A payslip may show deductions as negatives ("-1,790.00") and their total as a positive, or
        # the reverse: only the size of the deductions is compared.
        total_deductions = abs(total_deductions)
    if deductions_items is not None and total_deductions is not None:
        amounts = [_as_amount(r) for r in _rows(deductions_items)]
        numeric_amounts = [a for a in amounts if a is not None]
        if amounts and len(numeric_amounts) == len(amounts):
            total = abs(sum(numeric_amounts))
            if abs(total - total_deductions) > _MONEY_TOLERANCE:
                findings.append(_finding(
                    "PAY.DEDUCTIONS_SUM", "impossible",
                    f"Deduction lines sum to {_money(total)}, but total deductions shows {_money(total_deductions)}",
                    field="total_deductions", stated=_money(total_deductions), expected=_money(total),
                ))
    if total_earnings is not None and total_deductions is not None:
        expected_net = total_earnings - total_deductions
        if abs(expected_net - net_pay) > _MONEY_TOLERANCE:
            findings.append(_finding(
                "PAY.NET", "impossible",
                f"Total earnings {_money(total_earnings)} - total deductions {_money(total_deductions)} = "
                f"{_money(expected_net)}, but net pay shows {_money(net_pay)}",
                field="net_pay", stated=_money(net_pay), expected=_money(expected_net),
            ))
    return findings, None


def _as_amount(row: Any) -> float | None:
    """A line item's amount as a number."""
    return _field_number(row, "amount") if isinstance(row, dict) else None


# ---------------------------------------------------------------------------
# Credit report
# ---------------------------------------------------------------------------


@check(code="CR.SUMMARY_COUNTS", applies=("credit_report",))
def _check_cr_summary_counts(
    *, accounts: Any = None, account_count: Num | None = None, previous_enquiries: Any = None,
    enquiry_count: Num | None = None, defaults: Any = None, default_count: Num | None = None,
) -> CheckResult:
    """Summary counts of accounts, enquiries and defaults must match the rows listed."""
    findings = []
    ran_any = False
    for label, rows, stated, count_field in (
        ("accounts", accounts, account_count, "account_count"),
        ("enquiries", previous_enquiries, enquiry_count, "enquiry_count"),
        ("defaults", defaults, default_count, "default_count"),
    ):
        if rows is None or stated is None:
            continue
        ran_any = True
        actual = len(_rows(rows))
        if abs(stated - actual) > 0.5:
            findings.append(_finding(
                "CR.SUMMARY_COUNTS", "inconsistent",
                f"Summary states {stated:.0f} {label}, but {actual} row(s) are listed",
                field=count_field, stated=f"{stated:.0f}", expected=str(actual),
            ))
    if not ran_any:
        return [], skipped(reason="Required field(s) not extracted: accounts/account_count (and similar)")
    return findings, None


_DEFAULT_STATUS_RE = re.compile(r"overdue|write.?off|restructur", re.IGNORECASE)


@check(code="CR.DEFAULT_CONSISTENCY", applies=("credit_report",))
def _check_cr_default_consistency(*, accounts: Any, default_count: Num | None = None, defaults: Any = None) -> CheckResult:
    """A default count of zero is inconsistent with an account showing an overdue, write-off or restructure status."""
    defaults_rows = _rows(defaults)
    if default_count is None and not defaults_rows:
        return [], skipped(reason="Required field(s) not extracted: default_count/defaults")
    zero_defaults = default_count == 0 if default_count is not None else len(defaults_rows) == 0
    if not zero_defaults:
        return [], None
    concerning = [r for r in _rows(accounts) if _DEFAULT_STATUS_RE.search(str(r.get("status") or ""))]
    if concerning:
        return [_finding(
            "CR.DEFAULT_CONSISTENCY", "inconsistent",
            f"Default count is zero, but {len(concerning)} account(s) show an overdue/write-off/restructure status",
            field="default_count",
        )], None
    return [], None


@check(code="CR.LIMIT_VS_BALANCE", applies=("credit_report",))
def _check_cr_limit_vs_balance(*, accounts: Any) -> CheckResult:
    """Each account's balance must not exceed its stated limit."""
    findings = []
    for i, row in enumerate(_rows(accounts)):
        balance, limit = _field_number(row, "current_balance"), _field_number(row, "credit_limit")
        if balance is not None and limit is not None and balance > limit + _MONEY_TOLERANCE:
            findings.append(_finding(
                "CR.LIMIT_VS_BALANCE", "inconsistent",
                f"Account {i + 1}'s balance ({_money(balance)}) exceeds its stated limit ({_money(limit)})",
                field=f"accounts[{i}].current_balance", stated=_money(balance), expected=f"<= {_money(limit)}",
            ))
    return findings, None


@check(code="CR.AGGREGATE_SUM", applies=("credit_report",))
def _check_cr_aggregate_sum(*, accounts: Any, total_outstanding_balance: Num | None = None) -> CheckResult:
    """Account balances must add up to the stated total outstanding balance."""
    if total_outstanding_balance is None:
        return [], skipped(reason="Required field(s) not extracted: total_outstanding_balance")
    balances = [_field_number(r, "current_balance") for r in _rows(accounts)]
    numeric_balances = [b for b in balances if b is not None]
    if not balances or len(numeric_balances) != len(balances):
        return [], skipped(reason="account balances not all readable")
    total = sum(numeric_balances)
    if abs(total - total_outstanding_balance) > max(_MONEY_TOLERANCE, abs(total) * 0.01):
        return [_finding(
            "CR.AGGREGATE_SUM", "impossible",
            f"Account balances sum to {_money(total)}, but the stated total outstanding balance is "
            f"{_money(total_outstanding_balance)}",
            field="total_outstanding_balance", stated=_money(total_outstanding_balance), expected=_money(total),
        )], None
    return [], None


@check(code="CR.AMORTISATION", applies=("credit_report",))
def _check_cr_amortisation(*, accounts: Any) -> CheckResult:
    """Month-to-month balance fall must not exceed the instalment."""
    findings = []
    for i, row in enumerate(_rows(accounts)):
        history = _rows(row.get("balance_history"))
        instalment = _field_number(row, "monthly_instalment")
        if len(history) < 2 or instalment is None:
            continue
        # A history whose first balance is below its last is listed newest-first (as the credit
        # report's "Preceding 5 Months" tables are); put it oldest-first.
        first, last = _as_number(history[0].get("balance")), _as_number(history[-1].get("balance"))
        if first is not None and last is not None and first < last:
            history = list(reversed(history))
        for j in range(1, len(history)):
            prev, curr = _as_number(history[j - 1].get("balance")), _as_number(history[j].get("balance"))
            if prev is None or curr is None:
                continue
            fall = prev - curr
            if fall > instalment + _MONEY_TOLERANCE:
                findings.append(_finding(
                    "CR.AMORTISATION", "impossible",
                    f"Account {i + 1}'s balance fell by {_money(fall)} between two months, more than its own "
                    f"instalment ({_money(instalment)}) can account for",
                    field=f"accounts[{i}].balance_history",
                ))
    return findings, None


@check(code="CR.LINEAR_SERIES", applies=("credit_report",))
def _check_cr_linear_series(*, accounts: Any) -> CheckResult:
    """A balance falling by exactly the same amount for four or more consecutive months."""
    findings = []
    for i, row in enumerate(_rows(accounts)):
        balances = [b for b in (_field_number(h, "balance") for h in _rows(row.get("balance_history"))) if b is not None]
        if len(balances) < 5:
            continue
        diffs = [round(balances[j] - balances[j + 1], 2) for j in range(len(balances) - 1)]
        run = 1
        for j in range(1, len(diffs)):
            run = run + 1 if diffs[j] == diffs[j - 1] and diffs[j] != 0 else 1
            if run >= 4:
                findings.append(_finding(
                    "CR.LINEAR_SERIES", "implausible",
                    f"Account {i + 1}'s balance falls by exactly {_money(diffs[j])} for {run} consecutive "
                    "months — an unusually mechanical pattern",
                    field=f"accounts[{i}].balance_history",
                ))
                break
    return findings, None


@check(code="CR.TENOR", applies=("credit_report",))
def _check_cr_tenor(*, accounts: Any) -> CheckResult:
    """Balance divided by instalment must not imply a term beyond any plausible facility length."""
    findings = []
    for i, row in enumerate(_rows(accounts)):
        balance, instalment = _field_number(row, "current_balance"), _field_number(row, "monthly_instalment")
        if balance is None or not instalment:
            continue
        term = balance / instalment
        if term > 480:  # 40 years — beyond any facility this pipeline models
            findings.append(_finding(
                "CR.TENOR", "implausible",
                f"Account {i + 1}'s balance implies a {term:.0f}-month term at its stated instalment — beyond "
                "any plausible facility length",
                field=f"accounts[{i}].current_balance",
            ))
    return findings, None


@check(code="CR.SCORE", applies=("credit_report",))
def _check_cr_score(*, bureau_score: Any) -> CheckResult:
    """The bureau score must be an actual score in range, not the template's placeholder text."""
    if isinstance(bureau_score, str) and "example" in bureau_score.lower():
        return [_finding(
            "CR.SCORE", "inconsistent",
            "Bureau score field still contains the template's own \"Example\" placeholder text",
            field="bureau_score", stated=str(bureau_score),
        )], None
    score = _as_number(bureau_score)
    if score is None:
        return [], skipped(reason="bureau_score is not a number")
    if not 1000 <= score <= 2000:
        return [_finding(
            "CR.SCORE", "inconsistent", f"Bureau score {score:.0f} is outside the 1000-2000 range",
            field="bureau_score", stated=f"{score:.0f}",
        )], None
    return [], None


# ---------------------------------------------------------------------------
# List of loans
# ---------------------------------------------------------------------------


def _annuity_instalment(principal: float, monthly_rate: float, n_payments: int) -> float:
    if monthly_rate == 0:
        return principal / n_payments
    factor = (1 + monthly_rate) ** n_payments
    return principal * monthly_rate * factor / (factor - 1)


@check(code="LOAN.ANNUITY", applies=("list_of_loans",))
def _check_loan_annuity(*, loan_amount: Num, interest_rate: Num, loan_tenure: Num, monthly_instalment: Num) -> CheckResult:
    """A stated monthly instalment must match the annuity formula for principal, rate and tenure."""
    if loan_tenure <= 0:
        return [], skipped(reason="loan tenure is not positive")
    monthly_rate = (interest_rate / 100) / 12
    expected = _annuity_instalment(loan_amount, monthly_rate, int(loan_tenure))
    if abs(expected - monthly_instalment) > max(1.0, expected * 0.02):
        return [_finding(
            "LOAN.ANNUITY", "inconsistent",
            f"Instalment should be about {_money(expected)} for {_money(loan_amount)} at "
            f"{interest_rate:.2f}% over {loan_tenure:.0f} months, but the letter states {_money(monthly_instalment)}",
            field="monthly_instalment", stated=_money(monthly_instalment), expected=_money(expected),
        )], None
    return [], None


@check(code="LOAN.REPAYMENT_COUNT", applies=("list_of_loans",))
def _check_loan_repayment_count(*, loan_tenure: Num, repayment_schedule: Any) -> CheckResult:
    """The number of instalments must equal the tenure in months."""
    count = len(_rows(repayment_schedule))
    if count == 0:
        return [], skipped(reason="repayment schedule has no rows")
    if abs(loan_tenure - count) > 0.5:
        return [_finding(
            "LOAN.REPAYMENT_COUNT", "inconsistent",
            f"Stated tenure is {loan_tenure:.0f} months, but {count} instalments are listed",
            field="loan_tenure", stated=f"{loan_tenure:.0f}", expected=str(count),
        )], None
    return [], None


@check(code="LOAN.FEE", applies=("list_of_loans",))
def _check_loan_fee(*, loan_amount: Num, fee_percentage: Num, fee_amount: Num) -> CheckResult:
    """The stated fee amount must equal the fee percentage times the principal."""
    expected = loan_amount * fee_percentage / 100
    if abs(expected - fee_amount) > max(_MONEY_TOLERANCE, expected * 0.01):
        return [_finding(
            "LOAN.FEE", "inconsistent",
            f"{fee_percentage:.2f}% of {_money(loan_amount)} is {_money(expected)}, but the stated fee is "
            f"{_money(fee_amount)}",
            field="fee_amount", stated=_money(fee_amount), expected=_money(expected),
        )], None
    return [], None


_LOAN_RATE_BANDS = {"term_loan": (2.0, 10.0), "overdraft": (5.0, 15.0), "trade_facility": (2.0, 12.0)}


@check(code="LOAN.RATE_BAND", applies=("list_of_loans",))
def _check_loan_rate_band(*, interest_rate: Num, facility_type: Any) -> CheckResult:
    """The interest rate must fall within a configured band for the facility type."""
    band = _LOAN_RATE_BANDS.get(str(facility_type).strip().lower())
    if band is None:
        return [], skipped(reason="facility type is not recognised")
    lo, hi = band
    if not lo <= interest_rate <= hi:
        return [_finding(
            "LOAN.RATE_BAND", "implausible",
            f"Interest rate {interest_rate:.2f}% is outside the {lo}-{hi}% plausible band for {facility_type}",
            field="interest_rate", stated=f"{interest_rate:.2f}%",
        )], None
    return [], None


# ---------------------------------------------------------------------------
# Proof of address
# ---------------------------------------------------------------------------


@check(code="POA.USAGE_MATCH", applies=("proof_of_address",))
def _check_poa_usage_match(*, usage_this_period: Num, consumption_trend: Any) -> CheckResult:
    """Stated usage must equal the trend value for the billing month."""
    trend_rows = _rows(consumption_trend)
    if not trend_rows:
        return [], skipped(reason="consumption trend has no rows")
    # The billing month is the latest trend point, which sits at either end depending on the chart's order.
    ends = [trend_rows[-1]] if len(trend_rows) == 1 else [trend_rows[-1], trend_rows[0]]
    end_values = [v for v in (_field_number(r, "usage") for r in ends) if v is not None]
    tolerance = max(0.5, usage_this_period * 0.02)
    if end_values and all(abs(usage_this_period - v) > tolerance for v in end_values):
        return [_finding(
            "POA.USAGE_MATCH", "inconsistent",
            f"Stated usage ({usage_this_period:g}) does not match the latest consumption trend point "
            f"({' or '.join(f'{v:g}' for v in end_values)}, depending on the chart's order)",
            field="usage_this_period", stated=f"{usage_this_period:g}", expected=f"{end_values[0]:g}",
        )], None
    return [], None


@check(code="POA.BALANCE_ARITHMETIC", applies=("proof_of_address",))
def _check_poa_balance_arithmetic(
    *, previous_balance: Num | None = None, payment_received: Num | None = None, outstanding_balance: Num | None = None,
    current_charges: Num | None = None, total_payable: Num | None = None,
) -> CheckResult:
    """Previous balance minus payment equals outstanding; outstanding plus current charges equals total payable.
    Current charges are the month's bill; total payable adds any unpaid balance carried forward."""
    findings = []
    ran_any = False
    if previous_balance is not None and payment_received is not None and outstanding_balance is not None:
        ran_any = True
        # A bill prints the payment either way round ("-$199.60" or "$199.60"); it always reduces the balance.
        payment = abs(payment_received)
        expected = previous_balance - payment
        if abs(expected - outstanding_balance) > _MONEY_TOLERANCE:
            findings.append(_finding(
                "POA.BALANCE_ARITHMETIC", "impossible",
                f"Previous balance {_money(previous_balance)} - payment {_money(payment)} = "
                f"{_money(expected)}, but outstanding balance shows {_money(outstanding_balance)}",
                field="outstanding_balance", stated=_money(outstanding_balance), expected=_money(expected),
            ))
    if outstanding_balance is not None and current_charges is not None and total_payable is not None:
        ran_any = True
        expected = outstanding_balance + current_charges
        if abs(expected - total_payable) > _MONEY_TOLERANCE:
            findings.append(_finding(
                "POA.BALANCE_ARITHMETIC", "impossible",
                f"Outstanding balance {_money(outstanding_balance)} + current charges {_money(current_charges)} = "
                f"{_money(expected)}, but total payable shows {_money(total_payable)}",
                field="total_payable", stated=_money(total_payable), expected=_money(expected),
            ))
    if not ran_any:
        return [], skipped(reason="Required field(s) not extracted: previous_balance/outstanding_balance/total_payable")
    return findings, None


@check(code="POA.UNIT_RATE", applies=("proof_of_address",))
def _check_poa_unit_rate(*, current_charges: Num, usage_this_period: Num) -> CheckResult:
    """Charges divided by usage must fall within a plausible tariff band."""
    if not usage_this_period:
        return [], skipped(reason="usage is zero")
    rate = current_charges / usage_this_period
    if not 0.05 <= rate <= 3.0:
        return [_finding(
            "POA.UNIT_RATE", "implausible",
            f"Implied unit rate ({rate:.3f}/unit) is outside a plausible utility tariff band",
            field="current_charges",
        )], None
    return [], None
