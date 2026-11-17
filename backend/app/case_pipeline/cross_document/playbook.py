"""
Candidate hypotheses the planner is shown, computed by code from which documents the case has and what the
cross-document measures (signals.py) already say about them: a payslip whose net pay doesn't arrive, revenue the
statements don't carry, assessed income far from the reported profit, money going round, receipts from one customer,
months no statement covers. Each line names the tool to test it with, so the planner sharpens real leads instead of
inventing from scratch. A candidate is never a finding: the agent still tests it, and verify.py still decides.

Strongest first; at most MAX_CANDIDATES. No model call, nothing written.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...models import is_personal_bank_statement
from . import signals
from .evidence import CaseContext, DocView

MAX_CANDIDATES = 5
# Annualised credits this far from revenue (either way) are a strong lead.
REVENUE_RATIO_LOW, REVENUE_RATIO_HIGH = 0.5, 2.0
# Assessed income this far from the profit before tax.
ASSESSED_GAP = 0.25


@dataclass(frozen=True)
class Candidate:
    strength: int  # 3 strong, 2 worth testing, 1 a routine cross-check
    text: str


def _of_type(context: CaseContext, document_type: str) -> list[DocView]:
    return [v for v in context.docs.values() if v.document_type == document_type]


def _statements(context: CaseContext, personal: bool) -> list[DocView]:
    return [v for v in _of_type(context, "bank_statement")
            if is_personal_bank_statement(v.document_type, v.fields) is personal]


def _accounts(context: CaseContext) -> list[DocView]:
    return [v for v in _of_type(context, "source_of_wealth") if signals.revenue(v.fields) or signals.profit_before_tax(v.fields)]


def _salary(context: CaseContext) -> list[Candidate]:
    out = []
    statements = _statements(context, personal=True) or _statements(context, personal=False)
    for slip in _of_type(context, "payslip"):
        for account in statements:
            result = signals.salary_credit(slip.fields, account.fields)
            if not result.get("available"):
                continue
            check = f"salary_credit_match(payslip={slip.key}, statement={account.key})"
            if result["matching_credits"]:
                out.append(Candidate(1, f"{slip.key}'s net pay appears on {account.key}: confirm the employer named "
                                        f"on the credit is the payslip's company. Test with {check}."))
            elif result.get("pay_date_in_statement_period") is not False:
                out.append(Candidate(3, f"{slip.key}'s net pay {result['net_pay']:,.2f} has no matching credit on "
                                        f"{account.key} near the pay date (nearest differs by "
                                        f"{result['nearest_difference']:,.2f}). Test with {check}."))
    return out


def _revenue(context: CaseContext) -> list[Candidate]:
    statements = _statements(context, personal=False)
    if not statements:
        return []
    out = []
    for accounts in _accounts(context):
        result = signals.revenue_against_credits(accounts.fields, [s.fields for s in statements])
        if not result.get("available"):
            continue
        ratio = result["annualised_credits_to_revenue"]
        keys = ", ".join(s.key for s in statements)
        check = f"revenue_vs_bank_credits(financial_statement={accounts.key}, statements=[{keys}])"
        strength = 3 if ratio < REVENUE_RATIO_LOW or ratio > REVENUE_RATIO_HIGH else 1
        out.append(Candidate(strength, f"{accounts.key}'s revenue against the credits on {keys}: annualised credits "
                                       f"are {ratio:.2f} times revenue. Test with {check}; cross-check with "
                                       f"top_counterparties on the statements."))
    return out


def _tax(context: CaseContext) -> list[Candidate]:
    out = []
    for notice in _of_type(context, "notice_of_assessment"):
        for accounts in _accounts(context):
            result = signals.assessed_against_profit(notice.fields, accounts.fields)
            ratio = result.get("assessed_to_profit") if result.get("available") else None
            if ratio is None:
                continue
            check = f"assessed_income_vs_profit(notice_of_assessment={notice.key}, financial_statement={accounts.key})"
            strength = 3 if abs(ratio - 1) > ASSESSED_GAP else 1
            out.append(Candidate(strength, f"The income {notice.key} assessed is {ratio:.2f} times the profit before "
                                           f"tax in {accounts.key} (check the years line up). Test with {check}."))
    return out


def _money_flows(context: CaseContext) -> list[Candidate]:
    out = []
    for account in _statements(context, personal=False):
        own = signals.value(account.fields, "entity_name")
        trips = signals.round_trips(account.fields, own)
        if trips:
            top = trips[0]
            out.append(Candidate(2, f"{len(trips)} counterparty(ies) on {account.key} both pay in and are paid "
                                    f"(largest {min(top['paid_in'], top['paid_out']):,.0f} each way): money may be "
                                    f"going round. Test with counterparty_round_trips(document={account.key}); "
                                    f"refute against an invoice, contract or the accounts' related-party notes."))
        share = signals.concentration(account.fields, own)
        if share.get("available") and share["credits"] >= signals.CONCENTRATION_MIN_CREDITS \
                and share["top_share"] >= signals.CONCENTRATION_SHARE:
            out.append(Candidate(2, f"{share['top_share']:.0%} of the receipts on {account.key} come from one "
                                    f"counterparty. Test with customer_concentration(document={account.key}) and "
                                    f"compare with the business profile or the accounts."))
    return out


def _coverage(context: CaseContext) -> list[Candidate]:
    statements = _of_type(context, "bank_statement")
    if len(statements) < 2:
        return []
    missing = signals.missing_months([signals.covered_months(s.fields) for s in statements])
    if not missing:
        return []
    return [Candidate(2, f"No statement covers {', '.join(missing)}, between months the others cover: check balances "
                         f"carry over with compare_periods and statement_coverage().")]


def _loans(context: CaseContext) -> list[Candidate]:
    out = []
    facilities = _of_type(context, "list_of_loans")
    for account in _of_type(context, "bank_statement"):
        repayments = signals.loan_repayments(account.fields)
        if facilities:
            for facility in facilities:
                instalment = signals.number(facility.fields, "monthly_instalment")
                if instalment:
                    out.append(Candidate(2, f"{facility.key} sets a monthly instalment of {instalment:,.2f}: look for it "
                                            f"on {account.key} with query_transactions(document={account.key}, "
                                            f"direction=debit, min_amount={instalment * 0.98:.2f}, "
                                            f"max_amount={instalment * 1.02:.2f})."))
        elif repayments:
            out.append(Candidate(1, f"{account.key} shows {len(repayments)} loan repayment(s) but the case has no "
                                    f"facility letter: compare them with the accounts' borrowings "
                                    f"(query_transactions, description_contains=LOAN)."))
    return out


def candidates(context: CaseContext) -> list[Candidate]:
    found = [*_salary(context), *_revenue(context), *_tax(context), *_money_flows(context), *_coverage(context),
             *_loans(context)]
    # Stable: equal strengths keep the order above.
    return sorted(found, key=lambda c: -c.strength)[:MAX_CANDIDATES]


def render(context: CaseContext) -> str:
    """The planner's candidate block, or "" when the case's documents suggest nothing."""
    lines = [f"- [{'strong' if c.strength == 3 else 'worth testing' if c.strength == 2 else 'routine'}] {c.text}"
             for c in candidates(context)]
    return "\n".join(lines)


def _period_of(view: DocView) -> str | None:
    if view.document_type == "bank_statement":
        months = signals.covered_months(view.fields)
        return f"{months[0]} to {months[-1]}" if months else None
    for name in ("financial_year_end", "year_of_assessment", "pay_date", "pay_period_end", "bill_date",
                 "billing_period_end", "report_date", "document_date", "certificate_date", "noa_date"):
        found = signals.value(view.fields, name)
        if found not in (None, ""):
            return f"{name.replace('_', ' ')} {found}"
    return None


def overview(context: CaseContext) -> str:
    """The case at a glance for the planner: each document's type and the period it speaks for, and what's missing."""
    # Imported here: mapper imports the models this package's callers also load.
    from ...mapper import compute_requirement_coverage

    lines = []
    for key, view in context.docs.items():
        kind = view.document_type
        if kind == "bank_statement":
            kind = "personal bank statement" if is_personal_bank_statement(view.document_type, view.fields) \
                else "company bank statement"
        when = _period_of(view)
        lines.append(f"- {key}: {kind}" + (f" ({when})" if when else ""))
    _, missing = compute_requirement_coverage(context.records)
    if missing:
        lines.append(f"- Not on file from the checklist: {', '.join(m.replace('_', ' ') for m in missing)}")
    return "\n".join(lines)
