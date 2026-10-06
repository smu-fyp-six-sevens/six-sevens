"""Explicit bindings for the numerical runner: statement casts, row roles, naming mismatches,
and parity with the reference path."""

from __future__ import annotations


from app.fraud_detection.field_catalogue import render_field_catalogue
from app.fraud_detection.font_consistency import ExtractedField
from app.fraud_detection.numerical_validation.runner import Binding, render_check_roles, run_numerical_checks
from app.fraud_detection.numerical_validation.casts import CastLine, profit_cast_finding


def _field(name, value, confidence=0.95):
    return ExtractedField(field_name=name, field_type="string", field_value=value, confidence=confidence)


def _codes(outcome, code):
    return [f for f in outcome.findings if f.check_code == code]


INCOME_ITEMS = [
    {"label": "Cost of revenue", "amount": -1000000, "amount_prior_year": -900000},
    {"label": "Administrative expenses", "amount": -500000, "amount_prior_year": -400000},
    {"label": "Finance costs", "amount": -50000, "amount_prior_year": -40000},
    {"label": "Other income", "amount": 20000, "amount_prior_year": 10000},
    {"label": "Income tax expense", "amount": -30000, "amount_prior_year": -25000},
]

INCOME_BINDINGS = [
    Binding("expense_line", "income_statement_items", 0),
    Binding("expense_line", "income_statement_items", 1),
    Binding("expense_line", "income_statement_items", 2),
    Binding("income_line", "income_statement_items", 3),
    Binding("expense_line", "income_statement_items", 4),
]


def _income_fields(items=None):
    return [
        _field("revenue", "2000000"),
        _field("profit_for_the_year", "440000"),
        _field("revenue_prior_year", "1800000"),
        _field("profit_for_the_year_prior_year", "445000"),
        _field("income_statement_items", items if items is not None else INCOME_ITEMS),
    ]


# --- pure arithmetic ---------------------------------------------------------


def test_expense_sign_does_not_matter() -> None:
    assert CastLine("Cost of sales", "expense", -600000).effect == CastLine("Cost of sales", "expense", 600000).effect


def test_profit_cast_finding_is_none_when_the_statement_casts() -> None:
    lines = [CastLine("Cost of sales", "expense", -600000), CastLine("Other income", "income", 50000)]
    assert profit_cast_finding("current year", 1000000, 450000, lines,
                               profit_field="profit_for_the_year", needs_review=False) is None


# --- income statement casts ------------------------------------------------


def test_income_statement_casts_with_mixed_sign_conventions() -> None:
    outcome = run_numerical_checks("source_of_wealth", _income_fields(), INCOME_BINDINGS)
    assert _codes(outcome, "FS.IS_CAST") == []
    assert [b["year"] for b in outcome.breakdown if "year" in b] == ["current year", "prior year"]
    assert outcome.unassigned == []


def test_income_statement_flags_a_real_mismatch() -> None:
    items = [
        {"label": "Cost of sales", "amount": -1560800, "amount_prior_year": -1717000},
        {"label": "Total operating expenses", "amount": -665000, "amount_prior_year": -481500},
        {"label": "Tax expense", "amount": -86600, "amount_prior_year": -56900},
    ]
    fields = [_field("revenue", "2729000"), _field("profit_for_the_year", "422800"),
              _field("income_statement_items", items)]
    outcome = run_numerical_checks("source_of_wealth", fields, [])
    found = _codes(outcome, "FS.IS_CAST")
    assert len(found) == 1
    # The total row is left out of the cast: 2,729,000 - 1,560,800 - 86,600.
    assert found[0].expected == "1,081,600.00"


def test_income_statement_is_cast_from_its_labels_without_row_bindings() -> None:
    items = INCOME_ITEMS + [{"label": "Total operating expenses", "amount": -550000, "amount_prior_year": -440000}]
    outcome = run_numerical_checks("source_of_wealth", _income_fields(items), [])
    assert _codes(outcome, "FS.IS_CAST") == []
    assert [b["year"] for b in outcome.breakdown if "year" in b] == ["current year", "prior year"]
    # The total is reported as unassigned, not cast.
    assert {"field": "income_statement_items", "row": 5, "label": "Total operating expenses"} in outcome.unassigned


def test_income_statement_mismatch_is_still_flagged_when_a_line_is_missing() -> None:
    items = [item for item in INCOME_ITEMS if item["label"] != "Other income"]
    outcome = run_numerical_checks("source_of_wealth", _income_fields(items), [])
    assert len(_codes(outcome, "FS.IS_CAST")) == 2


def test_income_statement_skips_a_year_with_a_missing_amount() -> None:
    items = [{"label": "Cost of revenue", "amount": -1000000},
             {"label": "Administrative expenses", "amount": -500000, "amount_prior_year": -400000}]
    fields = [_field("revenue", "1500000"), _field("profit_for_the_year", "1000000"),
              _field("income_statement_items", items), _field("revenue_prior_year", "1000000"),
              _field("profit_for_the_year_prior_year", "600000")]
    bindings = [Binding("expense_line", "income_statement_items", 0),
                Binding("expense_line", "income_statement_items", 1)]
    outcome = run_numerical_checks("source_of_wealth", fields, bindings)
    assert [b["year"] for b in outcome.breakdown if "year" in b] == ["current year"]
    assert any("prior year" in s.reason for s in outcome.skipped if s.check_code == "FS.IS_CAST")


def test_income_statement_marks_low_confidence_findings_for_review() -> None:
    fields = _income_fields()
    fields[0] = _field("revenue", "2000000", confidence=0.5)  # low-confidence revenue, current year only
    fields[1] = _field("profit_for_the_year", "400000")  # both years mismatch so both produce a finding
    fields[3] = _field("profit_for_the_year_prior_year", "300000")
    outcome = run_numerical_checks("source_of_wealth", fields, [])
    by_year = {f.summary.split("(")[1].split(")")[0]: f.needs_review for f in _codes(outcome, "FS.IS_CAST")}
    assert by_year == {"current year": True, "prior year": False}


# --- balance sheet casts and identity ---------------------------------------


def _balance_fields(*, total_equity="1695700"):
    rows = [
        {"section": "equity", "label": "Share capital", "amount": 1000000},
        {"section": "equity", "label": "Retained earnings", "amount": 695700},
        {"section": "equity", "label": "Total equity", "amount": 1695700},
        {"section": "current_assets", "label": "Cash", "amount": 500},
    ]
    return [_field("balance_sheet_items", rows), _field("total_equity", total_equity),
            _field("total_assets", "2000"), _field("total_liabilities", "300")]


def test_balance_sheet_section_casts_when_total_row_is_left_out() -> None:
    bindings = [Binding("equity_row", "balance_sheet_items", 0), Binding("equity_row", "balance_sheet_items", 1)]
    outcome = run_numerical_checks("source_of_wealth", _balance_fields(), bindings)
    assert _codes(outcome, "FS.BS_CAST") == []
    assert {"field": "balance_sheet_items", "row": 2, "label": "equity: Total equity"} in outcome.unassigned


def test_balance_sheet_flags_a_section_that_does_not_sum() -> None:
    bindings = [Binding("equity_row", "balance_sheet_items", i) for i in range(3)]
    outcome = run_numerical_checks("source_of_wealth", _balance_fields(total_equity="1800000"), bindings)
    assert len(_codes(outcome, "FS.BS_CAST")) == 1


def test_balance_sheet_leaves_out_a_closing_balance_even_when_bound() -> None:
    rows = [
        {"section": "equity", "label": "Share capital", "amount": 750000},
        {"section": "equity", "label": "Retained earnings, beginning of year", "amount": 721800},
        {"section": "equity", "label": "Add: Profit for the year", "amount": 300600},
        {"section": "equity", "label": "Retained earnings, end of year", "amount": 1022400},
    ]
    fields = [_field("balance_sheet_items", rows), _field("total_equity", "1772400")]
    bindings = [Binding("equity_row", "balance_sheet_items", i) for i in range(4)]
    outcome = run_numerical_checks("source_of_wealth", fields, bindings)
    assert _codes(outcome, "FS.BS_CAST") == []  # 750,000 + 721,800 + 300,600 = 1,772,400


def test_balance_sheet_checks_only_the_current_year_column() -> None:
    # The prior-year balance sheet column is not checked yet, so it's ignored here.
    rows = [{"section": "equity", "label": "Share capital", "amount": 1, "amount_prior_year": 9}]
    fields = [_field("balance_sheet_items", rows), _field("total_equity", "1")]
    outcome = run_numerical_checks("source_of_wealth", fields, [Binding("equity_row", "balance_sheet_items", 0)])
    assert _codes(outcome, "FS.BS_CAST") == []


# --- severity of the rollforward and tax-rate checks -----------------------------


def test_negative_ppe_movement_is_inconsistent_not_impossible() -> None:
    items = [{"label": "Depreciation of property, plant and equipment", "amount": -44850}]
    fields = [_field("income_statement_items", items), _field("ppe_opening", "296910"), _field("ppe_closing", "237570")]
    outcome = run_numerical_checks("source_of_wealth", fields, [Binding("depreciation_line", "income_statement_items", 0)])
    found = _codes(outcome, "FS.PPE_ROLLFORWARD")
    assert len(found) == 1
    assert found[0].severity == "inconsistent"
    assert "Disposals or impairments" in found[0].summary


def test_flat_seventeen_percent_tax_is_a_signal() -> None:
    items = [{"label": "Profit before taxation", "amount": 366800}, {"label": "Tax expense", "amount": -62400}]
    fields = [_field("income_statement_items", items)]
    bindings = [Binding("profit_before_tax_line", "income_statement_items", 0),
                Binding("tax_expense_line", "income_statement_items", 1)]
    found = _codes(run_numerical_checks("source_of_wealth", fields, bindings), "FS.TAX_EXPENSE_RATE")
    assert len(found) == 1 and found[0].severity == "signal"


# --- guards on bindings -------------------------------------------------------


def test_a_plain_role_bound_to_a_list_field_is_rejected() -> None:
    outcome = run_numerical_checks(
        "source_of_wealth", _income_fields(), [Binding("dividends_paid", "income_statement_items")],
    )
    assert any("list field only binds to its own name" in issue for issue in outcome.issues)


def test_one_field_bound_to_two_roles_keeps_the_role_named_after_it() -> None:
    fields = [_field("retained_earnings_closing", "1000"), _field("total_assets", "1000"),
              _field("total_liabilities", "0"), _field("total_equity", "1000")]
    bindings = [Binding("dividends_paid", "retained_earnings_closing"),
                Binding("retained_earnings_closing", "retained_earnings_closing")]
    outcome = run_numerical_checks("source_of_wealth", fields, bindings)
    assert any("already bound to 'retained_earnings_closing'" in issue for issue in outcome.issues)


def test_balance_identity_is_checked_when_the_three_totals_are_present() -> None:
    fields = [_field("total_assets", "2000"), _field("total_liabilities", "300"), _field("total_equity", "500")]
    outcome = run_numerical_checks("source_of_wealth", fields, [])
    assert len(_codes(outcome, "FS.BALANCES")) == 1


# --- explicit bindings for ordinary checks -----------------------------------


def test_a_field_with_a_different_name_is_used_when_bound() -> None:
    fields = [_field("closing_cash", "1000"), _field("total_assets", "2000"),
              _field("total_liabilities", "400"), _field("total_equity", "600")]
    bound = run_numerical_checks("source_of_wealth", fields, [Binding("total_assets", "closing_cash")])
    assert _codes(bound, "FS.BALANCES") == []  # 1000 vs 1000 holds for the bound field only
    assert bound.issues == []


def test_an_unknown_field_is_reported_as_an_issue() -> None:
    outcome = run_numerical_checks("source_of_wealth", _income_fields(), [Binding("revenue", "turnover")])
    assert any("turnover" in issue for issue in outcome.issues)


def test_a_row_role_bound_to_a_non_array_is_reported() -> None:
    outcome = run_numerical_checks("source_of_wealth", _income_fields(), [Binding("expense_line", "revenue", 0)])
    assert any("not an array" in issue for issue in outcome.issues)


def test_an_unbound_row_role_skips_its_check_with_no_guess() -> None:
    fields = [_field("income_statement_items", INCOME_ITEMS), _field("revenue", "2000000"),
              _field("profit_for_the_year", "440000")]
    outcome = run_numerical_checks("source_of_wealth", fields, [])
    assert "FS.TAX_EXPENSE_RATE" in {s.check_code for s in outcome.skipped}
    assert _codes(outcome, "FS.TAX_EXPENSE_RATE") == []


# --- prompt rendering -----------------------------------------------------------


def test_check_roles_section_marks_matched_roles() -> None:
    fields = [_field("transactions", []), _field("opening_balance", "1")]
    text = render_check_roles("bank_statement", fields)
    assert "transactions (matched)" in text
    assert "ledger_balance (bind)" in text


def test_check_roles_section_lists_statement_casts_for_source_of_wealth() -> None:
    text = render_check_roles("source_of_wealth", _income_fields())
    assert "income_line" in text and "equity_row" in text


def test_catalogue_lists_structural_rows_with_indices() -> None:
    fields = [_field("company_name", "Acme Pte Ltd"),
              _field("income_statement_items", [{"label": "Cost of sales", "amount": -1}, {"label": "Staff costs"}])]
    text = render_field_catalogue(fields, "source_of_wealth")
    assert "- company_name" in text
    assert '[0] "Cost of sales"' in text and '[1] "Staff costs"' in text
    assert "Acme" not in text


def test_catalogue_shows_only_a_count_for_long_arrays() -> None:
    fields = [_field("transactions", [{"date": "2026-01-01", "description": "x", "amount": 1}] * 300)]
    text = render_field_catalogue(fields, "bank_statement")
    assert "300 rows" in text and "row labels" not in text
