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
    _MONEY_TOLERANCE,
    CheckResult,
    _as_number,
    _finding,
    _materiality_note,
    _money,
    _parse_field_date,
    _rows,
    row_amount,
)
from ..external_validation.sg_reference.corporate_tax import TAX_RATE
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
    # A brought/carried-forward line whose balance is not the one before it (amount 0).
    carry_over: bool = False


_SYSTEMIC_BREAK_RATIO = 0.25
_SYSTEMIC_MIN_BREAKS = 4

# "Balance brought forward", "B/F", "Opening balance" and their closing counterparts: lines that carry a
# balance but are not transactions. Extraction sometimes lists them as rows with no amount.
_CARRY_OVER_RE = re.compile(
    r"\b(brought|carried)\s+(forward|fwd)\b|\b[bc]\s*/\s*f\b|\b(opening|closing)\s+balance\b", re.IGNORECASE
)


def _is_carry_over(row: dict) -> bool:
    return _field_number(row, "amount") is None and bool(_CARRY_OVER_RE.search(str(row.get("description") or "")))


def _description(row: dict) -> str:
    return str(row.get("description") or "").strip()


def _running_balance_breaks(rows: list[dict]) -> list[_BalanceBreak]:
    """
    Lines where the balance does not follow from the one before. A brought/carried-forward line must repeat
    the previous balance, so a jump hidden at a page or month break is caught, not restarted from. Any other
    line with no amount can't be checked; _unverifiable_steps reports those.
    """
    breaks: list[_BalanceBreak] = []
    previous: float | None = None
    for index, row in enumerate(rows):
        balance = _field_number(row, "balance")
        amount = _field_number(row, "amount")
        if amount is None:
            if _is_carry_over(row) and previous is not None and balance is not None \
                    and abs(balance - previous) > _MONEY_TOLERANCE:
                breaks.append(_BalanceBreak(
                    index=index, previous=previous, amount=0.0, balance=balance, expected=previous,
                    description=_description(row), carry_over=True,
                ))
            if balance is not None:
                previous = balance
            continue
        if previous is None:
            previous = balance
            continue
        expected = previous + amount
        if balance is None:
            previous = expected
            continue
        if abs(expected - balance) > _MONEY_TOLERANCE:
            breaks.append(_BalanceBreak(
                index=index, previous=previous, amount=amount, balance=balance,
                expected=expected, description=_description(row),
                sign_inverted=abs(previous - amount - balance) <= _MONEY_TOLERANCE,
            ))
        previous = balance
    return breaks


def _unverifiable_steps(rows: list[dict]) -> list[tuple[int, float, float]]:
    """(index, previous balance, balance) for transaction lines with a balance but no amount, other than
    brought/carried-forward lines: the move into them can't be checked against anything."""
    steps: list[tuple[int, float, float]] = []
    previous: float | None = None
    for index, row in enumerate(rows):
        balance = _field_number(row, "balance")
        if _field_number(row, "amount") is None and not _is_carry_over(row) and previous is not None and balance is not None:
            steps.append((index, previous, balance))
        if balance is not None:
            previous = balance
        elif previous is not None and (amount := _field_number(row, "amount")) is not None:
            previous += amount
    return steps


def _is_newest_first(rows: list[dict]) -> bool:
    """Whether a transaction list runs newest-first, worked out per statement: from the dates when they
    differ, otherwise from which direction the running balance reconciles in with fewer breaks."""
    dates = [d for d in (_parse_field_date(r.get("date")) for r in rows) if d is not None]
    if len(dates) >= 2 and dates[0] != dates[-1]:
        return dates[0] > dates[-1]
    return len(_running_balance_breaks(list(reversed(rows)))) < len(_running_balance_breaks(rows))


def _balance_finding(b: _BalanceBreak) -> NumericalFinding:
    where = f"transaction {b.index + 1}" + (f" ({b.description})" if b.description else "")
    if b.carry_over:
        return _finding(
            "BS.RUNNING_BALANCE", "impossible",
            f"Line {b.index + 1} ({b.description}) carries a balance of {_money(b.balance)}, but the line before "
            f"it closed at {_money(b.previous)}",
            field=f"transactions[{b.index}].balance", stated=_money(b.balance), expected=_money(b.previous),
        )
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

    newest_first = _is_newest_first(rows)
    ordered = list(reversed(rows)) if newest_first else rows
    last = len(rows) - 1

    def original(index: int) -> int:
        return last - index if newest_first else index

    breaks = [b.model_copy(update={"index": original(b.index)}) for b in _running_balance_breaks(ordered)]
    # A transaction with no amount used to restart the balance silently, which hid an edit made on that
    # very line (SCRUM-288 PI-23). Now it is reported: the move into it can't be checked.
    gaps = [
        _finding(
            "BS.RUNNING_BALANCE", "inconsistent",
            f"Transaction {original(i) + 1}" + (f" ({_description(ordered[i])})" if _description(ordered[i]) else "")
            + f" has no amount, so the balance moving from {_money(previous)} to {_money(balance)} "
            f"({balance - previous:+,.2f}) cannot be checked",
            field=f"transactions[{original(i)}].amount", stated="no amount", expected=f"{balance - previous:,.2f}",
        )
        for i, previous, balance in _unverifiable_steps(ordered)
    ]
    if not breaks:
        return gaps, None

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
        ), *gaps], None
    return [*(_balance_finding(b) for b in breaks), *gaps], None


@check(code="BS.OPEN_CLOSE", applies=("bank_statement",))
def _check_open_close(
    *, transactions: Any, ledger_balance: Num | None = None, opening_balance: Num | None = None,
) -> CheckResult:
    """Opening balance plus credits minus debits must equal the closing balance. The stated header balances
    are used as the anchors when present, and also cross-checked against the transaction-derived values.
    Brought/carried-forward lines are not transactions: they used to make this skip on almost every
    statement (SCRUM-288), and now anchor the opening and closing balance when the header gives none."""
    rows = _rows(transactions)
    if len(rows) >= 2 and _is_newest_first(rows):
        rows = list(reversed(rows))
    body = [r for r in rows if not _is_carry_over(r)]
    amounts = [_field_number(r, "amount") for r in body]
    balances = [_field_number(r, "balance") for r in body]
    if not body or any(a is None for a in amounts) or balances[0] is None or balances[-1] is None:
        # A transaction with no amount is reported by BS.RUNNING_BALANCE; the total can't be summed past it.
        return [], skipped(reason="a transaction has no amount or the first or last has no balance")
    first_balance, last_balance = balances[0], balances[-1]
    amounts = [a for a in amounts if a is not None]

    derived_opening = first_balance - amounts[0]
    derived_closing = last_balance
    carried_in = _field_number(rows[0], "balance") if _is_carry_over(rows[0]) else None
    carried_out = _field_number(rows[-1], "balance") if len(rows) > 1 and _is_carry_over(rows[-1]) else None
    opening = next(v for v in (opening_balance, carried_in, derived_opening) if v is not None)
    closing = next(v for v in (ledger_balance, carried_out, derived_closing) if v is not None)
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
    if opening_balance is None and carried_in is None:
        # Nothing printed to start from: the opening is worked back from the first line, which proves nothing about it.
        findings.append(_finding(
            "BS.OPEN_CLOSE", "signal",
            f"The statement prints no opening balance, so its earliest transaction ({_money(amounts[0])}, "
            f"{_description(body[0]) or 'no description'}) can't be checked",
            field="transactions", stated="no opening balance", expected="an opening or brought-forward balance",
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


_CARD_RE = re.compile(r"\b\d{4}-[Xx*]{4}-[Xx*]{4}-\d{4}\b|\b[Xx*]{4,}\d{4}\b")


def card_usage(rows: list[dict]) -> tuple[int, set[str]]:
    """(card transactions, the distinct masked card numbers on them). Used by the cross-document card_usage tool."""
    card_txns = 0
    cards: set[str] = set()
    for row in rows:
        match = _CARD_RE.search(str(row.get("description") or ""))
        if match:
            card_txns += 1
            cards.add(match.group(0))
    return card_txns, cards


def recurring_payments(rows: list[dict]) -> dict[str, list[float]]:
    """Amounts per recurring payee (three or more payments, fees and charges left out). Digits are masked in the payee."""
    by_payee: dict[str, list[float]] = {}
    for row in rows:
        description = str(row.get("description") or "").strip().lower()
        amount = _field_number(row, "amount")
        if description and amount is not None:
            by_payee.setdefault(re.sub(r"\d+", "#", description), []).append(abs(amount))
    return {p: a for p, a in by_payee.items() if len(a) >= 3 and not _FEE_KEYWORDS.search(p)}


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


def repeated_payments(rows: list[dict]) -> list[tuple[str, float, list[int]]]:
    """(payee, amount, transaction indexes) for each payee and amount that appears more than once."""
    groups: dict[tuple[str, float], list[int]] = {}
    for i, row in enumerate(rows):
        description = str(row.get("description") or "").strip().lower()
        amount = _field_number(row, "amount")
        if amount is not None and description:
            groups.setdefault((description, amount), []).append(i)
    return [(d, a, idx) for (d, a), idx in groups.items() if len(idx) > 1]


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
    """The last two digits (cents) should be close to uniformly distributed, once fees and charges (often one fixed amount, many times) are left out."""
    cents = []
    for row in _rows(transactions):
        if _BANK_CHARGE_KEYWORDS.search(str(row.get("description") or "")):
            continue
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
    """
    Holdings must add up to the stated portfolio value. A holding with no market value is valued at
    quantity x price when both are printed. One with neither doesn't stop the check (that let a total
    120,000.00 above its holdings pass unremarked, SCRUM-288 PI-24): valued holdings that already exceed
    the total disprove it, and a total that rests on unvalued holdings is reported for review.
    """
    rows = _rows(holdings)
    if not rows:
        return [], skipped(reason="no holdings extracted")
    total = 0.0
    unvalued: list[dict] = []
    for row in rows:
        value = _field_number(row, "market_value")
        if value is None:
            quantity, price = _field_number(row, "quantity"), _field_number(row, "closing_price")
            value = quantity * price if quantity is not None and price is not None else None
        if value is None:
            unvalued.append(row)
        else:
            total += value

    # A short position (negative quantity) is worth less than nothing, so it could pull the total back down.
    has_short = any((_field_number(row, "quantity") or 0) < 0 for row in unvalued)
    disproved = bool(unvalued) and not has_short and total > portfolio_value + _MONEY_TOLERANCE
    if not unvalued or disproved:
        if abs(total - portfolio_value) > _MONEY_TOLERANCE:
            valued = "Holdings sum" if not unvalued else "The holdings with a value already sum"
            return [_finding(
                "SOW.PORTFOLIO_SUM", "impossible",
                f"{valued} to {_money(total)}, but the stated portfolio value is {_money(portfolio_value)}",
                field="portfolio_value", stated=_money(portfolio_value), expected=_money(total),
                severity_note=_materiality_note(total - portfolio_value, total),
            )], None
        return [], None

    unexplained = portfolio_value - total
    if abs(unexplained) <= _MONEY_TOLERANCE:
        return [], None
    names = ", ".join(str(row.get("name") or "unnamed holding") for row in unvalued)
    share = f" ({abs(unexplained) / abs(portfolio_value) * 100:.0f}%)" if portfolio_value else ""
    return [_finding(
        "SOW.PORTFOLIO_SUM", "inconsistent",
        f"{_money(unexplained)} of the stated portfolio value {_money(portfolio_value)}{share} rests on "
        f"holding(s) with no market value or price shown ({names}); the total cannot be confirmed",
        field="portfolio_value", stated=_money(portfolio_value), expected=f"{_money(total)} from valued holdings",
        severity_note=_materiality_note(unexplained, portfolio_value),
    )], None


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
    flat_rate_tax = profit * TAX_RATE
    if abs(tax - flat_rate_tax) < max(1.0, flat_rate_tax * 0.02):
        return [_finding(
            "FS.TAX_EXPENSE_RATE", "signal",
            f"Tax expense ({_money(tax)}) is a flat {TAX_RATE:.0%} of profit before tax "
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
