from __future__ import annotations

import pytest
import random
from types import SimpleNamespace

from app.fraud_detection.field_catalogue import array_rows
from app.fraud_detection.font_consistency import ExtractedField
from app.fraud_detection.numerical_validation.runner import Binding, ROW_ROLES, benford_summary, run_numerical_checks
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
    """Both check groups: the numerical tool and the external-validation tool's static reference checks."""
    bindings = _auto_bindings(fields)
    outcome = run_numerical_checks(document_type, fields, bindings)
    external = run_numerical_checks(document_type, fields, bindings, group="external")
    return SimpleNamespace(
        findings=[*outcome.findings, *external.findings],
        skipped_checks=[*outcome.skipped, *external.skipped],
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


def test_amount_parse_marks_unparseable_amount_as_needs_review() -> None:
    result = _run([_field("net_pay", "not a number")])
    findings = _codes(result, "GEN.AMOUNT_PARSE")
    assert len(findings) == 1
    assert findings[0].needs_review is True


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


def test_payment_patterns_are_no_longer_per_document_checks() -> None:
    """Duplicates, card diversity and recurring variance are cross-document hypotheses now."""
    transactions = [
        *[{"description": f"CARD 1234-XXXX-XXXX-{i:04d}", "amount": -1.0, "balance": 0.0} for i in range(6)],
        *[{"description": "NETFLIX", "amount": -15.0, "balance": 0.0}] * 2,
        {"description": "RENT PAYMENT", "amount": -1000.0, "balance": 0.0},
        {"description": "RENT PAYMENT", "amount": -1000.0, "balance": 0.0},
        {"description": "RENT PAYMENT", "amount": -1500.0, "balance": 0.0},
    ]
    result = _run([_field("transactions", transactions)], document_type="bank_statement")
    for code in ("BS.CARD_DIVERSITY", "BS.RECURRING_VARIANCE", "STAT.DUPLICATES"):
        assert _codes(result, code) == []


def test_repeated_payments_groups_every_repeat_once() -> None:
    from app.fraud_detection.numerical_validation.statements import repeated_payments

    rows = [{"description": "NETFLIX", "amount": -15.0}, {"description": "OTHER", "amount": -1.0}] + [
        {"description": "NETFLIX", "amount": -15.0}
    ] * 2
    assert repeated_payments(rows) == [("netflix", -15.0, [0, 2, 3])]


def test_card_usage_counts_distinct_cards() -> None:
    from app.fraud_detection.numerical_validation.statements import card_usage

    rows = [{"description": f"CARD 1234-XXXX-XXXX-{i % 2:04d}"} for i in range(6)]
    transactions, cards = card_usage(rows)
    assert (transactions, len(cards)) == (6, 2)


def test_recurring_payments_leave_out_fees_and_one_offs() -> None:
    from app.fraud_detection.numerical_validation.statements import recurring_payments

    rows = [{"description": "RENT 1", "amount": -1000.0}] * 2 + [{"description": "RENT 2", "amount": -1500.0}]
    rows += [{"description": "SERVICE CHARGE", "amount": -5.0}] * 3 + [{"description": "ONE OFF", "amount": -9.0}]
    assert recurring_payments(rows) == {"rent #": [1000.0, 1000.0, 1500.0]}


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
        [_field("eci", "100000"), _field("tax_payable", "4037.50"), _field("year_of_assessment", "2025")],
        document_type="notice_of_assessment",
    )
    assert _codes(result, "NOA.PAYABLE") == []


def _noa_for(year: str | None, eci: str, payable: str, computation: list[dict] | None = None):
    fields = [_field("eci", eci), _field("tax_payable", payable)]
    if year is not None:
        fields.append(_field("year_of_assessment", year))
    if computation is not None:
        fields.append(_field("tax_computation", computation))
    return [f for f in _run(fields, document_type="notice_of_assessment").findings if f.check_code.startswith("NOA")]


# Expected figures worked by hand from IRAS's rules (sg_reference/corporate_tax.py).


def test_noa_ya2021_to_2023_have_no_rebate() -> None:
    # ECI 100,000: exempt 7,500 + 45,000, tax 17% of 47,500 = 8,075, no rebate.
    assert _noa_for("2023", "100000", "8075") == []
    rebated = [{"line": "Less: Tax Rebate", "amount": 4037.5}]
    codes = {f.check_code for f in _noa_for("2023", "100000", "4037.50", rebated)}
    assert codes == {"NOA.REBATE", "NOA.PAYABLE"}


def test_noa_ya2020_rebate_is_25_percent() -> None:
    # Tax 8,075 less 25% (2,018.75, cap 15,000) = 6,056.25.
    assert _noa_for("2020", "100000", "6056.25") == []
    assert len(_noa_for("2020", "100000", "4037.50")) == 1


def test_noa_ya2019_uses_the_wider_exemption_band_and_20_percent_rebate() -> None:
    # ECI 400,000: 7,500 + 50% of the next 290,000 (145,000) exempt, tax 17% of 247,500 = 42,075,
    # rebate 20% = 8,415 (cap 10,000), payable 33,660.
    computation = [{"line": "First 10,000 @ 75%", "amount": 7500}, {"line": "Next 290,000 @ 50%", "amount": 145000}]
    assert _noa_for("2019", "400000", "33660", computation) == []


def test_noa_accepts_the_start_up_tax_exemption() -> None:
    # A new company: 75% of the first 100,000 and 50% of the next 100,000. ECI 150,000:
    # exempt 75,000 + 25,000, tax 17% of 50,000 = 8,500, rebate 50% = 4,250.
    computation = [{"line": "First 100,000 @ 75%", "amount": 75000}, {"line": "Next 100,000 @ 50%", "amount": 25000}]
    assert _noa_for("2025", "150000", "4250", computation) == []


def test_noa_ya2026_accepts_the_original_and_the_enhanced_rebate() -> None:
    # ECI 500,000: tax 17% of 397,500 = 67,575. Enhanced: 50% = 33,787.50. Original Budget 2026
    # terms: 40% = 27,030 (cap 30,000), or 25,530 net of the 1,500 cash grant.
    assert _noa_for("2026", "500000", "33787.50") == []
    assert _noa_for("2026", "500000", "40545") == []
    assert _noa_for("2026", "500000", "42045") == []
    assert len(_noa_for("2026", "500000", "50000")) == 1


def test_noa_skips_a_year_without_known_rules_or_no_year() -> None:
    for year in ("2010", None):
        result = _run([_field("eci", "100000"), _field("tax_payable", "1")]
                      + ([_field("year_of_assessment", year)] if year else []), document_type="notice_of_assessment")
        assert _codes(result, "NOA.PAYABLE") == []
        assert any(s.check_code == "NOA.PAYABLE" for s in result.skipped_checks)


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
        [_field("employee_cpf", "200"), _field("basic_pay", "1000"), _field("ordinary_wages", "2000"),
         _field("pay_date", "2026-03-31")],
        document_type="payslip",
    )
    assert len(_codes(result, "PAY.CPF_BASE")) == 1
    assert _codes(result, "PAY.CPF_RATE") == []  # reported once, by the check that names the mistake


# ---------------------------------------------------------------------------
# Credit report
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# List of loans
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Proof of address
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Certificate of incorporation / incumbency / constitution
# ---------------------------------------------------------------------------


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


# The C067 passport as extracted: the LLM wrote one "<" too many in the run
# after the name, and OCR read the printed ID's check letter I as a 1.
_C067_MRZ_LINE1 = "PASGPCHIA<<WEI<MING<<<<<<<<<<<<<<<<<<<<<<<<<"
_C067_MRZ_LINE2 = "K5690850D0SGP8904126F3511060S8934521I<<<<<22"


def test_a_miscounted_run_of_mrz_filler_is_not_a_format_error() -> None:
    too_long = _C067_MRZ_LINE1 + "<"
    too_short = _C067_MRZ_LINE2.replace("<<<<<", "<<<<", 1)
    for mrz in (too_long + "\n" + _C067_MRZ_LINE2, _C067_MRZ_LINE1 + "\n" + too_short):
        result = _run([_field("mrz", mrz)], document_type="passport")
        assert _codes(result, "ID.MRZ_FORMAT") == []
        assert _codes(result, "ID.MRZ_CHECKDIGITS") == []


def test_an_mrz_line_well_off_44_is_still_a_format_error() -> None:
    mrz = _C067_MRZ_LINE1 + "<<<" + "\n" + _C067_MRZ_LINE2
    assert len(_codes(_run([_field("mrz", mrz)], document_type="passport"), "ID.MRZ_FORMAT")) == 1


def test_mrz_vs_viz_allows_letters_ocr_reads_as_digits() -> None:
    mrz = _C067_MRZ_LINE1 + "\n" + _C067_MRZ_LINE2
    misread = _run([_field("mrz", mrz), _field("nric_number", "S89345211")], document_type="passport")
    assert _codes(misread, "ID.MRZ_VS_VIZ") == []
    different = _run([_field("mrz", mrz), _field("nric_number", "S8934521J")], document_type="passport")
    assert len(_codes(different, "ID.MRZ_VS_VIZ")) == 1


def test_mrz_vs_viz_flags_passport_number_mismatch() -> None:
    mrz = _build_mrz("E1234567A", "900101", "300101")
    result = _run(
        [_field("mrz", mrz), _field("passport_number", "X9999999Z")], document_type="passport",
    )
    assert len(_codes(result, "ID.MRZ_VS_VIZ")) == 1


# ---------------------------------------------------------------------------
# FATCA/CRS
# ---------------------------------------------------------------------------


def test_bank_identifiers_checks_dbs_gst_number() -> None:
    # DBS's GST number is not its UEN: real statements print "GST Registration No: MR-8500180-3".
    genuine = _run([_field("bank_name", "POSB"), _field("bank_gst_no", "MR 8500180 3")], document_type="bank_statement")
    assert _codes(genuine, "BS.BANK_IDENTIFIERS") == []
    uen_as_gst = _run([_field("bank_name", "DBS Bank Ltd"), _field("bank_gst_no", "196800306E")], document_type="bank_statement")
    findings = _codes(uen_as_gst, "BS.BANK_IDENTIFIERS")
    assert len(findings) == 1 and findings[0].field == "bank_gst_no"


def test_bank_identifiers_skips_a_value_not_shaped_like_the_identifier() -> None:
    # Regression: the binder bound bank_contact to bic_code, and the phone number was reported as a fake BIC.
    fields = [_field("bank_name", "DBS Bank Ltd"), _field("bank_contact", "1800 222 2200 (+65 6222 2200 from overseas)")]
    outcome = run_numerical_checks(
        "bank_statement", fields, [Binding(role="bic_code", field="bank_contact", row=None)], group="external",
    )
    assert [f for f in outcome.findings if f.check_code == "BS.BANK_IDENTIFIERS"] == []
    skip = next(s for s in outcome.skipped if s.check_code == "BS.BANK_IDENTIFIERS")
    assert "isn't shaped like a SWIFT BIC" in skip.reason


def test_nric_checksum_reads_nested_rows_and_reports_each_number_once() -> None:
    shareholders = [{"name": "NG BEE LENG", "identification_no": "S7845632F"}]
    result = _run(
        [_field("shareholders", shareholders), _field("notes", "Ng Bee Leng (S7845632F) subscribed 100,000 shares"),
         _field("subscriber_2_id", "S7845632F")],
        document_type="company_constitution",
    )
    findings = _codes(result, "ID.NRIC_CHECKSUM")
    assert len(findings) == 1
    # The ID field is named before the table row, and free text last.
    assert findings[0].field == "subscriber_2_id"
    assert findings[0].summary.startswith("S7845632F in Subscriber 2 ID has an invalid check letter")
    assert "Also printed in Shareholder 1 (NG BEE LENG), Identification No and Notes" in findings[0].summary


def test_nric_finding_needs_review_only_if_every_copy_was_extracted_with_low_confidence() -> None:
    def run(flat_confidence: float, table_confidence: float):
        fields = [
            ExtractedField(field_name="nric_number", field_type="string", field_value="S8934521C", confidence=flat_confidence),
            ExtractedField(field_name="shareholders", field_type="array",
                           field_value=[{"name": "CHIA WEI MING", "identification_no": "S8934521C"}], confidence=table_confidence),
        ]
        return _codes(_run(fields, document_type="certificate_of_incumbency"), "ID.NRIC_CHECKSUM")[0]

    assert run(0.75, 1.0).needs_review is False  # the table read it confidently
    assert run(0.75, 0.75).needs_review is True


def test_poa_balance_accepts_a_payment_printed_as_a_negative() -> None:
    # Regression: "-$199.60" was subtracted as a negative, giving an outstanding balance of 399.20.
    def run(payment: str, outstanding: str, total: str):
        return _run([_field("previous_balance", "$199.60"), _field("payment_received", payment),
                     _field("outstanding_balance", outstanding), _field("current_charges", "$202.22"),
                     _field("total_payable", total)], document_type="proof_of_address")
    for payment in ("-$199.60", "$199.60"):
        assert _codes(run(payment, "$0.00", "$202.22"), "POA.BALANCE_ARITHMETIC") == [], payment
    # Still caught: a total that isn't the outstanding balance plus this month's charges.
    assert len(_codes(run("-$199.60", "$0.00", "$402.22"), "POA.BALANCE_ARITHMETIC")) == 1


# ---------------------------------------------------------------------------
# Rules with no extra assertions: one row per rule, same checks as the
# single-purpose tests these rows replaced.
# ---------------------------------------------------------------------------


_FLAGGED_ONCE = [
    pytest.param(
        'ID.UEN_FORMAT', None, [_field("uen", "195012345K")],
        id='uen_format_rejects_implausible_year_prefix',
    ),
    pytest.param(
        'BS.BANK_IDENTIFIERS', "bank_statement", [_field("bank_name", "OCBC Bank"), _field("bic_code", "DBSSSGSGXXX")],
        id='bank_identifiers_flag_another_banks_bic',
    ),
    pytest.param(
        'BS.AVAILABLE_VS_LEDGER', "bank_statement", [_field("ledger_balance", "1000"), _field("available_balance", "900"), _field("holds", "200")],
        id='available_vs_ledger_flags_mismatch',
    ),
    pytest.param(
        'SOW.NAV', "source_of_wealth", [_field("cash_balance", "1000"), _field("portfolio_value", "5000"),
             _field("net_asset_value", "5500")],
        id='nav_flags_mismatch',
    ),
    pytest.param(
        'FS.TAX_EXPENSE_RATE', "source_of_wealth", [_field("income_statement_items", [
                {"label": "Profit before taxation", "amount": 1000000},
                {"label": "Tax expense", "amount": 170000},
            ])],
        id='fs_tax_expense_rate_flags_flat_rate_ignoring_exemption',
    ),
    pytest.param(
        'FS.RE_ROLLFORWARD', "source_of_wealth", [_field("retained_earnings_opening", "100000"), _field("profit_for_the_year", "50000"),
             _field("dividends_paid", "0"), _field("retained_earnings_closing", "999999")],
        id='fs_re_rollforward_flags_broken_roll_forward',
    ),
    pytest.param(
        'FS.PPE_ROLLFORWARD', "source_of_wealth", [_field("ppe_opening", "100000"), _field("ppe_closing", "10000"),
             _field("income_statement_items", [
                 {"label": "Depreciation of property, plant and equipment", "amount": 5000},
             ])],
        id='fs_ppe_rollforward_flags_negative_implied_additions',
    ),
    pytest.param(
        'FS.DEBT_CLASSIFICATION', "source_of_wealth", [_field("borrowings_current", "50000"), _field("loan_tenure", "60")],
        id='fs_debt_classification_flags_long_tenure_loan_with_no_non_current_portion',
    ),
    pytest.param(
        'FS.RATIO_BANDS', "source_of_wealth", [_field("income_statement_items", [{"label": "Finance costs", "amount": 100000}]),
             _field("borrowings_opening", "100000"),
             _field("borrowings_closing", "100000")],
        id='fs_ratio_bands_flags_finance_costs_outside_band',
    ),
    pytest.param(
        'FS.RATIO_BANDS', "source_of_wealth", [_field("revenue", "100000"),
             _field("income_statement_items", [{"label": "Cost of sales", "amount": 200000}])],
        id='fs_ratio_bands_flags_gross_margin_outside_band',
    ),
    pytest.param(
        'NOA.PAYABLE', "notice_of_assessment", [_field("eci", "100000"), _field("tax_payable", "99999"), _field("year_of_assessment", "2025")],
        id='noa_chain_flags_wrong_payable',
    ),
    pytest.param(
        'PAY.NET', "payslip", [_field("net_pay", "5000"), _field("total_earnings", "6000"),
             _field("total_deductions", "500"), _field("earnings_items", []),
             _field("deductions_items", [])],
        id='payslip_net_flags_earnings_minus_deductions_mismatch',
    ),
    pytest.param(
        'PAY.EARNINGS_SUM', "payslip", [_field("net_pay", "5000"),
             _field("earnings_items", [{"amount": 3000}, {"amount": 2000}]),
             _field("total_earnings", "4000")],
        id='payslip_earnings_sum_flags_mismatch',
    ),
    pytest.param(
        'CR.SUMMARY_COUNTS', "credit_report", [_field("accounts", [{}, {}]), _field("account_count", "5")],
        id='cr_summary_counts_flags_mismatched_account_count',
    ),
    pytest.param(
        'CR.DEFAULT_CONSISTENCY', "credit_report", [_field("accounts", [{"status": "Overdue"}]), _field("default_count", "0")],
        id='cr_default_consistency_flags_zero_defaults_with_overdue_account',
    ),
    pytest.param(
        'CR.LIMIT_VS_BALANCE', "credit_report", [_field("accounts", [{"current_balance": 6000, "credit_limit": 5000}])],
        id='cr_limit_vs_balance_flags_balance_over_limit',
    ),
    pytest.param(
        'CR.SCORE', "credit_report", [_field("bureau_score", "Example Score 1500")],
        id='cr_score_flags_template_placeholder',
    ),
    pytest.param(
        'CR.AGGREGATE_SUM', "credit_report", [_field("accounts", [{"current_balance": 1000}, {"current_balance": 2000}]),
             _field("total_outstanding_balance", "5000")],
        id='cr_aggregate_sum_flags_mismatch_with_stated_total',
    ),
    pytest.param(
        'CR.AMORTISATION', "credit_report", [_field("accounts", [{
                "monthly_instalment": 100,
                "balance_history": [{"balance": 1000}, {"balance": 200}],
            }])],
        id='cr_amortisation_flags_a_balance_fall_bigger_than_the_instalment',
    ),
    pytest.param(
        'CR.LINEAR_SERIES', "credit_report", [_field("accounts", [{
                "balance_history": [
                    {"balance": 1000}, {"balance": 900}, {"balance": 800},
                    {"balance": 700}, {"balance": 600},
                ],
            }])],
        id='cr_linear_series_flags_a_mechanically_constant_balance_fall',
    ),
    pytest.param(
        'LOAN.ANNUITY', "list_of_loans", [_field("loan_amount", "100000"), _field("interest_rate", "5"),
             _field("loan_tenure", "60"), _field("monthly_instalment", "1")],
        id='loan_annuity_flags_instalment_mismatch',
    ),
    pytest.param(
        'LOAN.REPAYMENT_COUNT', "list_of_loans", [_field("loan_tenure", "12"), _field("repayment_schedule", [{}] * 6)],
        id='loan_repayment_count_flags_mismatch',
    ),
    pytest.param(
        'LOAN.FEE', "list_of_loans", [_field("loan_amount", "100000"), _field("fee_percentage", "1"), _field("fee_amount", "500")],
        id='loan_fee_flags_mismatch',
    ),
    pytest.param(
        'LOAN.RATE_BAND', "list_of_loans", [_field("interest_rate", "50"), _field("facility_type", "term_loan")],
        id='loan_rate_band_flags_out_of_band_rate',
    ),
    pytest.param(
        'POA.BALANCE_ARITHMETIC', "proof_of_address", [_field("previous_balance", "100"), _field("payment_received", "50"),
             _field("outstanding_balance", "40")],
        id='poa_balance_arithmetic_flags_mismatch',
    ),
    pytest.param(
        'POA.USAGE_MATCH', "proof_of_address", [_field("usage_this_period", "500"),
             _field("consumption_trend", [{"month": "Jan", "usage": 200}])],
        id='poa_usage_match_flags_stated_usage_not_matching_the_trend',
    ),
    pytest.param(
        'POA.UNIT_RATE', "proof_of_address", [_field("current_charges", "1000"), _field("usage_this_period", "1")],
        id='poa_unit_rate_flags_an_implausible_tariff',
    ),
    pytest.param(
        'ACRA.SHARE_SUM', "certificate_of_incumbency", [_field("shareholders", [{"shares": 300000}, {"shares": 100000}]),
             _field("issued_shares", "500000")],
        id='acra_share_sum_flags_mismatch',
    ),
    pytest.param(
        'ACRA.OFFICERS', "certificate_of_incumbency", [_field("officers", [{"role": "Secretary"}]), _field("incorporation_date", "2020-01-01")],
        id='acra_officers_flags_no_director',
    ),
    pytest.param(
        'ACRA.CAPITAL_PER_SHARE', "certificate_of_incumbency", [_field("share_capital", "2000000"), _field("issued_shares", "1")],
        id='acra_capital_per_share_flags_an_implausible_per_share_value',
    ),
    pytest.param(
        'CONST.SUBSCRIBER_SUM', "company_constitution", [_field("subscribers", [{"shares": 300000}, {"shares": 100000}]),
             _field("issued_shares", "500000")],
        id='const_subscriber_sum_flags_mismatch',
    ),
    pytest.param(
        'CONST.QUORUM', "company_constitution", [_field("subscribers", [{}, {}]), _field("quorum", "5")],
        id='const_quorum_flags_quorum_exceeding_subscribers',
    ),
    pytest.param(
        'CONST.CAPITAL_PER_SHARE', "company_constitution", [_field("share_capital", "2000000"), _field("issued_shares", "1")],
        id='const_capital_per_share_flags_an_implausible_per_share_value',
    ),
    pytest.param(
        'ID.MRZ_FORMAT', "passport", [_field("mrz", "too short")],
        id='mrz_format_requires_two_44_character_lines',
    ),
    pytest.param(
        'FATCA.TIN_OR_REASON', "fatca_crs_declaration", [_field("tax_residences", [{"tin": "202312345K", "no_tin_reason": ["A"]}])],
        id='fatca_tin_or_reason_flags_both_present',
    ),
    pytest.param(
        'FATCA.ENTITY_TYPE', "fatca_crs_declaration", [_field("entity_type", ["A", "B"])],
        id='fatca_entity_type_flags_multiple_selected',
    ),
    pytest.param(
        'FATCA.ENTITY_TYPE', "fatca_crs_declaration", [_field("entity_type", "D")],
        id='fatca_entity_type_flags_passive_without_controlling_persons',
    ),
    pytest.param(
        'FATCA.US_PERSON', "fatca_crs_declaration", [_field("specified_us_person", "No"), _field("us_tin", "123-45-6789")],
        id='fatca_us_person_flags_no_answer_with_a_us_tin',
    ),
]


@pytest.mark.parametrize("check_code, document_type, fields", _FLAGGED_ONCE)
def test_rule_flags_exactly_one_finding(check_code, document_type, fields) -> None:
    result = _run(fields, document_type=document_type)
    assert len(_codes(result, check_code)) == 1


_NOT_FLAGGED = [
    pytest.param(
        'GEN.AMOUNT_PARSE', None, [_field("interest_rate", "6.50% per annum")],
        id='amount_parse_does_not_flag_a_plain_percentage_rate',
    ),
    pytest.param(
        'BS.BANK_IDENTIFIERS', "bank_statement", [_field("bank_name", "OCBC Bank"), _field("bic_code", "OCBCSGSGXXX")],
        id='bank_identifiers_accept_an_11_character_bic_with_the_head_office_suffix',
    ),
    pytest.param(
        'BS.AVAILABLE_VS_LEDGER', "bank_statement", [_field("ledger_balance", "1000"), _field("available_balance", "800"), _field("holds", "200")],
        id='available_vs_ledger_reconciles',
    ),
    pytest.param(
        'GEN.AMOUNT_PARSE', "notice_of_assessment", [_field("tax_reference_number", "202034564S")],
        id='amount_parse_ignores_identifier_fields_that_mention_tax',
    ),
    pytest.param(
        'POA.USAGE_MATCH', "proof_of_address", [_field("usage_this_period", "500"),
             _field("consumption_trend", [
                 {"month": "Mar", "usage": 500}, {"month": "Feb", "usage": 420}, {"month": "Jan", "usage": 380},
             ])],
        id='poa_usage_match_accepts_a_newest_first_trend',
    ),
    pytest.param(
        'POA.USAGE_MATCH', "proof_of_address", [_field("usage_this_period", "500"),
             _field("consumption_trend", [
                 {"month": "Jan", "usage": 380}, {"month": "Feb", "usage": 420}, {"month": "Mar", "usage": 500},
             ])],
        id='poa_usage_match_accepts_an_oldest_first_trend',
    ),
]


@pytest.mark.parametrize("check_code, document_type, fields", _NOT_FLAGGED)
def test_rule_does_not_flag(check_code, document_type, fields) -> None:
    result = _run(fields, document_type=document_type)
    assert _codes(result, check_code) == []
