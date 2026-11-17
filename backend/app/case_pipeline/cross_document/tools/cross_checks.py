"""Tools that measure one document against another (signals.py does the counting)."""

from __future__ import annotations

from langchain_core.tools import tool

from ....models import is_personal_bank_statement
from .. import signals
from ..evidence import mask_identifiers
from .common import ToolKit, statement_rows, unavailable


def build_cross_check_tools(kit: ToolKit) -> list:
    context = kit.context

    def _two(tool_name: str, args: dict, first: str, second: str, probe: bool):
        """Both documents, or the logged refusal."""
        a, error = kit.doc(first)
        if a is None:
            return None, None, kit.log(tool_name, args, error or {}, probe)
        b, error = kit.doc(second)
        if b is None:
            return None, None, kit.log(tool_name, args, error or {}, probe, a.key)
        return a, b, None

    @tool
    async def salary_credit_match(payslip: str, statement: str, is_refutation_probe: bool = False) -> str:
        """Whether a payslip's net pay arrives as a credit on a bank statement near the pay date: matching credits, or the nearest one and how far off it is."""
        args = {"payslip": payslip, "statement": statement}
        slip, account, refused = _two("salary_credit_match", args, payslip, statement, is_refutation_probe)
        if refused is not None:
            return refused
        assert slip is not None and account is not None
        if statement_rows(account) is None:
            return kit.log("salary_credit_match", args, unavailable("The statement has no transactions."),
                           is_refutation_probe, account.key)
        result = signals.salary_credit(slip.fields, account.fields)
        if not result.get("available", True):
            return kit.log("salary_credit_match", args, result, is_refutation_probe, slip.key)
        ref = context.ledger.issue(account.key, f"salary_credit[{slip.key}]", result, source="salary_credit_match")
        return kit.log("salary_credit_match", args, {**result, "ref": ref}, is_refutation_probe, slip.key,
                       [ref, context.ledger.issue(slip.key, "net_pay", result["net_pay"])])

    @tool
    async def revenue_vs_bank_credits(
        financial_statement: str, statements: list[str], is_refutation_probe: bool = False,
    ) -> str:
        """A financial statement's revenue against the credits on the company's bank statements, annualised over the months they cover. A ratio far from 1 means the statements don't show the business the accounts describe (or other accounts carry it)."""
        args = {"financial_statement": financial_statement, "statements": statements}
        accounts, error = kit.doc(financial_statement)
        if accounts is None:
            return kit.log("revenue_vs_bank_credits", args, error or {}, is_refutation_probe)
        views = []
        for name in statements or []:
            view, error = kit.doc(name)
            if view is None:
                return kit.log("revenue_vs_bank_credits", args, error or {}, is_refutation_probe, accounts.key)
            if is_personal_bank_statement(view.document_type, view.fields):
                return kit.log("revenue_vs_bank_credits", args,
                               unavailable(f"{view.key} is a personal statement, not the company's."),
                               is_refutation_probe, accounts.key)
            views.append(view)
        if not views:
            return kit.log("revenue_vs_bank_credits", args, unavailable("Name at least one company bank statement."),
                           is_refutation_probe, accounts.key)
        result = signals.revenue_against_credits(accounts.fields, [v.fields for v in views])
        if not result.get("available", True):
            return kit.log("revenue_vs_bank_credits", args, result, is_refutation_probe, accounts.key)
        ref = context.ledger.issue(accounts.key, "revenue_vs_bank_credits", result, source="revenue_vs_bank_credits")
        return kit.log("revenue_vs_bank_credits", args, {**result, "ref": ref}, is_refutation_probe, accounts.key,
                       [ref])

    @tool
    async def assessed_income_vs_profit(
        notice_of_assessment: str, financial_statement: str, is_refutation_probe: bool = False,
    ) -> str:
        """The income IRAS assessed on the notice of assessment (ECI) against the profit before tax in the financial statement, with the years each covers."""
        args = {"notice_of_assessment": notice_of_assessment, "financial_statement": financial_statement}
        notice, accounts, refused = _two(
            "assessed_income_vs_profit", args, notice_of_assessment, financial_statement, is_refutation_probe,
        )
        if refused is not None:
            return refused
        assert notice is not None and accounts is not None
        result = signals.assessed_against_profit(notice.fields, accounts.fields)
        if not result.get("available", True):
            return kit.log("assessed_income_vs_profit", args, result, is_refutation_probe, notice.key)
        ref = context.ledger.issue(notice.key, f"assessed_vs_profit[{accounts.key}]", result,
                                   source="assessed_income_vs_profit")
        return kit.log("assessed_income_vs_profit", args, {**result, "ref": ref}, is_refutation_probe, notice.key,
                       [ref])

    @tool
    async def counterparty_round_trips(document: str, is_refutation_probe: bool = False) -> str:
        """Counterparties that both pay into a statement's account and are paid from it, with the totals each way. Money going out and coming back can be a genuine supplier-customer, or money going round in a circle."""
        args = {"document": document}
        view, error = kit.doc(document)
        if view is None:
            return kit.log("counterparty_round_trips", args, error or {}, is_refutation_probe)
        if statement_rows(view) is None:
            return kit.log("counterparty_round_trips", args, unavailable("This document has no transactions."),
                           is_refutation_probe, view.key)
        trips = signals.round_trips(view.fields, signals.value(view.fields, "entity_name"))[:10]
        shown = [{**t, "counterparty": mask_identifiers(t["counterparty"])} for t in trips]
        ref = context.ledger.issue(view.key, "round_trips", shown, source="counterparty_round_trips")
        return kit.log("counterparty_round_trips", args,
                       {"available": True, "round_trips": shown, "threshold_each_way": signals.ROUND_TRIP_MIN_EACH_WAY,
                        "ref": ref}, is_refutation_probe, view.key, [ref])

    @tool
    async def customer_concentration(document: str, is_refutation_probe: bool = False) -> str:
        """The share of a statement's receipts that comes from its largest paying counterparty (own-account transfers left out)."""
        args = {"document": document}
        view, error = kit.doc(document)
        if view is None:
            return kit.log("customer_concentration", args, error or {}, is_refutation_probe)
        if statement_rows(view) is None:
            return kit.log("customer_concentration", args, unavailable("This document has no transactions."),
                           is_refutation_probe, view.key)
        result = signals.concentration(view.fields, signals.value(view.fields, "entity_name"))
        if not result.get("available", True):
            return kit.log("customer_concentration", args, result, is_refutation_probe, view.key)
        result = {**result, "top_counterparty": mask_identifiers(str(result["top_counterparty"]))}
        ref = context.ledger.issue(view.key, "customer_concentration", result, source="customer_concentration")
        return kit.log("customer_concentration", args, {**result, "ref": ref}, is_refutation_probe, view.key, [ref])

    @tool
    async def statement_coverage(is_refutation_probe: bool = False) -> str:
        """Which calendar months each bank statement in the case covers, and the months between them no statement covers."""
        covered: dict[str, list[str]] = {}
        for key, view in context.docs.items():
            if view.document_type == "bank_statement":
                covered[key] = signals.covered_months(view.fields)
        if not covered:
            return kit.log("statement_coverage", {}, unavailable("The case has no bank statements."), is_refutation_probe)
        result = {"available": True, "months_by_statement": covered,
                  "missing_months": signals.missing_months(list(covered.values()))}
        ref = context.ledger.issue(next(iter(covered)), "statement_coverage", result, source="statement_coverage")
        return kit.log("statement_coverage", {}, {**result, "ref": ref}, is_refutation_probe, None, [ref])

    return [
        salary_credit_match, revenue_vs_bank_credits, assessed_income_vs_profit,
        counterparty_round_trips, customer_concentration, statement_coverage,
    ]
