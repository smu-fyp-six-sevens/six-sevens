"""
The cross-document measures (case_pipeline/cross_document/signals.py): pure functions over the
documents' fields that the evidence tools, the planner's candidates and the supporting-document rules all read. Each is
tested at its edges here, since a wrong measure misleads all three.
"""

from __future__ import annotations

import pytest

from app.case_pipeline.cross_document import signals


def statement(rows: list[dict], start: str | None = "2026-04-01", end: str | None = "2026-07-31") -> dict:
    fields: dict = {"transactions": rows}
    if start:
        fields["statement_period_start"] = start
    if end:
        fields["statement_period_end"] = end
    return fields


def row(day: str, amount: float, description: str = "", counterparty: str | None = None) -> dict:
    out = {"date": day, "amount": amount, "description": description}
    if counterparty:
        out["counterparty"] = counterparty
    return out


# --- salary_credit --------------------------------------------------------------------------------------------


PAYSLIP = {"net_pay": "5,200.00", "pay_date": "2026-07-31"}


@pytest.mark.parametrize("credit", [5200.00, 5200.00 * 0.995, 5200.00 * 1.009], ids=["exact", "-0.5%", "+0.9%"])
def test_a_credit_within_one_percent_of_net_pay_near_the_pay_date_matches(credit) -> None:
    result = signals.salary_credit(PAYSLIP, statement([row("2026-08-01", credit, "SALARY")]))

    assert result["available"] and len(result["matching_credits"]) == 1
    assert result["credits_near_pay_date"] == 1


@pytest.mark.parametrize("day", ["2026-07-26", "2026-08-10"], ids=["5 days before", "10 days after"])
def test_the_window_runs_five_days_before_to_ten_after_the_pay_date(day) -> None:
    result = signals.salary_credit(PAYSLIP, statement([row(day, 5200.00)], end="2026-08-31"))

    assert len(result["matching_credits"]) == 1


@pytest.mark.parametrize("day", ["2026-07-25", "2026-08-11"], ids=["6 days before", "11 days after"])
def test_a_matching_amount_outside_the_window_does_not_match(day) -> None:
    result = signals.salary_credit(PAYSLIP, statement([row(day, 5200.00)], end="2026-08-31"))

    assert result["matching_credits"] == [] and result["credits_near_pay_date"] == 0
    assert result["nearest_credit"]["amount"] == 5200.00  # nothing in the window: the nearest of all credits


def test_a_wrong_amount_reports_the_nearest_credit_and_its_difference() -> None:
    rows = [row("2026-07-30", 1000.00), row("2026-08-01", 5000.00), row("2026-07-02", 5190.00)]

    result = signals.salary_credit(PAYSLIP, statement(rows))

    assert result["matching_credits"] == []
    # The nearest inside the window, not the closer amount a month earlier.
    assert result["nearest_credit"]["amount"] == 5000.00 and result["nearest_difference"] == -200.00


@pytest.mark.parametrize("payslip, rows, reason", [
    ({"pay_date": "2026-07-31"}, [row("2026-08-01", 5200.0)], "no net pay"),
    ({"net_pay": "0", "pay_date": "2026-07-31"}, [row("2026-08-01", 5200.0)], "no net pay"),
    (PAYSLIP, [row("2026-08-01", -5200.0)], "no credits"),
])
def test_no_net_pay_or_no_credits_is_unavailable(payslip, rows, reason) -> None:
    result = signals.salary_credit(payslip, statement(rows))

    assert result["available"] is False and reason in result["reason"]


# --- revenue, revenue_against_credits --------------------------------------------------------------------------


def test_revenue_is_the_field_when_there_is_one() -> None:
    assert signals.revenue({"revenue": "5,314,000", "income_statement_items": [
        {"label": "Revenue", "amount": "1"}]}) == 5_314_000


def test_revenue_falls_back_to_the_income_statements_revenue_line_but_not_cost_of_sales() -> None:
    items = [{"label": "Cost of sales", "amount": "(3,000,000)"}, {"label": "Revenue", "amount": "4,200,000"}]

    assert signals.revenue({"income_statement_items": items}) == 4_200_000
    assert signals.revenue({"income_statement_items": [{"label": "Cost of sales", "amount": "3,000,000"}]}) is None


def test_revenue_against_credits_annualises_over_the_months_covered() -> None:
    rows = [row("2026-04-05", 100_000), row("2026-05-05", 100_000), row("2026-06-05", 100_000),
            row("2026-07-05", 100_000), row("2026-07-06", -50_000)]

    result = signals.revenue_against_credits({"revenue": 1_200_000}, [statement(rows)])

    assert result["months_of_statements"] == 4 and result["credits_in_those_months"] == 400_000
    assert result["credits_annualised"] == 1_200_000 and result["annualised_credits_to_revenue"] == 1.0


def test_two_statements_over_the_same_months_add_their_credits_but_not_their_months() -> None:
    first = statement([row("2026-04-05", 60_000)], "2026-04-01", "2026-06-30")
    second = statement([row("2026-04-07", 30_000)], "2026-04-01", "2026-06-30")

    result = signals.revenue_against_credits({"revenue": 360_000}, [first, second])

    assert result["months_of_statements"] == 3 and result["credits_annualised"] == 360_000


@pytest.mark.parametrize("accounts, statements", [
    ({}, [statement([row("2026-04-05", 1000)])]),
    ({"revenue": "0"}, [statement([row("2026-04-05", 1000)])]),
    ({"revenue": 100_000}, [statement([row("2026-04-05", -1000)])]),
    ({"revenue": 100_000}, []),
])
def test_revenue_against_credits_refuses_without_revenue_or_credits(accounts, statements) -> None:
    assert signals.revenue_against_credits(accounts, statements)["available"] is False


# --- assessed_against_profit -----------------------------------------------------------------------------------


def test_assessed_income_is_compared_with_profit_before_tax() -> None:
    accounts = {"income_statement_items": [{"label": "Profit before income tax", "amount": "800,000"}],
                "profit_for_the_year": "650,000"}

    result = signals.assessed_against_profit({"eci": "1,000,000", "year_of_assessment": "2026"}, accounts)

    assert result["profit_before_tax"] == 800_000 and result["assessed_to_profit"] == 1.25


def test_without_a_profit_before_tax_line_the_years_profit_is_used() -> None:
    result = signals.assessed_against_profit({"eci": "500,000"}, {"profit_for_the_year": "400,000"})

    assert result["assessed_to_profit"] == 1.25


def test_assessed_against_profit_needs_both_figures() -> None:
    assert signals.assessed_against_profit({}, {"profit_for_the_year": "1"})["available"] is False
    assert signals.assessed_against_profit({"eci": "1"}, {})["available"] is False


# --- round_trips, concentration ------------------------------------------------------------------------------


def test_a_counterparty_paid_and_paying_ten_thousand_each_way_is_a_round_trip() -> None:
    rows = [row("2026-04-05", 12_000, counterparty="Orion Trading Pte Ltd"),
            row("2026-04-20", -11_000, counterparty="ORION TRADING PTE. LTD."),
            row("2026-05-05", 9_000, counterparty="Small Co"), row("2026-05-06", -9_999, counterparty="Small Co")]

    (trip,) = signals.round_trips(statement(rows))

    assert trip["paid_in"] == 12_000 and trip["paid_out"] == 11_000 and trip["count"] == 2


def test_the_companys_own_transfers_are_not_a_round_trip() -> None:
    rows = [row("2026-04-05", 50_000, counterparty="Harbourview Digital Media Pte Ltd"),
            row("2026-04-20", -50_000, counterparty="HARBOURVIEW DIGITAL MEDIA PTE LTD")]

    assert signals.round_trips(statement(rows), own_name="Harbourview Digital Media Pte. Ltd.") == []


def test_concentration_is_the_largest_payers_share_of_receipts() -> None:
    rows = [row(f"2026-04-{d:02d}", 10_000, counterparty="Big Client") for d in (3, 10, 17)]
    rows += [row("2026-04-20", 5_000, counterparty="Small Client"), row("2026-04-21", -9_000, counterparty="Supplier")]

    result = signals.concentration(statement(rows))

    assert result["top_counterparty"] == "Big Client" and result["credits"] == 4
    assert result["top_share"] == round(30_000 / 35_000, 3)


def test_concentration_leaves_own_account_transfers_out() -> None:
    rows = [row("2026-04-03", 90_000, "TRANSFER FROM HARBOURVIEW DIGITAL MEDIA PTE LTD"),
            row("2026-04-10", 10_000, counterparty="Client A"), row("2026-04-11", 10_000, counterparty="Client B")]

    result = signals.concentration(statement(rows), own_name="Harbourview Digital Media Pte Ltd")

    assert result["credits"] == 2 and result["total_credits"] == 20_000 and result["top_share"] == 0.5


def test_concentration_with_no_receipts_is_unavailable() -> None:
    assert signals.concentration(statement([row("2026-04-03", -100)]))["available"] is False


# --- covered_months, missing_months, loan_repayments -------------------------------------------------------


def test_covered_months_come_from_the_stated_period() -> None:
    assert signals.covered_months(statement([], "2026-11-15", "2027-02-02")) == [
        "2026-11", "2026-12", "2027-01", "2027-02"]


def test_without_a_period_covered_months_come_from_the_rows() -> None:
    rows = [row("2026-05-03", 1), row("2026-03-09", 1), row("2026-05-20", -1)]

    assert signals.covered_months(statement(rows, start=None, end=None)) == ["2026-03", "2026-05"]


def test_missing_months_are_the_gaps_between_the_first_and_last() -> None:
    assert signals.missing_months([["2026-01", "2026-02"], ["2026-05"]]) == ["2026-03", "2026-04"]
    assert signals.missing_months([["2026-01", "2026-02"], ["2026-03"]]) == []
    assert signals.missing_months([["2026-01"]]) == []


def test_only_debits_naming_a_loan_are_loan_repayments() -> None:
    rows = [row("2026-04-05", -10_681.80, "GIRO TERM LOAN INSTALMENT DBS"),
            row("2026-04-06", -3_000, "HIRE PURCHASE REPAYMENT"),
            row("2026-04-07", 725_000, "LOAN DISBURSEMENT"),            # a credit: money in, not a repayment
            row("2026-04-08", -500, "GIRO PAYMENT PREMISES RENTAL")]

    assert [m.amount for m in signals.loan_repayments(statement(rows))] == [-10_681.80, -3_000]
