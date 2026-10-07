"""Numerical checks for bank statements, source of wealth and notices of assessment."""

from __future__ import annotations

import collections
import math
import re
from typing import Any

from pydantic import BaseModel

from .common import (
    skipped,
    _field_number,
    _CURRENCY_SYMBOLS,
    _CURRENCY_WORDS,
    _CURRENT_NOA_SCHEME,
    REFERENCE_DATA,
    _MONEY_TOLERANCE,
    CheckResult,
    _as_number,
    _bank_reference,
    _finding,
    _materiality_note,
    _money,
    _parse_field_date,
    _rows,
    _skip_reference,
    row_amount,
)
from .models import BenfordAnalysis, BenfordDigit, NumericalFinding
from .registry import Num, check


# ---------------------------------------------------------------------------
# Bank statement
# ---------------------------------------------------------------------------


class _BalanceBreak(BaseModel):
    index: int
    previous: float
    amount: float
    balance: float
    expected: float
    description: str
    sign_inverted: bool = False


_SYSTEMIC_BREAK_RATIO = 0.25
_SYSTEMIC_MIN_BREAKS = 4


def _running_balance_breaks(rows: list[dict]) -> list[_BalanceBreak]:
    breaks: list[_BalanceBreak] = []
    previous: float | None = None
    for index, row in enumerate(rows):
        balance = _field_number(row, "balance")
        amount = _field_number(row, "amount")
        if previous is None or amount is None:
            previous = balance
            continue
        expected = previous + amount
        if balance is None:
            previous = expected
            continue
        if abs(expected - balance) > _MONEY_TOLERANCE:
            breaks.append(_BalanceBreak(
                index=index, previous=previous, amount=amount, balance=balance,
                expected=expected, description=str(row.get("description") or "").strip(),
                sign_inverted=abs(previous - amount - balance) <= _MONEY_TOLERANCE,
            ))
        previous = balance
    return breaks


def _is_newest_first(rows: list[dict]) -> bool:
    """Whether a transaction list runs newest-first, worked out per statement: from the dates when they
    differ, otherwise from which direction the running balance reconciles in with fewer breaks."""
    dates = [d for d in (_parse_field_date(r.get("date")) for r in rows) if d is not None]
    if len(dates) >= 2 and dates[0] != dates[-1]:
        return dates[0] > dates[-1]
    return len(_running_balance_breaks(list(reversed(rows)))) < len(_running_balance_breaks(rows))


def _balance_finding(b: _BalanceBreak) -> NumericalFinding:
    where = f"transaction {b.index + 1}" + (f" ({b.description})" if b.description else "")
    if b.sign_inverted:
        return _finding(
            "BS.RUNNING_BALANCE", "inconsistent",
            f"Debit/credit direction looks reversed on {where}: recorded as {_money(b.amount)}, but the "
            f"balance moved from {_money(b.previous)} to {_money(b.balance)} — a change of {_money(-b.amount)}",
            field=f"transactions[{b.index}].amount", stated=_money(b.amount), expected=_money(-b.amount),
            severity_note="Direction, not magnitude: usually a debit/credit read the wrong way round",
        )
    return _finding(
        "BS.RUNNING_BALANCE", "impossible",
        f"Running balance does not reconcile on {where}: {_money(b.previous)} "
        f"{'-' if b.amount < 0 else '+'} {_money(abs(b.amount))} = {_money(b.expected)}, but the statement "
        f"shows {_money(b.balance)}",
        field=f"transactions[{b.index}].balance", stated=_money(b.balance), expected=_money(b.expected),
    )


@check(code="BS.RUNNING_BALANCE", applies=("bank_statement",))
def _check_running_balance(*, transactions: Any) -> CheckResult:
    """Each balance must equal the previous one plus that row's amount. Direction is worked out per statement:
    most banks list oldest-first, but some (e.g. OCBC) list newest-first."""
    rows = _rows(transactions)
    if len(rows) < 2:
        return [], skipped(reason="fewer than 2 transactions")

    if _is_newest_first(rows):
        last = len(rows) - 1
        breaks = [b.model_copy(update={"index": last - b.index}) for b in _running_balance_breaks(list(reversed(rows)))]
    else:
        breaks = _running_balance_breaks(rows)
    if not breaks:
        return [], None

    comparable = max(len(rows) - 1, 1)
    if len(breaks) >= _SYSTEMIC_MIN_BREAKS and len(breaks) / comparable > _SYSTEMIC_BREAK_RATIO:
        return [_finding(
            "BS.RUNNING_BALANCE", "implausible",
            f"The running balance fails to reconcile on {len(breaks)} of {comparable} transaction lines, in both "
            "date directions — the balance column could not be followed through this statement",
            field="transactions", stated=f"{len(breaks)} of {comparable} lines",
            expected="a balance column that reconciles end to end",
            severity_note="Systemic rather than per-figure: usually means the column couldn't be read cleanly, "
                          "not that this many lines were edited",
        )], None
    return [_balance_finding(b) for b in breaks], None


@check(code="BS.OPEN_CLOSE", applies=("bank_statement",))
def _check_open_close(
    *, transactions: Any, ledger_balance: Num | None = None, opening_balance: Num | None = None,
) -> CheckResult:
    """Opening balance plus credits minus debits must equal the closing balance. The stated header balances
    are used as the anchors when present, and also cross-checked against the transaction-derived values."""
    rows = _rows(transactions)
    if len(rows) >= 2 and _is_newest_first(rows):
        rows = list(reversed(rows))
    amounts = [_field_number(r, "amount") for r in rows]
    balances = [_field_number(r, "balance") for r in rows]
    if not rows or any(a is None for a in amounts) or not balances or balances[0] is None or balances[-1] is None:
        return [], skipped(reason="transactions are missing amounts or balances")
    first_balance, last_balance = balances[0], balances[-1]
    amounts = [a for a in amounts if a is not None]

    derived_opening = first_balance - amounts[0]
    derived_closing = last_balance
    opening = opening_balance if opening_balance is not None else derived_opening
    closing = ledger_balance if ledger_balance is not None else derived_closing
    credits = sum(a for a in amounts if a and a > 0)
    debits = sum(-a for a in amounts if a and a < 0)
    expected_closing = opening + credits - debits

    findings = []
    if abs(expected_closing - closing) > _MONEY_TOLERANCE:
        findings.append(_finding(
            "BS.OPEN_CLOSE", "impossible",
            f"Opening balance {_money(opening)} + credits {_money(credits)} - debits {_money(debits)} = "
            f"{_money(expected_closing)}, but the closing balance is {_money(closing)}",
            field="transactions", stated=_money(closing), expected=_money(expected_closing),
        ))
    if opening_balance is not None and abs(opening_balance - derived_opening) > _MONEY_TOLERANCE:
        findings.append(_finding(
            "BS.STATED_OPENING", "inconsistent",
            f"Stated opening balance {_money(opening_balance)} does not match the balance implied by the first "
            f"transaction ({_money(derived_opening)})",
            field="opening_balance", stated=_money(opening_balance), expected=_money(derived_opening),
        ))
    if ledger_balance is not None and abs(ledger_balance - derived_closing) > _MONEY_TOLERANCE:
        findings.append(_finding(
            "BS.STATED_CLOSING", "inconsistent",
            f"Stated ledger balance {_money(ledger_balance)} does not match the last transaction's running balance "
            f"({_money(derived_closing)})",
            field="ledger_balance", stated=_money(ledger_balance), expected=_money(derived_closing),
        ))
    return findings, None


@check(code="BS.TOTALS", applies=("bank_statement",))
def _check_totals(
    *, transactions: Any, total_credits: Num | None = None, total_debits: Num | None = None, transaction_count: Num | None = None,
) -> CheckResult:
    """Any stated total credits, total debits or transaction count must match the transaction lines."""
    rows = _rows(transactions)
    amounts = [a for a in (_field_number(r, "amount") for r in rows) if a is not None]
    computed_values = {
        "total_credits": sum(a for a in amounts if a > 0),
        "total_debits": -sum(a for a in amounts if a < 0),
        "transaction_count": float(len(rows)),
    }
    stated_values = {"total_credits": total_credits, "total_debits": total_debits, "transaction_count": transaction_count}
    findings = []
    ran_any = False
    for field_name, computed in computed_values.items():
        stated = stated_values[field_name]
        if stated is None:
            continue
        ran_any = True
        tolerance = _MONEY_TOLERANCE if field_name != "transaction_count" else 0.5
        if abs(stated - computed) > tolerance:
            findings.append(_finding(
                "BS.TOTALS", "inconsistent",
                f"Stated {field_name} ({stated:,.2f}) does not match the transaction lines ({computed:,.2f})",
                field=field_name, stated=f"{stated:,.2f}", expected=f"{computed:,.2f}",
            ))
    if not ran_any:
        return [], skipped(reason="Required field(s) not extracted: total_credits/total_debits/transaction_count")
    return findings, None


@check(code="BS.OVERDRAFT_LIMIT", applies=("bank_statement",))
def _check_overdraft_limit(*, transactions: Any, overdraft_limit: Num) -> CheckResult:
    """No balance may fall below the negative of the overdraft limit (including a limit of zero)."""
    findings = []
    for i, row in enumerate(_rows(transactions)):
        balance = _field_number(row, "balance")
        if balance is not None and balance < -abs(overdraft_limit) - _MONEY_TOLERANCE:
            findings.append(_finding(
                "BS.OVERDRAFT_LIMIT", "impossible",
                f"Balance {_money(balance)} on transaction {i + 1} exceeds the overdraft limit of "
                f"{_money(overdraft_limit)}",
                field=f"transactions[{i}].balance", stated=_money(balance), expected=f">= -{_money(overdraft_limit)}",
            ))
    return findings, None


@check(code="BS.AVAILABLE_VS_LEDGER", applies=("bank_statement",))
def _check_available_vs_ledger(
    *, ledger_balance: Num, available_balance: Num, holds: Num | None = None, total_hold_amount: Num | None = None,
) -> CheckResult:
    """Available balance equals ledger balance minus holds."""
    # Extraction sometimes names this after the printed label ("Total Hold Amount").
    hold_amount = holds if holds is not None else (total_hold_amount or 0.0)
    expected = ledger_balance - hold_amount
    if abs(expected - available_balance) > _MONEY_TOLERANCE:
        return [_finding(
            "BS.AVAILABLE_VS_LEDGER", "inconsistent",
            f"Available balance {_money(available_balance)} does not equal ledger balance {_money(ledger_balance)} "
            f"minus holds {_money(hold_amount)} ({_money(expected)})",
            field="available_balance", stated=_money(available_balance), expected=_money(expected),
        )], None
    return [], None


_ROUNDED_AMOUNT_IN_TEXT_RE = re.compile(rf"(?:{_CURRENCY_WORDS}|{_CURRENCY_SYMBOLS})\s?(\d{{1,3}}(?:,\d{{3}})*)")


@check(code="BS.NARRATIVE_AMOUNT", applies=("bank_statement",))
def _check_narrative_amount(*, transactions: Any) -> CheckResult:
    """Where a transaction description repeats a rounded amount, it must match the amount column to within $1."""
    findings = []
    ran_any = False
    for i, row in enumerate(_rows(transactions)):
        match = _ROUNDED_AMOUNT_IN_TEXT_RE.search(str(row.get("description") or ""))
        amount = _field_number(row, "amount")
        if not match or amount is None:
            continue
        ran_any = True
        narrative_amount = _as_number(match.group(1))
        if narrative_amount is not None and abs(abs(amount) - narrative_amount) > 1.0:
            findings.append(_finding(
                "BS.NARRATIVE_AMOUNT", "inconsistent",
                f"Transaction {i + 1}'s description states {_money(narrative_amount)}, but the amount column shows "
                f"{_money(abs(amount))}",
                field=f"transactions[{i}].description", stated=_money(narrative_amount), expected=_money(abs(amount)),
            ))
    if not ran_any:
        return [], skipped(reason="no transaction description repeats an amount")
    return findings, None


_FEE_KEYWORDS = re.compile(r"fee|charge|fast\b|giro|interbank", re.IGNORECASE)
# The bank's own charges only: FAST/GIRO name a payment channel, so a supplier payment sent by GIRO isn't a fee.
_BANK_CHARGE_KEYWORDS = re.compile(r"\bfees?\b|\bcharges?\b|commission", re.IGNORECASE)


@check(code="BS.FEE_SCHEDULE", applies=("bank_statement",))
def _check_fee_schedule(*, transactions: Any) -> CheckResult:
    """A bank charge should come from a fixed tariff, so a recurring charge should not take free-form amounts."""
    rows = _rows(transactions)
    if len(rows) < 3:
        return [], skipped(reason="fewer than 3 transactions")

    fee_amounts_by_desc: dict[str, list[float]] = {}
    for row in rows:
        amount = _field_number(row, "amount")
        description = str(row.get("description") or "")
        if amount is not None and _BANK_CHARGE_KEYWORDS.search(description):
            key = re.sub(r"\d+", "#", description.strip().lower())
            fee_amounts_by_desc.setdefault(key, []).append(abs(amount))

    findings = []
    for desc, amounts in fee_amounts_by_desc.items():
        if len(amounts) < 3:
            continue
        # One description can legitimately take a couple of set amounts (a $35.00 monthly fee and a $0.50
        # per-transaction fee both printed as "CHARGES"). Only flag amounts that look free-form.
        counts = collections.Counter(round(a, 2) for a in amounts)
        recurring = [a for a, n in counts.items() if n >= 2]
        one_offs = [a for a, n in counts.items() if n == 1 and recurring and all(abs(a - r) / r > 0.5 for r in recurring if r)]
        if len(counts) > max(2, len(amounts) // 2):
            reason = (f"takes {len(counts)} different amounts across {len(amounts)} occurrences — bank charges "
                      "normally come from a fixed tariff")
        elif one_offs:
            reason = (f"is normally {', '.join(_money(r) for r in sorted(recurring))} but appears once as "
                      f"{', '.join(_money(a) for a in sorted(one_offs))}")
        else:
            continue
        findings.append(_finding(
            "BS.FEE_SCHEDULE", "implausible",
            f"Recurring charge \"{desc}\" {reason} ({', '.join(_money(a) for a in amounts)})",
            field="transactions",
        ))
    return findings, None


@check(code="BS.BANK_IDENTIFIERS", applies=("bank_statement",))
def _check_bank_identifiers(
    *, bank_name: Any, bank_registration_no: Any = None, bank_gst_no: Any = None, bic_code: Any = None,
) -> CheckResult:
    """The bank's registration number, GST number and SWIFT BIC printed on the statement must match the
    reference table for the named bank. A mismatch is a fabricated or mistyped identifier."""
    reference = _bank_reference(bank_name)
    if reference is None:
        return [], _skip_reference("BS.BANK_IDENTIFIERS", f"bank_identifiers[{bank_name!r}]")
    findings = []
    ran_any = False
    for field_name, stated, key, label, length in (
        ("bank_registration_no", bank_registration_no, "registration_no", "registration number", None),
        ("bank_gst_no", bank_gst_no, "gst_no", "GST registration number", None),
        # BIC: compare bank + country + location only; the optional 3-letter branch suffix doesn't change the institution.
        ("bic_code", bic_code, "bic", "SWIFT BIC", 8),
    ):
        expected = reference.get(key)
        if not stated or not expected:
            continue
        ran_any = True
        stated_norm = re.sub(r"[^A-Z0-9]", "", str(stated).upper())[:length]
        expected_norm = re.sub(r"[^A-Z0-9]", "", str(expected).upper())[:length]
        if stated_norm != expected_norm:
            findings.append(_finding(
                "BS.BANK_IDENTIFIERS", "impossible",
                f"The statement prints {bank_name}'s {label} as {stated}, but the bank's actual {label} is {expected}",
                field=field_name, stated=str(stated), expected=str(expected),
            ))
    if not ran_any:
        return [], skipped(reason="no identifier printed to compare")
    return findings, None


@check(code="BS.DEPOSIT_INSURANCE_TEXT", applies=("bank_statement",))
def _check_deposit_insurance_text() -> CheckResult:
    """Deposit insurance wording is checked against a reference table that isn't configured yet."""
    return [], _skip_reference("BS.DEPOSIT_INSURANCE_TEXT", "deposit_insurance_limits")


# Verified against real extracted transaction descriptions (e.g. "4933-XXXX-XXXX-6411"): 4 digits, two masked
# groups, 4 digits. The second alternative covers a straight run of mask characters ending in the last 4 digits.
_CARD_RE = re.compile(r"\b\d{4}-[Xx*]{4}-[Xx*]{4}-\d{4}\b|\b[Xx*]{4,}\d{4}\b")


@check(code="BS.CARD_DIVERSITY", applies=("bank_statement",))
def _check_card_diversity(*, transactions: Any) -> CheckResult:
    """A near one-to-one ratio of distinct masked card numbers to card transactions is a signal."""
    card_txns = 0
    cards: set[str] = set()
    for row in _rows(transactions):
        match = _CARD_RE.search(str(row.get("description") or ""))
        if match:
            card_txns += 1
            cards.add(match.group(0))
    if card_txns < 5:
        return [], skipped(reason="fewer than 5 card transactions")
    if len(cards) / card_txns > 0.8:
        return [_finding(
            "BS.CARD_DIVERSITY", "signal",
            f"{len(cards)} distinct masked card numbers across only {card_txns} card transactions — unusually "
            "little reuse of the same card",
            field="transactions",
        )], None
    return [], None


@check(code="BS.RECURRING_VARIANCE", applies=("bank_statement",))
def _check_recurring_variance(*, transactions: Any) -> CheckResult:
    """A recurring payee (rent, subscriptions) whose amount varies beyond 15%."""
    by_payee: dict[str, list[float]] = {}
    for row in _rows(transactions):
        description = str(row.get("description") or "").strip().lower()
        amount = _field_number(row, "amount")
        if description and amount is not None:
            by_payee.setdefault(re.sub(r"\d+", "#", description), []).append(abs(amount))

    findings = []
    for payee, amounts in by_payee.items():
        if len(amounts) < 3 or _FEE_KEYWORDS.search(payee):
            continue
        mean = sum(amounts) / len(amounts)
        if mean == 0:
            continue
        if any(abs(a - mean) / mean > 0.15 for a in amounts):
            findings.append(_finding(
                "BS.RECURRING_VARIANCE", "signal",
                f"Recurring payment \"{payee}\" varies by more than 15% across the statement "
                f"({', '.join(_money(a) for a in amounts)})",
                field="transactions",
            ))
    return findings, None


# --- Statistical checks (bank statement transactions) ----------------------

BENFORD_EXPECTED: dict[int, float] = {d: math.log10(1 + 1 / d) * 100 for d in range(1, 10)}
BENFORD_MIN_SAMPLE = 100
_CHI_SQUARE_CRITICAL_8DF_P01 = 20.090


def _benford_unavailable(reason: str, sample_size: int = 0) -> BenfordAnalysis:
    # No digits padded in with zeroed-out counts: a 9-row table of zeros reads as real data, not "not applicable".
    return BenfordAnalysis(sample_size=sample_size, digits=[], unable_to_assess=True, unable_to_assess_reason=reason)


def _leading_digit(magnitude: float) -> int | None:
    text = f"{magnitude:.10e}"
    first = text[0]
    return int(first) if first.isdigit() and first != "0" else None


def _benford(amounts: list[float], code: str) -> tuple[BenfordAnalysis, list[NumericalFinding]]:
    digits = [d for d in (_leading_digit(abs(a)) for a in amounts if a) if d is not None]
    if len(digits) < BENFORD_MIN_SAMPLE:
        return _benford_unavailable(
            f"Only {len(digits)} eligible value(s); Benford's Law needs at least {BENFORD_MIN_SAMPLE} to be "
            "meaningful", sample_size=len(digits),
        ), []

    total = len(digits)
    rows: list[BenfordDigit] = []
    deviations, chi_square = [], 0.0
    for digit in range(1, 10):
        count = digits.count(digit)
        observed = count / total * 100
        expected = BENFORD_EXPECTED[digit]
        expected_count = total * expected / 100
        deviations.append(abs(observed - expected) / 100)
        chi_square += (count - expected_count) ** 2 / expected_count
        rows.append(BenfordDigit(digit=digit, count=count, observed_percentage=round(observed, 4),
                                 expected_percentage=round(expected, 4)))

    conforms = chi_square <= _CHI_SQUARE_CRITICAL_8DF_P01
    analysis = BenfordAnalysis(
        sample_size=total, digits=rows, mean_absolute_deviation=round(sum(deviations) / 9, 6),
        chi_square=round(chi_square, 4), chi_square_critical=_CHI_SQUARE_CRITICAL_8DF_P01,
        conforms=conforms, unable_to_assess=False,
    )
    if conforms:
        return analysis, []

    worst = max(rows, key=lambda r: abs(r.observed_percentage - r.expected_percentage))
    return analysis, [_finding(
        code, "signal",
        f"Leading-digit distribution across {total} amounts differs from Benford's Law by more than chance "
        f"(chi-square {chi_square:.1f} against a p<0.01 critical value of {_CHI_SQUARE_CRITICAL_8DF_P01}); "
        f"the largest gap is digit {worst.digit}",
        field="transactions",
        severity_note="Statistical, not per-figure: describes the whole distribution, not any single amount",
    )]


@check(code="STAT.DUPLICATES", applies=("bank_statement",))
def _check_duplicates(*, transactions: Any) -> CheckResult:
    """Repeated amounts to the same payee."""
    seen: dict[tuple[str, float], int] = {}
    findings = []
    for i, row in enumerate(_rows(transactions)):
        description = str(row.get("description") or "").strip().lower()
        amount = _field_number(row, "amount")
        if amount is None or not description:
            continue
        key = (description, amount)
        if key in seen:
            findings.append(_finding(
                "STAT.DUPLICATES", "signal",
                f"Transaction {i + 1} repeats the same payee and amount as transaction {seen[key] + 1}",
                field="transactions",
            ))
        seen[key] = i
    return findings, None


@check(code="STAT.ROUND_NUMBERS", applies=("bank_statement",))
def _check_round_numbers(*, transactions: Any) -> CheckResult:
    """An unusually high share of round amounts."""
    amounts = [a for a in (_field_number(r, "amount") for r in _rows(transactions)) if a]
    if len(amounts) < BENFORD_MIN_SAMPLE:
        return [], skipped(reason="fewer than 100 amounts")
    round_share = sum(1 for a in amounts if abs(a) % 100 < 0.005) / len(amounts)
    if round_share > 0.30:
        return [_finding(
            "STAT.ROUND_NUMBERS", "signal",
            f"{round_share:.0%} of amounts are round multiples of 100 — higher than typical",
            field="transactions",
        )], None
    return [], None


@check(code="STAT.LAST_TWO_DIGITS", applies=("bank_statement",))
def _check_last_two_digits(*, transactions: Any) -> CheckResult:
    """The last two digits (cents) should be close to uniformly distributed."""
    cents = []
    for row in _rows(transactions):
        amount = _field_number(row, "amount")
        if amount is not None and abs(round(amount, 2) - round(amount, 0)) > 0.001:
            cents.append(int(round(abs(amount) * 100)) % 100)
    if len(cents) < BENFORD_MIN_SAMPLE:
        return [], skipped(reason="fewer than 100 decimal amounts")
    expected = len(cents) / 100
    chi_square = sum((cents.count(d) - expected) ** 2 / expected for d in range(100))
    # 99 degrees of freedom, p < 0.01 critical value.
    if chi_square > 135.807:
        return [_finding(
            "STAT.LAST_TWO_DIGITS", "signal",
            f"The last two digits of amounts are not uniformly distributed (chi-square {chi_square:.1f})",
            field="transactions", severity_note="Statistical, not per-figure",
        )], None
    return [], None


def _run_benford_pair(transactions: Any) -> tuple[BenfordAnalysis | None, BenfordAnalysis | None, list[NumericalFinding]]:
    """STAT.BENFORD_1D, run twice: once on all amounts, once excluding likely bank fees."""
    rows = _rows(transactions)
    if not rows:
        return None, None, []
    all_amounts = [a for a in (_field_number(r, "amount") for r in rows) if a]
    non_fee_amounts = [
        _field_number(r, "amount") for r in rows if not _BANK_CHARGE_KEYWORDS.search(str(r.get("description") or ""))
    ]
    non_fee_amounts = [a for a in non_fee_amounts if a]

    all_analysis, all_findings = _benford(all_amounts, "STAT.BENFORD_1D")
    excl_analysis, excl_findings = _benford(non_fee_amounts, "STAT.BENFORD_1D")
    return all_analysis, excl_analysis, all_findings + excl_findings


# ---------------------------------------------------------------------------
# Source of wealth
# ---------------------------------------------------------------------------


@check(code="SOW.HOLDING_VALUE", applies=("source_of_wealth",))
def _check_holding_value(*, holdings: Any) -> CheckResult:
    """Quantity times closing price must equal market value."""
    findings = []
    for i, row in enumerate(_rows(holdings)):
        quantity, price, market_value = (
            _field_number(row, "quantity"), _field_number(row, "closing_price"), _field_number(row, "market_value"),
        )
        if quantity is None or price is None or market_value is None:
            continue
        expected = quantity * price
        if abs(expected - market_value) > max(0.51, abs(expected) * 0.01):
            findings.append(_finding(
                "SOW.HOLDING_VALUE", "impossible",
                f"Holding {i + 1}: {quantity:g} x {_money(price)} = {_money(expected)}, but market value shows "
                f"{_money(market_value)}",
                field=f"holdings[{i}].market_value", stated=_money(market_value), expected=_money(expected),
            ))
    return findings, None


@check(code="SOW.PORTFOLIO_SUM", applies=("source_of_wealth",))
def _check_portfolio_sum(*, holdings: Any, portfolio_value: Num) -> CheckResult:
    """Holdings must add up to the stated portfolio value."""
    market_values = [_field_number(r, "market_value") for r in _rows(holdings)]
    numeric_values = [v for v in market_values if v is not None]
    if not market_values or len(numeric_values) != len(market_values):
        return [], skipped(reason="holding market values not all readable")
    total = sum(numeric_values)
    if abs(total - portfolio_value) > _MONEY_TOLERANCE:
        return [_finding(
            "SOW.PORTFOLIO_SUM", "impossible",
            f"Holdings sum to {_money(total)}, but the stated portfolio value is {_money(portfolio_value)}",
            field="portfolio_value", stated=_money(portfolio_value), expected=_money(total),
            severity_note=_materiality_note(total - portfolio_value, total),
        )], None
    return [], None


@check(code="SOW.NAV", applies=("source_of_wealth",))
def _check_nav(
    *, cash_balance: Num, portfolio_value: Num, net_asset_value: Num, margin_loan: Num | None = None,
) -> CheckResult:
    """Cash plus portfolio value minus margin loan must equal net asset value."""
    margin = margin_loan or 0.0
    expected = cash_balance + portfolio_value - margin
    if abs(expected - net_asset_value) > _MONEY_TOLERANCE:
        return [_finding(
            "SOW.NAV", "impossible",
            f"Cash {_money(cash_balance)} + portfolio {_money(portfolio_value)} - margin loan {_money(margin)} = "
            f"{_money(expected)}, but net asset value shows {_money(net_asset_value)}",
            field="net_asset_value", stated=_money(net_asset_value), expected=_money(expected),
            severity_note=_materiality_note(expected - net_asset_value, expected),
        )], None
    return [], None


@check(code="FS.RE_ROLLFORWARD", applies=("source_of_wealth",))
def _check_fs_re_rollforward(
    *, retained_earnings_opening: Num, profit_for_the_year: Num, dividends_paid: Num,
    retained_earnings_closing: Num, retained_earnings_closing_prior_year: Num | None = None,
) -> CheckResult:
    """Retained earnings roll forward (opening plus profit minus dividends is closing), and last year's closing
    equals this year's opening."""
    findings = []
    expected_closing = retained_earnings_opening + profit_for_the_year - dividends_paid
    if abs(expected_closing - retained_earnings_closing) > _MONEY_TOLERANCE:
        findings.append(_finding(
            "FS.RE_ROLLFORWARD", "impossible",
            f"Retained earnings: opening {_money(retained_earnings_opening)} + profit {_money(profit_for_the_year)} - "
            f"dividends {_money(dividends_paid)} = {_money(expected_closing)}, but closing shows "
            f"{_money(retained_earnings_closing)}",
            field="retained_earnings_closing", stated=_money(retained_earnings_closing),
            expected=_money(expected_closing),
            severity_note=_materiality_note(expected_closing - retained_earnings_closing, expected_closing),
        ))
    if retained_earnings_closing_prior_year is not None and (
        abs(retained_earnings_closing_prior_year - retained_earnings_opening) > _MONEY_TOLERANCE
    ):
        findings.append(_finding(
            "FS.RE_ROLLFORWARD", "impossible",
            f"This year's opening retained earnings ({_money(retained_earnings_opening)}) does not equal last year's "
            f"closing figure ({_money(retained_earnings_closing_prior_year)})",
            field="retained_earnings_opening", stated=_money(retained_earnings_opening),
            expected=_money(retained_earnings_closing_prior_year),
        ))
    return findings, None


@check(code="FS.PPE_ROLLFORWARD", applies=("source_of_wealth",))
def _check_fs_ppe_rollforward(*, ppe_opening: Num, ppe_closing: Num, depreciation_line: Any) -> CheckResult:
    """Closing = opening + additions - disposals - depreciation. With no disposals field, a negative implied
    addition is unexplained, not impossible: disposals or impairments would explain it."""
    depreciation = _magnitude(depreciation_line)
    if depreciation is None:
        return [], skipped(reason="depreciation amount is not a number")
    implied_additions = ppe_closing - ppe_opening + depreciation
    if implied_additions < -_MONEY_TOLERANCE:
        return [_finding(
            "FS.PPE_ROLLFORWARD", "inconsistent",
            f"Unexplained PPE movement: closing {_money(ppe_closing)} is {_money(-implied_additions)} lower than "
            f"opening {_money(ppe_opening)} less depreciation {_money(depreciation)}, which additions cannot explain. "
            "Disposals or impairments may account for it.",
            field="ppe_closing", stated=_money(ppe_closing), expected=f">= {_money(ppe_opening - depreciation)}",
        )], None
    return [], None


@check(code="FS.TAX_EXPENSE_RATE", applies=("source_of_wealth",))
def _check_fs_tax_expense_rate(*, profit_before_tax_line: Any, tax_expense_line: Any) -> CheckResult:
    """Tax expense should be close to 17% of profit after the partial tax exemption, not a flat 17% of profit before
    exemption."""
    profit = row_amount(profit_before_tax_line)
    tax = _magnitude(tax_expense_line)
    if profit is None or tax is None:
        return [], skipped(reason="profit or tax amount is not a number")
    if profit <= 0:
        return [], skipped(reason="profit before tax is not positive")
    flat_rate_tax = profit * _CURRENT_NOA_SCHEME["tax_rate_pct"]
    if abs(tax - flat_rate_tax) < max(1.0, flat_rate_tax * 0.02):
        return [_finding(
            "FS.TAX_EXPENSE_RATE", "signal",
            f"Tax expense ({_money(tax)}) is a flat {_CURRENT_NOA_SCHEME['tax_rate_pct']:.0%} of profit before tax "
            "with no sign of the partial tax exemption a genuine Singapore company would apply",
            field="tax_expense", stated=_money(tax),
        )], None
    return [], None


@check(code="FS.DEBT_CLASSIFICATION", applies=("source_of_wealth",))
def _check_fs_debt_classification(
    *, borrowings_current: Num, loan_tenure: Num, borrowings_non_current: Num | None = None,
) -> CheckResult:
    """A term loan repaid in instalments should have a non-current portion."""
    if loan_tenure <= 12:
        return [], skipped(reason="loan tenure is 12 months or less")
    if borrowings_current > 0 and (borrowings_non_current is None or borrowings_non_current <= 0):
        return [_finding(
            "FS.DEBT_CLASSIFICATION", "inconsistent",
            f"A loan with a {loan_tenure:.0f}-month tenure is classified entirely as current, with no non-current "
            "portion",
            field="borrowings_non_current",
        )], None
    return [], None


_RATIO_BANDS = {
    "finance_cost_ratio": (0.0, 0.20),
    "depreciation_ratio": (0.0, 0.40),
    "gross_margin": (-0.5, 0.95),
}


@check(code="FS.RATIO_BANDS", applies=("source_of_wealth",))
def _check_fs_ratio_bands(
    *, borrowings_opening: Num | None = None, borrowings_closing: Num | None = None, ppe_closing: Num | None = None, revenue: Num | None = None,
    finance_costs_line: Any = None, depreciation_line: Any = None, cost_of_sales_line: Any = None,
) -> CheckResult:
    """Finance costs over average borrowings, depreciation over PPE, and gross margin must each fall within a
    configured band."""
    findings = []
    ran_any = False

    costs = _magnitude(finance_costs_line)
    if costs is not None and borrowings_opening is not None and borrowings_closing is not None and (
        borrowings_opening + borrowings_closing
    ) > 0:
        ran_any = True
        ratio = costs / ((borrowings_opening + borrowings_closing) / 2)
        lo, hi = _RATIO_BANDS["finance_cost_ratio"]
        if not lo <= ratio <= hi:
            findings.append(_finding(
                "FS.RATIO_BANDS", "implausible",
                f"Finance costs are {ratio:.1%} of average borrowings, outside the {lo:.0%}-{hi:.0%} plausible band",
                field="finance_costs",
            ))

    depreciation = _magnitude(depreciation_line)
    if depreciation is not None and ppe_closing is not None and ppe_closing > 0:
        ran_any = True
        ratio = depreciation / ppe_closing
        lo, hi = _RATIO_BANDS["depreciation_ratio"]
        if not lo <= ratio <= hi:
            findings.append(_finding(
                "FS.RATIO_BANDS", "implausible",
                f"Depreciation is {ratio:.1%} of PPE, outside the {lo:.0%}-{hi:.0%} plausible band",
                field="depreciation_expense",
            ))

    cost = _magnitude(cost_of_sales_line)
    if revenue is not None and cost is not None and revenue > 0:
        ran_any = True
        ratio = (revenue - cost) / revenue
        lo, hi = _RATIO_BANDS["gross_margin"]
        if not lo <= ratio <= hi:
            findings.append(_finding(
                "FS.RATIO_BANDS", "implausible",
                f"Gross margin is {ratio:.1%}, outside the {lo:.0%}-{hi:.0%} plausible band",
                field="revenue",
            ))

    if not ran_any:
        return [], skipped(reason="no ratio had all its inputs")
    return findings, None


@check(code="FS.NOTE_REFS", applies=("source_of_wealth",))
def _check_fs_note_refs(*, notes: Any = None, income_statement_items: Any = None, balance_sheet_items: Any = None) -> CheckResult:
    """Every note referenced by a statement line must exist in the notes."""
    item_rows = [*_rows(income_statement_items), *_rows(balance_sheet_items)]
    if notes is None or not item_rows:
        return [], skipped(reason="notes or statement lines not extracted")
    note_numbers = {str(n.get("note_number")).strip() for n in _rows(notes) if n.get("note_number")}
    referenced = {str(r.get("note")).strip() for r in item_rows if r.get("note")}
    missing = referenced - note_numbers
    if missing:
        return [_finding(
            "FS.NOTE_REFS", "signal",
            f"Note(s) {', '.join(sorted(missing))} are referenced but not present",
            field="notes",
        )], None
    return [], None


# ---------------------------------------------------------------------------
# Notice of assessment
# ---------------------------------------------------------------------------


def _magnitude(row: Any) -> float | None:
    """An expense line's size, whichever sign it was printed with."""
    amount = row_amount(row)
    return abs(amount) if amount is not None else None


def _noa_scheme(year_of_assessment: int | None) -> dict:
    if year_of_assessment is not None:
        override = REFERENCE_DATA["noa_tax_parameters"].get(year_of_assessment)
        if override:
            return override
    return _CURRENT_NOA_SCHEME


def _compute_noa(eci: float, scheme: dict) -> dict[str, float]:
    first = min(eci, scheme["exempt_first_cap"])
    nxt = min(max(eci - scheme["exempt_first_cap"], 0), scheme["exempt_next_cap"])
    exempt_first = round(first * scheme["exempt_first_pct"])
    exempt_next = round(nxt * scheme["exempt_next_pct"])
    after = eci - exempt_first - exempt_next
    tax = round(after * scheme["tax_rate_pct"], 2)
    rebate = round(min(tax * scheme["rebate_pct"], scheme["rebate_cap"]), 2)
    payable = round(tax - rebate, 2)
    # A company that received the CIT Rebate Cash Grant has the rebate printed "net of CIT Rebate Cash Grant",
    # so both readings are genuine.
    rebate_net = round(max(rebate - scheme.get("cash_grant", 0), 0), 2)
    payable_net = round(tax - rebate_net, 2)
    return {"exempt_first": exempt_first, "exempt_next": exempt_next, "after": after,
            "tax": tax, "rebate": rebate, "payable": payable,
            "rebate_net": rebate_net, "payable_net": payable_net}


def _noa_year(year_of_assessment: Any) -> int | None:
    match = re.search(r"\d{4}", str(year_of_assessment or ""))
    return int(match.group(0)) if match else None


@check(code="NOA.PAYABLE", applies=("notice_of_assessment",))
def _check_noa_chain(
    *, eci: Num, tax_payable: Num, year_of_assessment: Any = None, tax_computation: Any = None,
) -> CheckResult:
    """The tax computation runs as one chain from ECI to payable tax: exemption tiers, chargeable income after
    exemption, tax at the rate, rebate, payable, and any amendment. Uses the Singapore partial tax exemption
    scheme unless REFERENCE_DATA has an override for the year of assessment."""
    scheme = _noa_scheme(_noa_year(year_of_assessment))
    computed = _compute_noa(eci, scheme)

    findings = []
    lines = [(str(r.get("line", "")).strip().lower(), _field_number(r, "amount")) for r in _rows(tax_computation)]

    def _find_line(pattern: str) -> float | None:
        regex = re.compile(pattern)
        return next((amount for label, amount in lines if regex.search(label)), None)

    for code_label, pattern, expected in (
        ("first", r"^first\b", computed["exempt_first"]),
        ("next", r"^next\b", computed["exempt_next"]),
    ):
        stated_tier = _find_line(pattern)
        if stated_tier is not None and abs(stated_tier - expected) > _MONEY_TOLERANCE:
            findings.append(_finding(
                "NOA.EXEMPTION_TIERS", "impossible",
                f"The \"{code_label}\" exemption tier should be {_money(expected)} for ECI {_money(eci)}, but the "
                f"computation shows {_money(stated_tier)}",
                field="tax_computation", stated=_money(stated_tier), expected=_money(expected),
            ))

    stated_after = _find_line(r"chargeable income after")
    if stated_after is not None and abs(stated_after - computed["after"]) > _MONEY_TOLERANCE:
        findings.append(_finding(
            "NOA.CHARGEABLE_AFTER_EXEMPTION", "impossible",
            f"Chargeable income after exemption should be {_money(computed['after'])} for ECI {_money(eci)}, but the "
            f"computation shows {_money(stated_after)}",
            field="tax_computation", stated=_money(stated_after), expected=_money(computed["after"]),
        ))

    stated_tax = _find_line(r"^tax on\b") or _find_line(r"^tax assessed\b")
    if stated_tax is not None and abs(stated_tax - computed["tax"]) > _MONEY_TOLERANCE:
        findings.append(_finding(
            "NOA.TAX_AT_RATE", "impossible",
            f"Tax at {scheme['tax_rate_pct']:.0%} should be {_money(computed['tax'])}, but the computation shows "
            f"{_money(stated_tax)}",
            field="tax_computation", stated=_money(stated_tax), expected=_money(computed["tax"]),
        ))

    def _matches_either(stated: float, *expected: float) -> bool:
        return any(abs(stated - e) <= _MONEY_TOLERANCE for e in expected)

    stated_rebate = _find_line(r"\brebate\b")
    if stated_rebate is not None and not _matches_either(abs(stated_rebate), computed["rebate"], computed["rebate_net"]):
        net_note = (
            f" or {_money(computed['rebate_net'])} net of the {_money(scheme['cash_grant'])} cash grant"
            if scheme.get("cash_grant") else ""
        )
        findings.append(_finding(
            "NOA.REBATE", "impossible",
            f"Tax rebate should be {_money(computed['rebate'])}{net_note} (capped at {_money(scheme['rebate_cap'])}), "
            f"but the computation shows {_money(stated_rebate)}",
            field="tax_computation", stated=_money(stated_rebate), expected=_money(computed["rebate"]),
        ))

    if not _matches_either(tax_payable, computed["payable"], computed["payable_net"]):
        findings.append(_finding(
            "NOA.PAYABLE", "impossible",
            f"Tax payable should be {_money(computed['payable'])} for ECI {_money(eci)} under the current partial tax "
            f"exemption scheme, but the notice states {_money(tax_payable)}",
            field="tax_payable", stated=_money(tax_payable), expected=_money(computed["payable"]),
        ))

    # An amended assessment nets the new figure against the previous one:
    # payable - previous = additional tax due (positive) or discharged (negative).
    previous = _find_line(r"previous assessment")
    settled = _find_line(r"discharged")
    additional = _find_line(r"additional tax|balance payable")
    if previous is not None and (settled is not None or additional is not None):
        difference = tax_payable - abs(previous)
        stated_diff = -abs(settled) if settled is not None else abs(additional or 0.0)
        if abs(difference - stated_diff) > _MONEY_TOLERANCE:
            findings.append(_finding(
                "NOA.AMENDMENT", "impossible",
                f"Tax payable {_money(tax_payable)} less the previous assessment {_money(abs(previous))} is "
                f"{_money(difference)}, but the notice states "
                f"{'a discharge of ' + _money(abs(stated_diff)) if settled is not None else 'additional tax of ' + _money(stated_diff)}",
                field="tax_computation", stated=_money(abs(stated_diff)), expected=_money(abs(difference)),
            ))
    return findings, None
