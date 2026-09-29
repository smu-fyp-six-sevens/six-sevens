from __future__ import annotations

import random
from datetime import date

from app.fraud_detection.font_consistency import ExtractedField
from app.fraud_detection.numerical_validation import (
    BENFORD_MIN_SAMPLE,
    REFERENCE_DATA,
    _benford,
    _mrz_check_digit,
    _nric_expected_letter,
    check_numerical_validation,
)


def _field(name: str, value, confidence: float = 0.95) -> ExtractedField:
    return ExtractedField(field_name=name, field_type="string", field_value=value, confidence=confidence)


def _run(fields: list[ExtractedField], document_type: str | None = None):
    return check_numerical_validation("doc-1", fields, document_type)


def _codes(result, code: str) -> list:
    return [f for f in result.findings if f.check_code == code]


def _valid_nric(prefix: str, digits: str) -> str:
    return f"{prefix}{digits}{_nric_expected_letter(prefix, digits)}"


# ---------------------------------------------------------------------------
# GEN.* / ID.NRIC_CHECKSUM — universal checks
# ---------------------------------------------------------------------------


def test_nric_checksum_flags_invalid_check_letter() -> None:
    valid = _valid_nric("S", "1234567")
    invalid = valid[:-1] + ("A" if valid[-1] != "A" else "B")
    result = _run([_field("nric_number", invalid, confidence=0.95)])
    findings = _codes(result, "ID.NRIC_CHECKSUM")
    assert len(findings) == 1
    assert findings[0].severity == "impossible"
    assert findings[0].expected == valid


def test_nric_checksum_accepts_valid_checksum_for_every_prefix() -> None:
    for prefix, digits in [("S", "1234567"), ("T", "7654321"), ("F", "9876543"),
                            ("G", "1112223"), ("M", "5556667")]:
        valid = _valid_nric(prefix, digits)
        result = _run([_field("nric_number", valid)])
        assert _codes(result, "ID.NRIC_CHECKSUM") == [], f"false positive for {valid}"


def test_nric_checksum_scans_any_field_containing_an_nric() -> None:
    # Runs on every document type, against any field whose value contains
    # an NRIC-shaped token — not just a field literally named nric_number.
    valid = _valid_nric("S", "1234567")
    invalid = valid[:-1] + ("A" if valid[-1] != "A" else "B")
    result = _run(
        [_field("individual_name", f"Jane Tan ({invalid})")],
        document_type="certificate_of_incumbency",
    )
    assert len(_codes(result, "ID.NRIC_CHECKSUM")) == 1


def test_uen_format_accepts_real_shape_and_rejects_garbage() -> None:
    result = _run([_field("uen", "202312345K")])
    assert _codes(result, "ID.UEN_FORMAT") == []

    result = _run([_field("uen", "NOTAUEN")])
    assert len(_codes(result, "ID.UEN_FORMAT")) == 1


def test_uen_format_rejects_implausible_year_prefix() -> None:
    result = _run([_field("uen", "195012345K")])
    assert len(_codes(result, "ID.UEN_FORMAT")) == 1


def test_amount_parse_marks_unparseable_amount_as_needs_review() -> None:
    result = _run([_field("net_pay", "not a number")])
    findings = _codes(result, "GEN.AMOUNT_PARSE")
    assert len(findings) == 1
    assert findings[0].needs_review is True


# ---------------------------------------------------------------------------
# Skipping — the AC1/AC7 contract
# ---------------------------------------------------------------------------


def test_missing_fields_are_skipped_with_a_reason_not_silently_dropped() -> None:
    result = _run([_field("company_name", "Acme")], document_type="bank_statement")
    codes = {s.check_code for s in result.skipped_checks}
    assert "BS.RUNNING_BALANCE" in codes
    running_balance_skip = next(s for s in result.skipped_checks if s.check_code == "BS.RUNNING_BALANCE")
    assert "transactions" in running_balance_skip.reason


def test_unable_to_assess_when_nothing_at_all_extracted() -> None:
    result = _run([])
    assert result.unable_to_assess is True


def test_reference_data_checks_skip_when_no_table_configured() -> None:
    assert REFERENCE_DATA["bank_identifiers"] == {}
    result = _run([_field("bank_name", "OCBC")], document_type="bank_statement")
    skip = next(s for s in result.skipped_checks if s.check_code == "BS.BANK_IDENTIFIERS")
    assert "not configured" in skip.reason


# ---------------------------------------------------------------------------
# needs_review downgrade
# ---------------------------------------------------------------------------


def test_low_confidence_field_downgrades_its_finding_to_needs_review() -> None:
    valid = _valid_nric("S", "1234567")
    invalid = valid[:-1] + ("A" if valid[-1] != "A" else "B")
    result = _run([_field("nric_number", invalid, confidence=0.4)])
    findings = _codes(result, "ID.NRIC_CHECKSUM")
    assert len(findings) == 1
    assert findings[0].needs_review is True


def test_high_confidence_field_is_not_downgraded() -> None:
    valid = _valid_nric("S", "1234567")
    invalid = valid[:-1] + ("A" if valid[-1] != "A" else "B")
    result = _run([_field("nric_number", invalid, confidence=0.99)])
    findings = _codes(result, "ID.NRIC_CHECKSUM")
    assert len(findings) == 1
    assert findings[0].needs_review is False


# ---------------------------------------------------------------------------
# Bank statement — real-data-shaped behaviour
# ---------------------------------------------------------------------------


def test_running_balance_flags_a_tampered_transaction() -> None:
    transactions = [
        {"amount": 0, "balance": 10000.00},
        {"amount": 5000.00, "balance": 15000.00},
        {"amount": -2000.00, "balance": 14500.00},
    ]
    result = _run([_field("transactions", transactions)], document_type="bank_statement")
    findings = _codes(result, "BS.RUNNING_BALANCE")
    assert len(findings) == 1
    assert findings[0].severity == "impossible"


def test_running_balance_reconciles_a_newest_first_statement() -> None:
    newest_first = [
        {"amount": -35.00, "balance": 22449.86},
        {"amount": -304.06, "balance": 22484.86},
        {"amount": -0.50, "balance": 22788.92},
    ]
    result = _run([_field("transactions", newest_first)], document_type="bank_statement")
    assert _codes(result, "BS.RUNNING_BALANCE") == []


def test_running_balance_reports_sign_inversion_as_direction() -> None:
    transactions = [
        {"amount": 0, "balance": 15153.28},
        {"description": "ONLINE TRANSFER", "amount": -405.92, "balance": 15559.20},
    ]
    result = _run([_field("transactions", transactions)], document_type="bank_statement")
    findings = _codes(result, "BS.RUNNING_BALANCE")
    assert len(findings) == 1
    assert findings[0].severity == "inconsistent"
    assert "direction" in (findings[0].severity_note or "").lower()


def test_running_balance_reports_systemic_break_once() -> None:
    transactions = [{"amount": 1.0, "balance": float(i * 997)} for i in range(20)]
    result = _run([_field("transactions", transactions)], document_type="bank_statement")
    findings = _codes(result, "BS.RUNNING_BALANCE")
    assert len(findings) == 1
    assert findings[0].severity == "implausible"


def test_open_close_reconciles() -> None:
    transactions = [
        {"amount": 5000.00, "balance": 15000.00},
        {"amount": -2000.00, "balance": 13000.00},
    ]
    result = _run([_field("transactions", transactions)], document_type="bank_statement")
    assert _codes(result, "BS.OPEN_CLOSE") == []


def test_open_close_flags_a_break() -> None:
    transactions = [
        {"amount": 5000.00, "balance": 15000.00},
        {"amount": -2000.00, "balance": 20000.00},
    ]
    result = _run([_field("transactions", transactions)], document_type="bank_statement")
    assert len(_codes(result, "BS.OPEN_CLOSE")) == 1


def test_open_close_uses_stated_header_balances_when_present() -> None:
    # transactions[0].balance - transactions[0].amount = 10,000, matching
    # the stated opening_balance — but the stated ledger_balance (closing)
    # disagrees with the last transaction's own running balance by more
    # than the transaction arithmetic alone would explain.
    transactions = [
        {"amount": 5000.00, "balance": 15000.00},
        {"amount": -2000.00, "balance": 13000.00},
    ]
    result = _run(
        [_field("transactions", transactions), _field("opening_balance", "10000.00"),
         _field("ledger_balance", "99999.00")],
        document_type="bank_statement",
    )
    assert len(_codes(result, "BS.STATED_CLOSING")) == 1
    assert _codes(result, "BS.STATED_OPENING") == []


def test_open_close_flags_stated_opening_mismatch_with_first_transaction() -> None:
    transactions = [
        {"amount": 5000.00, "balance": 15000.00},
        {"amount": -2000.00, "balance": 13000.00},
    ]
    result = _run(
        [_field("transactions", transactions), _field("opening_balance", "50000.00")],
        document_type="bank_statement",
    )
    findings = _codes(result, "BS.STATED_OPENING")
    assert len(findings) == 1
    assert findings[0].expected == "10,000.00"


def test_totals_checks_stated_credits_debits_and_count() -> None:
    transactions = [{"amount": 100.0, "balance": 100.0}, {"amount": -30.0, "balance": 70.0}]
    result = _run(
        [_field("transactions", transactions), _field("total_credits", "50")],
        document_type="bank_statement",
    )
    assert len(_codes(result, "BS.TOTALS")) == 1


def test_overdraft_limit_flags_balance_below_limit() -> None:
    transactions = [{"amount": -100.0, "balance": -600.0}]
    result = _run(
        [_field("transactions", transactions), _field("overdraft_limit", "500")],
        document_type="bank_statement",
    )
    assert len(_codes(result, "BS.OVERDRAFT_LIMIT")) == 1


def test_available_vs_ledger_reconciles() -> None:
    result = _run(
        [_field("ledger_balance", "1000"), _field("available_balance", "800"), _field("holds", "200")],
        document_type="bank_statement",
    )
    assert _codes(result, "BS.AVAILABLE_VS_LEDGER") == []


def test_available_vs_ledger_flags_mismatch() -> None:
    result = _run(
        [_field("ledger_balance", "1000"), _field("available_balance", "900"), _field("holds", "200")],
        document_type="bank_statement",
    )
    assert len(_codes(result, "BS.AVAILABLE_VS_LEDGER")) == 1


def test_narrative_amount_flags_description_amount_mismatch() -> None:
    transactions = [{"description": "CARD SGD 500", "amount": -100.0, "balance": 0.0}]
    result = _run([_field("transactions", transactions)], document_type="bank_statement")
    assert len(_codes(result, "BS.NARRATIVE_AMOUNT")) == 1


def test_fee_schedule_flags_widely_varying_recurring_fee() -> None:
    transactions = [
        {"description": "MONTHLY SERVICE CHARGE", "amount": -5.0, "balance": 0.0},
        {"description": "MONTHLY SERVICE CHARGE", "amount": -5.0, "balance": 0.0},
        {"description": "MONTHLY SERVICE CHARGE", "amount": -50.0, "balance": 0.0},
    ]
    result = _run([_field("transactions", transactions)], document_type="bank_statement")
    assert len(_codes(result, "BS.FEE_SCHEDULE")) == 1


def test_card_diversity_flags_one_card_per_transaction() -> None:
    transactions = [
        {"description": f"CARD 1234-XXXX-XXXX-{i:04d}", "amount": -1.0, "balance": 0.0}
        for i in range(6)
    ]
    result = _run([_field("transactions", transactions)], document_type="bank_statement")
    assert len(_codes(result, "BS.CARD_DIVERSITY")) == 1


def test_recurring_variance_flags_rent_that_moves_a_lot() -> None:
    transactions = [
        {"description": "RENT PAYMENT", "amount": -1000.0, "balance": 0.0},
        {"description": "RENT PAYMENT", "amount": -1000.0, "balance": 0.0},
        {"description": "RENT PAYMENT", "amount": -1500.0, "balance": 0.0},
    ]
    result = _run([_field("transactions", transactions)], document_type="bank_statement")
    assert len(_codes(result, "BS.RECURRING_VARIANCE")) == 1


def test_duplicates_flags_same_payee_and_amount_repeated() -> None:
    transactions = [
        {"description": "NETFLIX", "amount": -15.0, "balance": 0.0},
        {"description": "NETFLIX", "amount": -15.0, "balance": 0.0},
    ]
    result = _run([_field("transactions", transactions)], document_type="bank_statement")
    assert len(_codes(result, "STAT.DUPLICATES")) == 1


def test_real_statements_from_production_reconcile() -> None:
    """
    Regression pin: the exact real data that drove the ordering-direction
    and sign-inversion fixes to BS.RUNNING_BALANCE. All three come back
    clean or with exactly the one known genuine break.
    """
    newest_first = [
        {"amount": -35.00, "balance": 22449.86},
        {"amount": -304.06, "balance": 22484.86},
        {"amount": -0.50, "balance": 22788.92},
    ]
    assert _codes(
        _run([_field("transactions", newest_first)], document_type="bank_statement"),
        "BS.RUNNING_BALANCE",
    ) == []

    sign_inverted = [
        {"amount": 0, "balance": 15153.28},
        {"description": "ONLINE TRANSFER", "amount": -405.92, "balance": 15559.20},
        {"amount": 14.61, "balance": 15573.81},
    ]
    findings = _codes(
        _run([_field("transactions", sign_inverted)], document_type="bank_statement"),
        "BS.RUNNING_BALANCE",
    )
    assert len(findings) == 1
    assert findings[0].severity == "inconsistent"


# ---------------------------------------------------------------------------
# Benford's Law
# ---------------------------------------------------------------------------


def test_benford_flags_fabricated_round_amounts() -> None:
    random.seed(7)
    transactions = [
        {"amount": float(random.choice([5000, 6000, 7500, 8000, 9500])), "balance": 0.0}
        for _ in range(150)
    ]
    result = _run([_field("transactions", transactions)], document_type="bank_statement")
    assert result.benford.conforms is False
    assert len(_codes(result, "STAT.BENFORD_1D")) >= 1


def test_benford_does_not_flag_genuine_log_uniform_amounts() -> None:
    random.seed(7)
    transactions = [{"amount": 10 ** random.uniform(0, 5), "balance": 0.0} for _ in range(200)]
    result = _run([_field("transactions", transactions)], document_type="bank_statement")
    assert result.benford.conforms is True
    assert _codes(result, "STAT.BENFORD_1D") == []


def test_benford_unable_to_assess_when_no_transactions_field() -> None:
    result = _run([_field("company_name", "Acme")], document_type="bank_statement")
    assert result.benford.unable_to_assess is True


def test_benford_not_run_for_non_bank_statement_types() -> None:
    result = _run([_field("company_name", "Acme")], document_type="payslip")
    assert result.benford.unable_to_assess is True
    assert result.benford_excluding_fees is None


def test_benford_unit_function_reports_all_nine_digits() -> None:
    random.seed(1)
    amounts = [10 ** random.uniform(0, 5) for _ in range(BENFORD_MIN_SAMPLE + 10)]
    analysis, _ = _benford(amounts, "STAT.BENFORD_1D")
    assert [d.digit for d in analysis.digits] == list(range(1, 10))
    assert analysis.unable_to_assess is False


# ---------------------------------------------------------------------------
# Source of wealth
# ---------------------------------------------------------------------------


def test_holding_value_flags_quantity_times_price_mismatch() -> None:
    holdings = [{"quantity": 100, "closing_price": 10.0, "market_value": 500.0}]
    result = _run([_field("holdings", holdings)], document_type="source_of_wealth")
    assert len(_codes(result, "SOW.HOLDING_VALUE")) == 1


def test_portfolio_sum_reconciles() -> None:
    holdings = [{"market_value": 500.0}, {"market_value": 500.0}]
    result = _run(
        [_field("holdings", holdings), _field("portfolio_value", "1000")],
        document_type="source_of_wealth",
    )
    assert _codes(result, "SOW.PORTFOLIO_SUM") == []


def test_nav_flags_mismatch() -> None:
    result = _run(
        [_field("cash_balance", "1000"), _field("portfolio_value", "5000"),
         _field("net_asset_value", "5500")],
        document_type="source_of_wealth",
    )
    assert len(_codes(result, "SOW.NAV")) == 1


def test_fs_is_cast_flags_income_statement_that_does_not_cast() -> None:
    result = _run(
        [_field("revenue", "1000000"), _field("cost_of_sales", "600000"),
         _field("operating_expenses", "200000"), _field("tax_expense", "17000"),
         _field("profit_for_the_year", "999999")],
        document_type="source_of_wealth",
    )
    assert len(_codes(result, "FS.IS_CAST")) == 1


def test_fs_balances_flags_broken_identity() -> None:
    result = _run(
        [_field("total_assets", "1000"), _field("total_liabilities", "400"),
         _field("total_equity", "500")],
        document_type="source_of_wealth",
    )
    findings = _codes(result, "FS.BALANCES")
    assert len(findings) == 1
    # 10% of total assets — clearly material, no "this might just be
    # rounding" qualifier.
    assert findings[0].severity_note is None


def test_fs_balances_prior_year_flags_broken_identity_independently() -> None:
    # Current year reconciles; only the prior-year column is broken.
    result = _run(
        [_field("total_assets", "1000"), _field("total_liabilities", "400"), _field("total_equity", "600"),
         _field("total_assets_prior_year", "2000"),
         _field("total_liabilities_prior_year", "400"),
         _field("total_equity_prior_year", "600")],
        document_type="source_of_wealth",
    )
    assert _codes(result, "FS.BALANCES") == []
    findings = _codes(result, "FS.BALANCES_PRIOR_YEAR")
    assert len(findings) == 1
    assert findings[0].expected == "1,000.00"


def test_fs_balances_still_flags_a_small_discrepancy_with_a_materiality_note() -> None:
    # A $200 gap on ~$2.4M total assets (~0.008%) used to fall entirely
    # within the old 0.1%-of-assets tolerance and was silently dropped.
    # It must now still be flagged, just annotated as small — the lead
    # agent decides whether that's worth escalating, not this check.
    result = _run(
        [_field("total_assets", "2392800"), _field("total_liabilities", "1385300"),
         _field("total_equity", "1007300")],
        document_type="source_of_wealth",
    )
    findings = _codes(result, "FS.BALANCES")
    assert len(findings) == 1
    assert findings[0].severity_note is not None
    assert "%" in findings[0].severity_note


def test_fs_balances_skips_when_neither_year_extracted() -> None:
    result = _run([_field("company_name", "Acme")], document_type="source_of_wealth")
    codes = {s.check_code for s in result.skipped_checks}
    assert "FS.BALANCES" in codes
    assert "FS.BALANCES_PRIOR_YEAR" not in codes


def test_fs_tax_expense_rate_flags_flat_rate_ignoring_exemption() -> None:
    result = _run(
        [_field("profit_before_tax", "1000000"), _field("tax_expense", "170000")],
        document_type="source_of_wealth",
    )
    assert len(_codes(result, "FS.TAX_EXPENSE_RATE")) == 1


# ---------------------------------------------------------------------------
# Notice of assessment
# ---------------------------------------------------------------------------


def test_noa_chain_matches_the_real_generator_formula_for_a_round_number() -> None:
    # 100,000 ECI under the current scheme: exempt_first=7500 (75% of
    # 10,000), exempt_next=45000 (50% of 90,000), after=47500,
    # tax=8075.00, rebate=min(4037.50,40000)=4037.50, payable=4037.50.
    result = _run(
        [_field("eci", "100000"), _field("tax_payable", "4037.50")],
        document_type="notice_of_assessment",
    )
    assert _codes(result, "NOA.PAYABLE") == []


def test_noa_chain_flags_wrong_payable() -> None:
    result = _run(
        [_field("eci", "100000"), _field("tax_payable", "99999")],
        document_type="notice_of_assessment",
    )
    assert len(_codes(result, "NOA.PAYABLE")) == 1


# ---------------------------------------------------------------------------
# Payslip
# ---------------------------------------------------------------------------


def test_payslip_net_flags_earnings_minus_deductions_mismatch() -> None:
    result = _run(
        [_field("net_pay", "5000"), _field("total_earnings", "6000"),
         _field("total_deductions", "500"), _field("earnings_items", []),
         _field("deductions_items", [])],
        document_type="payslip",
    )
    assert len(_codes(result, "PAY.NET")) == 1


def test_payslip_earnings_sum_flags_mismatch() -> None:
    result = _run(
        [_field("net_pay", "5000"),
         _field("earnings_items", [{"amount": 3000}, {"amount": 2000}]),
         _field("total_earnings", "4000")],
        document_type="payslip",
    )
    assert len(_codes(result, "PAY.EARNINGS_SUM")) == 1


def test_cpf_ceiling_skips_without_reference_year_configured() -> None:
    result = _run(
        [_field("employee_cpf", "5000"), _field("ordinary_wages", "10000"),
         _field("pay_date", "2026-01-15")],
        document_type="payslip",
    )
    skip = next((s for s in result.skipped_checks if s.check_code == "PAY.CPF_CEILING"), None)
    assert skip is not None
    assert "not configured" in skip.reason


def test_cpf_ceiling_flags_excess_once_configured() -> None:
    REFERENCE_DATA["cpf_ordinary_wage_ceiling"][2026] = 7400
    try:
        result = _run(
            [_field("employee_cpf", "5000"), _field("ordinary_wages", "10000"),
             _field("pay_date", "2026-01-15")],
            document_type="payslip",
        )
        assert len(_codes(result, "PAY.CPF_CEILING")) == 1
    finally:
        REFERENCE_DATA["cpf_ordinary_wage_ceiling"].clear()


# ---------------------------------------------------------------------------
# Credit report
# ---------------------------------------------------------------------------


def test_cr_summary_counts_flags_mismatched_account_count() -> None:
    result = _run(
        [_field("accounts", [{}, {}]), _field("account_count", "5")],
        document_type="credit_report",
    )
    assert len(_codes(result, "CR.SUMMARY_COUNTS")) == 1


def test_cr_default_consistency_flags_zero_defaults_with_overdue_account() -> None:
    result = _run(
        [_field("accounts", [{"status": "Overdue"}]), _field("default_count", "0")],
        document_type="credit_report",
    )
    assert len(_codes(result, "CR.DEFAULT_CONSISTENCY")) == 1


def test_cr_limit_vs_balance_flags_balance_over_limit() -> None:
    result = _run(
        [_field("accounts", [{"current_balance": 6000, "credit_limit": 5000}])],
        document_type="credit_report",
    )
    assert len(_codes(result, "CR.LIMIT_VS_BALANCE")) == 1


def test_cr_score_flags_template_placeholder() -> None:
    result = _run([_field("bureau_score", "Example Score 1500")], document_type="credit_report")
    assert len(_codes(result, "CR.SCORE")) == 1


# ---------------------------------------------------------------------------
# List of loans
# ---------------------------------------------------------------------------


def test_loan_annuity_flags_instalment_mismatch() -> None:
    result = _run(
        [_field("loan_amount", "100000"), _field("interest_rate", "5"),
         _field("loan_tenure", "60"), _field("monthly_instalment", "1")],
        document_type="list_of_loans",
    )
    assert len(_codes(result, "LOAN.ANNUITY")) == 1


def test_loan_repayment_count_flags_mismatch() -> None:
    result = _run(
        [_field("loan_tenure", "12"), _field("repayment_schedule", [{}] * 6)],
        document_type="list_of_loans",
    )
    assert len(_codes(result, "LOAN.REPAYMENT_COUNT")) == 1


def test_loan_fee_flags_mismatch() -> None:
    result = _run(
        [_field("loan_amount", "100000"), _field("fee_percentage", "1"), _field("fee_amount", "500")],
        document_type="list_of_loans",
    )
    assert len(_codes(result, "LOAN.FEE")) == 1


def test_loan_rate_band_flags_out_of_band_rate() -> None:
    result = _run(
        [_field("interest_rate", "50"), _field("facility_type", "term_loan")],
        document_type="list_of_loans",
    )
    assert len(_codes(result, "LOAN.RATE_BAND")) == 1


# ---------------------------------------------------------------------------
# Proof of address
# ---------------------------------------------------------------------------


def test_poa_balance_arithmetic_flags_mismatch() -> None:
    result = _run(
        [_field("previous_balance", "100"), _field("payment_received", "50"),
         _field("outstanding_balance", "40")],
        document_type="proof_of_address",
    )
    assert len(_codes(result, "POA.BALANCE_ARITHMETIC")) == 1


# ---------------------------------------------------------------------------
# Certificate of incorporation / incumbency / constitution
# ---------------------------------------------------------------------------


def test_acra_share_sum_flags_mismatch() -> None:
    result = _run(
        [_field("shareholders", [{"shares": 300000}, {"shares": 100000}]),
         _field("issued_shares", "500000")],
        document_type="certificate_of_incumbency",
    )
    assert len(_codes(result, "ACRA.SHARE_SUM")) == 1


def test_acra_officers_flags_no_director() -> None:
    result = _run(
        [_field("officers", [{"role": "Secretary"}]), _field("incorporation_date", "2020-01-01")],
        document_type="certificate_of_incumbency",
    )
    assert len(_codes(result, "ACRA.OFFICERS")) == 1


def test_const_subscriber_sum_flags_mismatch() -> None:
    result = _run(
        [_field("subscribers", [{"shares": 300000}, {"shares": 100000}]),
         _field("issued_shares", "500000")],
        document_type="company_constitution",
    )
    assert len(_codes(result, "CONST.SUBSCRIBER_SUM")) == 1


def test_const_quorum_flags_quorum_exceeding_subscribers() -> None:
    result = _run(
        [_field("subscribers", [{}, {}]), _field("quorum", "5")],
        document_type="company_constitution",
    )
    assert len(_codes(result, "CONST.QUORUM")) == 1


def _build_mrz(passport_no: str, dob: str, expiry: str) -> str:
    line1 = "P<SGPTAN<<JOVI<<<<<<<<<<<<<<<<<<<<<<<<<<<<<".ljust(44, "<")[:44]
    passport_no = passport_no.ljust(9, "<")[:9]
    p_chk = _mrz_check_digit(passport_no)
    dob_chk = _mrz_check_digit(dob)
    exp_chk = _mrz_check_digit(expiry)
    personal_no = "<" * 14
    pn_chk = 0
    composite_input = f"{passport_no}{p_chk}{dob}{dob_chk}{expiry}{exp_chk}{personal_no}{pn_chk}"
    composite = _mrz_check_digit(composite_input)
    line2 = f"{passport_no}{p_chk}SGP{dob}{dob_chk}M{expiry}{exp_chk}{personal_no}{pn_chk}{composite}"
    return f"{line1}\n{line2}"


def test_mrz_format_requires_two_44_character_lines() -> None:
    result = _run([_field("mrz", "too short")], document_type="passport")
    assert len(_codes(result, "ID.MRZ_FORMAT")) == 1


def test_mrz_checkdigits_valid_mrz_passes_clean() -> None:
    mrz = _build_mrz("E1234567A", "900101", "300101")
    result = _run(
        [_field("mrz", mrz), _field("passport_number", "E1234567A"),
         _field("date_of_birth", "1990-01-01")],
        document_type="passport",
    )
    assert _codes(result, "ID.MRZ_FORMAT") == []
    assert _codes(result, "ID.MRZ_CHECKDIGITS") == []
    assert _codes(result, "ID.MRZ_VS_VIZ") == []


def test_mrz_checkdigits_flags_corrupted_check_digit() -> None:
    mrz = _build_mrz("E1234567A", "900101", "300101")
    lines = mrz.split("\n")
    corrupted_char = "1" if lines[1][9] != "1" else "2"
    lines[1] = lines[1][:9] + corrupted_char + lines[1][10:]
    result = _run([_field("mrz", "\n".join(lines))], document_type="passport")
    assert len(_codes(result, "ID.MRZ_CHECKDIGITS")) >= 1


def test_mrz_vs_viz_flags_passport_number_mismatch() -> None:
    mrz = _build_mrz("E1234567A", "900101", "300101")
    result = _run(
        [_field("mrz", mrz), _field("passport_number", "X9999999Z")], document_type="passport",
    )
    assert len(_codes(result, "ID.MRZ_VS_VIZ")) == 1


# ---------------------------------------------------------------------------
# FATCA/CRS
# ---------------------------------------------------------------------------


def test_fatca_tin_or_reason_flags_both_present() -> None:
    result = _run(
        [_field("tax_residences", [{"tin": "202312345K", "no_tin_reason": ["A"]}])],
        document_type="fatca_crs_declaration",
    )
    assert len(_codes(result, "FATCA.TIN_OR_REASON")) == 1


def test_fatca_entity_type_flags_multiple_selected() -> None:
    result = _run(
        [_field("entity_type", ["A", "B"])], document_type="fatca_crs_declaration",
    )
    assert len(_codes(result, "FATCA.ENTITY_TYPE")) == 1


def test_fatca_entity_type_flags_passive_without_controlling_persons() -> None:
    result = _run(
        [_field("entity_type", "D")], document_type="fatca_crs_declaration",
    )
    assert len(_codes(result, "FATCA.ENTITY_TYPE")) == 1


def test_fatca_us_person_flags_no_answer_with_a_us_tin() -> None:
    result = _run(
        [_field("specified_us_person", "No"), _field("us_tin", "123-45-6789")],
        document_type="fatca_crs_declaration",
    )
    assert len(_codes(result, "FATCA.US_PERSON")) == 1
