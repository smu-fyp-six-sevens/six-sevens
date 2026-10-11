from __future__ import annotations

import random
from types import SimpleNamespace

from app.fraud_detection.field_catalogue import array_rows
from app.fraud_detection.font_consistency import ExtractedField
from app.fraud_detection.numerical_validation.runner import Binding, ROW_ROLES, benford_summary, run_numerical_checks
from app.fraud_detection.numerical_validation.common import REFERENCE_DATA
from app.fraud_detection.numerical_validation.identity_company import _mrz_check_digit, _nric_expected_letter
from app.fraud_detection.numerical_validation.statements import BENFORD_MIN_SAMPLE, _benford


def _field(name: str, value, confidence: float = 0.95) -> ExtractedField:
    return ExtractedField(field_name=name, field_type="string", field_value=value, confidence=confidence)


def _auto_bindings(fields: list[ExtractedField]) -> list[Binding]:
    """What a correct binder would produce: each row role points at the first row carrying one of its labels."""
    rows = array_rows(next((f.field_value for f in fields if f.field_name == "income_statement_items"), None)) or []
    bindings = []
    for role, aliases in ROW_ROLES.items():
        wanted = {alias.lower() for alias in aliases}
        for index, row in enumerate(rows):
            if str(row.get("label", "")).strip().lower() in wanted:
                bindings.append(Binding(role=role, field="income_statement_items", row=index))
                break
    return bindings


def _run(fields: list[ExtractedField], document_type: str | None = None):
    outcome = run_numerical_checks(document_type, fields, _auto_bindings(fields))
    return SimpleNamespace(
        findings=outcome.findings,
        skipped_checks=outcome.skipped,
        benford=benford_summary(document_type, outcome),
        benford_excluding_fees=outcome.benford_excluding_fees,
        unable_to_assess=not fields,
    )


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


def test_amount_parse_does_not_flag_a_plain_percentage_rate() -> None:
    result = _run([_field("interest_rate", "6.50% per annum")])
    assert _codes(result, "GEN.AMOUNT_PARSE") == []


def test_amount_parse_does_not_flag_a_reference_rate_formula() -> None:
    # doc_gen_scripts' overdraft template renders this shape; the effective
    # rate is the "(currently N%)" clause.
    result = _run([_field("interest_rate", "Bank's Prime Rate + 2.50% (currently 6.72% p.a.)")])
    assert _codes(result, "GEN.AMOUNT_PARSE") == []


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


def test_bank_identifiers_skip_a_bank_not_in_the_reference_table() -> None:
    result = _run(
        [_field("bank_name", "Bank of Atlantis"), _field("bank_registration_no", "199900001A")],
        document_type="bank_statement",
    )
    skip = next(s for s in result.skipped_checks if s.check_code == "BS.BANK_IDENTIFIERS")
    assert "not configured" in skip.reason


def test_bank_identifiers_accept_the_real_registration_number_under_any_name_form() -> None:
    for name in ("OCBC Bank", "OVERSEA-CHINESE BANKING CORPORATION LIMITED", "Oversea-Chinese Banking Corporation Limited"):
        result = _run(
            [_field("bank_name", name), _field("bank_registration_no", "193200032W")],
            document_type="bank_statement",
        )
        assert _codes(result, "BS.BANK_IDENTIFIERS") == [], name


def test_bank_identifiers_flag_a_wrong_registration_number() -> None:
    result = _run(
        [_field("bank_name", "OCBC Bank"), _field("bank_registration_no", "Co.Reg.no.:196800000R")],
        document_type="bank_statement",
    )
    findings = _codes(result, "BS.BANK_IDENTIFIERS")
    assert len(findings) == 1
    assert findings[0].expected == "193200032W"


def test_bank_identifiers_treat_posb_as_dbs() -> None:
    ok = _run(
        [_field("bank_name", "POSB"), _field("bank_registration_no", "196800306E"),
         _field("bic_code", "DBSSSGSGXXX")],
        document_type="bank_statement",
    )
    wrong = _run(
        [_field("bank_name", "POSB"), _field("bank_registration_no", "199900001A")],
        document_type="bank_statement",
    )
    assert _codes(ok, "BS.BANK_IDENTIFIERS") == []
    assert len(_codes(wrong, "BS.BANK_IDENTIFIERS")) == 1


def test_bank_identifiers_accept_an_11_character_bic_with_the_head_office_suffix() -> None:
    result = _run(
        [_field("bank_name", "OCBC Bank"), _field("bic_code", "OCBCSGSGXXX")],
        document_type="bank_statement",
    )
    assert _codes(result, "BS.BANK_IDENTIFIERS") == []


def test_bank_identifiers_flag_another_banks_bic() -> None:
    result = _run(
        [_field("bank_name", "OCBC Bank"), _field("bic_code", "DBSSSGSGXXX")],
        document_type="bank_statement",
    )
    assert len(_codes(result, "BS.BANK_IDENTIFIERS")) == 1


def test_bank_identifiers_skip_when_the_statement_prints_no_registration_number() -> None:
    result = _run([_field("bank_name", "DBS Bank Ltd")], document_type="bank_statement")
    assert any(s.check_code == "BS.BANK_IDENTIFIERS" for s in result.skipped_checks)


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


def test_running_balance_uses_dates_to_tell_a_newest_first_statement() -> None:
    # Newest-first (OCBC-style): the dates decide the direction, so a break
    # is reported against the right row rather than the whole list failing.
    newest_first = [
        {"date": "31 Aug 2026", "amount": -35.00, "balance": 110251.40},
        {"date": "29 Aug 2026", "amount": 18141.88, "balance": 110286.40},
        {"date": "25 Aug 2026", "amount": -0.50, "balance": 92144.52},
        {"date": "25 Aug 2026", "amount": -5709.40, "balance": 92145.02},
    ]
    result = _run([_field("transactions", newest_first)], document_type="bank_statement")
    assert _codes(result, "BS.RUNNING_BALANCE") == []


def test_open_close_handles_a_newest_first_statement() -> None:
    # Rows from a real OCBC statement, listed newest-first. The derived
    # opening/closing must come from the chronologically first/last rows,
    # not list positions — otherwise the stated header balances are
    # falsely reported as mismatching.
    newest_first = [
        {"date": "05 Jun 2026", "amount": 44573.19, "balance": 86709.09},
        {"date": "01 Jun 2026", "amount": -4737.73, "balance": 42135.90},
    ]
    result = _run(
        [_field("transactions", newest_first), _field("opening_balance", "46,873.63"),
         _field("ledger_balance", "86,709.09")],
        document_type="bank_statement",
    )
    assert _codes(result, "BS.STATED_OPENING") == []
    assert _codes(result, "BS.STATED_CLOSING") == []
    assert _codes(result, "BS.OPEN_CLOSE") == []


def test_open_close_still_flags_a_wrong_stated_opening_on_a_newest_first_statement() -> None:
    newest_first = [
        {"date": "05 Jun 2026", "amount": 44573.19, "balance": 86709.09},
        {"date": "01 Jun 2026", "amount": -4737.73, "balance": 42135.90},
    ]
    result = _run(
        [_field("transactions", newest_first), _field("opening_balance", "50,000.00"),
         _field("ledger_balance", "86,709.09")],
        document_type="bank_statement",
    )
    assert len(_codes(result, "BS.STATED_OPENING")) == 1


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


def test_fs_tax_expense_rate_flags_flat_rate_ignoring_exemption() -> None:
    result = _run(
        [_field("income_statement_items", [
            {"label": "Profit before taxation", "amount": 1000000},
            {"label": "Tax expense", "amount": 170000},
        ])],
        document_type="source_of_wealth",
    )
    assert len(_codes(result, "FS.TAX_EXPENSE_RATE")) == 1


def test_fs_re_rollforward_flags_broken_roll_forward() -> None:
    result = _run(
        [_field("retained_earnings_opening", "100000"), _field("profit_for_the_year", "50000"),
         _field("dividends_paid", "0"), _field("retained_earnings_closing", "999999")],
        document_type="source_of_wealth",
    )
    assert len(_codes(result, "FS.RE_ROLLFORWARD")) == 1


def test_fs_ppe_rollforward_flags_negative_implied_additions() -> None:
    result = _run(
        [_field("ppe_opening", "100000"), _field("ppe_closing", "10000"),
         _field("income_statement_items", [
             {"label": "Depreciation of property, plant and equipment", "amount": 5000},
         ])],
        document_type="source_of_wealth",
    )
    assert len(_codes(result, "FS.PPE_ROLLFORWARD")) == 1


def test_fs_debt_classification_flags_long_tenure_loan_with_no_non_current_portion() -> None:
    result = _run(
        [_field("borrowings_current", "50000"), _field("loan_tenure", "60")],
        document_type="source_of_wealth",
    )
    assert len(_codes(result, "FS.DEBT_CLASSIFICATION")) == 1


def test_fs_ratio_bands_flags_finance_costs_outside_band() -> None:
    result = _run(
        [_field("income_statement_items", [{"label": "Finance costs", "amount": 100000}]),
         _field("borrowings_opening", "100000"),
         _field("borrowings_closing", "100000")],
        document_type="source_of_wealth",
    )
    assert len(_codes(result, "FS.RATIO_BANDS")) == 1


def test_fs_ratio_bands_flags_gross_margin_outside_band() -> None:
    result = _run(
        [_field("revenue", "100000"),
         _field("income_statement_items", [{"label": "Cost of sales", "amount": 200000}])],
        document_type="source_of_wealth",
    )
    assert len(_codes(result, "FS.RATIO_BANDS")) == 1


def test_fs_note_refs_flags_a_reference_with_no_matching_note() -> None:
    result = _run(
        [_field("notes", [{"note_number": "3", "heading": "Revenue"}]),
         _field("income_statement_items", [{"label": "Revenue", "amount": 100000, "note": "3"}]),
         _field("balance_sheet_items", [
             {"section": "equity", "label": "Share capital", "amount": 1000, "note": "7"},
         ])],
        document_type="source_of_wealth",
    )
    findings = _codes(result, "FS.NOTE_REFS")
    assert len(findings) == 1
    assert "7" in findings[0].summary



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


# The tax computation of a real amended NOA (C030, YA2025, ECI 321,800).
_C030_COMPUTATION = [
    {"line": "First 10,000 @ 75.00%", "amount": 7500},
    {"line": "Next 190,000 @ 50.00%", "amount": 95000},
    {"line": "Estimated Chargeable Income after deducting Exempt Amount", "amount": 219300},
    {"line": "Tax on 219,300 @ 17.00%", "amount": 37281.0},
    {"line": "Tax Assessed", "amount": 37281.0},
    {"line": "Less: Tax Rebate (net of CIT Rebate Cash Grant, if any)", "amount": 18640.5},
    {"line": "Estimated Tax Payable", "amount": 18640.5},
    {"line": "Less: Previous Assessment dated 12 May 2025", "amount": 37281.0},
    {"line": "Estimated Tax Discharged per this Assessment", "amount": 18640.5},
]


def _noa(computation: list[dict], payable: str = "18,640.50"):
    return _run(
        [_field("eci", "321,800"), _field("tax_payable", payable),
         _field("year_of_assessment", "2025"), _field("tax_computation", computation)],
        document_type="notice_of_assessment",
    )


def test_noa_chain_passes_a_genuine_amended_assessment() -> None:
    # Regression: "Tax on 219,300 @ 17.00%" used to be read as the
    # chargeable-income-after-exemption line because its label contains "17".
    result = _noa(_C030_COMPUTATION)
    assert [f for f in result.findings if f.check_code.startswith("NOA")] == []


def test_noa_chain_accepts_a_rebate_printed_net_of_the_cash_grant() -> None:
    # A company that got the $2,000 CIT Rebate Cash Grant: rebate printed as
    # 18,640.50 - 2,000 = 16,640.50, so payable is 37,281 - 16,640.50.
    computation = [dict(r) for r in _C030_COMPUTATION[:5]] + [
        {"line": "Less: Tax Rebate (net of CIT Rebate Cash Grant, if any)", "amount": 16640.5},
        {"line": "Estimated Tax Payable", "amount": 20640.5},
    ]
    result = _noa(computation, payable="20,640.50")
    assert [f for f in result.findings if f.check_code.startswith("NOA")] == []


def test_noa_chain_flags_a_wrong_exemption_tier() -> None:
    computation = [dict(r) for r in _C030_COMPUTATION]
    computation[1]["amount"] = 85000
    assert len(_codes(_noa(computation), "NOA.EXEMPTION_TIERS")) == 1


def test_noa_chain_flags_a_wrong_chargeable_income_after_exemption() -> None:
    computation = [dict(r) for r in _C030_COMPUTATION]
    computation[2]["amount"] = 229300
    assert len(_codes(_noa(computation), "NOA.CHARGEABLE_AFTER_EXEMPTION")) == 1


def test_noa_chain_flags_a_discharge_that_does_not_net_off_the_previous_assessment() -> None:
    computation = [dict(r) for r in _C030_COMPUTATION]
    computation[8]["amount"] = 8640.5
    assert len(_codes(_noa(computation), "NOA.AMENDMENT")) == 1


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


def test_cpf_ceiling_skips_a_pay_date_before_the_reference_table() -> None:
    result = _run(
        [_field("employee_cpf", "5000"), _field("ordinary_wages", "10000"),
         _field("pay_date", "2015-06-30")],
        document_type="payslip",
    )
    skip = next((s for s in result.skipped_checks if s.check_code == "PAY.CPF_CEILING"), None)
    assert skip is not None
    assert "not configured" in skip.reason


def test_cpf_ceiling_flags_a_contribution_above_20_percent_of_the_2026_ceiling() -> None:
    # 2026 ceiling is $8,000, so the most an employee contributes is $1,600.
    result = _run(
        [_field("employee_cpf", "1700"), _field("ordinary_wages", "10000"),
         _field("pay_date", "2026-01-15")],
        document_type="payslip",
    )
    assert len(_codes(result, "PAY.CPF_CEILING")) == 1


def test_cpf_ceiling_uses_the_ceiling_in_force_on_the_pay_date_within_2023() -> None:
    # $6,000 until 31 Aug 2023, $6,300 from 1 Sep 2023: a $1,260 contribution
    # is only possible on the later ceiling.
    fields = [_field("employee_cpf", "1260"), _field("ordinary_wages", "10000")]
    august = _run(fields + [_field("pay_date", "2023-08-31")], document_type="payslip")
    october = _run(fields + [_field("pay_date", "2023-10-31")], document_type="payslip")
    assert len(_codes(august, "PAY.CPF_CEILING")) == 1
    assert _codes(october, "PAY.CPF_CEILING") == []


def test_cpf_base_flags_contribution_calculated_on_basic_pay_alone() -> None:
    result = _run(
        [_field("employee_cpf", "200"), _field("basic_pay", "1000"), _field("ordinary_wages", "2000")],
        document_type="payslip",
    )
    assert len(_codes(result, "PAY.CPF_BASE")) == 1


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


def test_cr_aggregate_sum_flags_mismatch_with_stated_total() -> None:
    result = _run(
        [_field("accounts", [{"current_balance": 1000}, {"current_balance": 2000}]),
         _field("total_outstanding_balance", "5000")],
        document_type="credit_report",
    )
    assert len(_codes(result, "CR.AGGREGATE_SUM")) == 1


def test_cr_amortisation_flags_a_balance_fall_bigger_than_the_instalment() -> None:
    result = _run(
        [_field("accounts", [{
            "monthly_instalment": 100,
            "balance_history": [{"balance": 1000}, {"balance": 200}],
        }])],
        document_type="credit_report",
    )
    assert len(_codes(result, "CR.AMORTISATION")) == 1


def test_cr_amortisation_reads_a_newest_first_history_in_date_order() -> None:
    # Newest-first like the report's "Preceding 5 Months" table: read in
    # list order the balance only rises, so a too-large fall went unseen.
    result = _run(
        [_field("accounts", [{
            "monthly_instalment": 100,
            "balance_history": [{"balance": 200}, {"balance": 1000}],
        }])],
        document_type="credit_report",
    )
    assert len(_codes(result, "CR.AMORTISATION")) == 1


def test_cr_linear_series_flags_a_mechanically_constant_balance_fall() -> None:
    result = _run(
        [_field("accounts", [{
            "balance_history": [
                {"balance": 1000}, {"balance": 900}, {"balance": 800},
                {"balance": 700}, {"balance": 600},
            ],
        }])],
        document_type="credit_report",
    )
    assert len(_codes(result, "CR.LINEAR_SERIES")) == 1


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


def test_loan_rate_band_runs_against_a_reference_rate_formula() -> None:
    # Previously silently skipped for every overdraft facility, since
    # _as_number couldn't parse this shape at all — 6.72 is within the
    # 5-15% overdraft band, so this should run and find nothing wrong.
    result = _run(
        [_field("interest_rate", "Bank's Prime Rate + 2.50% (currently 6.72% p.a.)"),
         _field("facility_type", "overdraft")],
        document_type="list_of_loans",
    )
    assert _codes(result, "LOAN.RATE_BAND") == []
    assert "LOAN.RATE_BAND" not in {s.check_code for s in result.skipped_checks}


def test_loan_scheme_params_flags_amount_over_the_schemes_cap() -> None:
    REFERENCE_DATA["loan_scheme_params"]["Enterprise Financing Scheme"] = {"amount_cap": 500000}
    try:
        result = _run(
            [_field("scheme_name", "Enterprise Financing Scheme"), _field("loan_amount", "900000")],
            document_type="list_of_loans",
        )
        assert len(_codes(result, "LOAN.SCHEME_PARAMS")) == 1
    finally:
        REFERENCE_DATA["loan_scheme_params"].clear()


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


def test_poa_usage_match_flags_stated_usage_not_matching_the_trend() -> None:
    result = _run(
        [_field("usage_this_period", "500"),
         _field("consumption_trend", [{"month": "Jan", "usage": 200}])],
        document_type="proof_of_address",
    )
    assert len(_codes(result, "POA.USAGE_MATCH")) == 1


def test_amount_parse_ignores_identifier_fields_that_mention_tax() -> None:
    result = _run([_field("tax_reference_number", "202034564S")], document_type="notice_of_assessment")
    assert _codes(result, "GEN.AMOUNT_PARSE") == []


def test_fee_schedule_ignores_supplier_payments_and_two_fixed_charge_tiers() -> None:
    # From a real OCBC statement: $35.00 monthly and $0.50 per-transaction
    # charges share a description, and GIRO/FAST supplier payments vary.
    transactions = (
        [{"description": "Txn Charges Billing / CHARGES / Billing Statement", "amount": a}
         for a in (-35.00, -0.50, -35.00, -0.50, -35.00, -0.50)]
        + [{"description": "Outward Remittance / GIRO / TAN HENG PACKAGING PTE LTD", "amount": a}
           for a in (-26758.55, -22342.15, -16801.43, -5145.57)]
    )
    result = _run([_field("transactions", transactions)], document_type="bank_statement")
    assert _codes(result, "BS.FEE_SCHEDULE") == []


def test_fee_schedule_flags_a_charge_with_free_form_amounts() -> None:
    transactions = [{"description": "Service Charge", "amount": a} for a in (-35.00, -37.20, -41.15, -29.80)]
    result = _run([_field("transactions", transactions)], document_type="bank_statement")
    assert len(_codes(result, "BS.FEE_SCHEDULE")) == 1


def test_poa_usage_match_accepts_a_newest_first_trend() -> None:
    result = _run(
        [_field("usage_this_period", "500"),
         _field("consumption_trend", [
             {"month": "Mar", "usage": 500}, {"month": "Feb", "usage": 420}, {"month": "Jan", "usage": 380},
         ])],
        document_type="proof_of_address",
    )
    assert _codes(result, "POA.USAGE_MATCH") == []


def test_poa_usage_match_accepts_an_oldest_first_trend() -> None:
    result = _run(
        [_field("usage_this_period", "500"),
         _field("consumption_trend", [
             {"month": "Jan", "usage": 380}, {"month": "Feb", "usage": 420}, {"month": "Mar", "usage": 500},
         ])],
        document_type="proof_of_address",
    )
    assert _codes(result, "POA.USAGE_MATCH") == []


def test_poa_unit_rate_flags_an_implausible_tariff() -> None:
    result = _run(
        [_field("current_charges", "1000"), _field("usage_this_period", "1")],
        document_type="proof_of_address",
    )
    assert len(_codes(result, "POA.UNIT_RATE")) == 1


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


def test_acra_officers_reads_the_role_whatever_its_column_is_called() -> None:
    # A real ACRA extract: the role column was transcribed as "position_held".
    officers = [
        {"name": "CHIA WEI MING", "position_held": "Director", "date_of_appointment": "15/03/2022"},
        {"name": "RAJESWARI D/O MUTHUSAMY", "position_held": "Secretary", "date_of_appointment": "09/05/2022"},
    ]
    result = _run(
        [_field("officers", officers), _field("incorporation_date", "2022-03-15")],
        document_type="certificate_of_incumbency",
    )
    assert _codes(result, "ACRA.OFFICERS") == []


def test_acra_capital_per_share_flags_an_implausible_per_share_value() -> None:
    result = _run(
        [_field("share_capital", "2000000"), _field("issued_shares", "1")],
        document_type="certificate_of_incumbency",
    )
    assert len(_codes(result, "ACRA.CAPITAL_PER_SHARE")) == 1


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


def test_const_capital_per_share_flags_an_implausible_per_share_value() -> None:
    result = _run(
        [_field("share_capital", "2000000"), _field("issued_shares", "1")],
        document_type="company_constitution",
    )
    assert len(_codes(result, "CONST.CAPITAL_PER_SHARE")) == 1


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


def test_mrz_lines_joined_by_a_space_are_parsed_as_two_lines() -> None:
    # Real extracted value: the two TD3 lines joined with a space, 89 chars.
    mrz = (
        "PASGPLIM<<CHOON<GUAN<<<<<<<<<<<<<<<<<<<<<<<< "
        "K1791518E6SGP7512032F3001075S7534567X<<<<<66"
    )
    result = _run([_field("mrz", mrz)], document_type="passport")
    assert _codes(result, "ID.MRZ_FORMAT") == []
    assert _codes(result, "ID.MRZ_CHECKDIGITS") == []


def test_mrz_lines_with_no_separator_are_split_at_44() -> None:
    mrz = _build_mrz("E1234567A", "900101", "300101").replace("\n", "")
    result = _run([_field("mrz", mrz)], document_type="passport")
    assert _codes(result, "ID.MRZ_FORMAT") == []
    assert _codes(result, "ID.MRZ_CHECKDIGITS") == []


def test_mrz_checkdigits_still_run_and_flag_when_lines_are_space_joined() -> None:
    lines = _build_mrz("E1234567A", "900101", "300101").split("\n")
    corrupted_char = "1" if lines[1][9] != "1" else "2"
    lines[1] = lines[1][:9] + corrupted_char + lines[1][10:]
    result = _run([_field("mrz", " ".join(lines))], document_type="passport")
    assert len(_codes(result, "ID.MRZ_CHECKDIGITS")) >= 1


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
