from __future__ import annotations

from app.fraud_detection.font_consistency import ExtractedField
from app.fraud_detection.date_anomalies import check_date_anomalies


def _field(name: str, value, confidence: float = 0.95) -> ExtractedField:
    return ExtractedField(field_name=name, field_type="string", field_value=value, confidence=confidence)


def _run(fields: list[ExtractedField], document_type: str | None = None):
    return check_date_anomalies("doc-1", fields, document_type)


def _codes(result, code: str) -> list:
    return [f for f in result.findings if f.check_code == code]


def _valid_nric(prefix: str, digits: str) -> str:
    from app.fraud_detection.numerical_validation import _nric_expected_letter
    return f"{prefix}{digits}{_nric_expected_letter(prefix, digits)}"


# ---------------------------------------------------------------------------
# AC1/AC2/AC5 — labelled date extraction with page/text-location
# ---------------------------------------------------------------------------


def test_extracts_and_labels_a_known_date_field() -> None:
    result = _run([_field("incorporation_date", "2020-01-15")])
    assert len(result.dates_extracted) == 1
    extracted = result.dates_extracted[0]
    assert extracted.field == "incorporation_date"
    assert extracted.label == "incorporation date"
    assert extracted.value == "2020-01-15"
    assert extracted.raw_value == "2020-01-15"


def test_extracts_row_level_transaction_dates_with_their_own_label() -> None:
    transactions = [{"date": "2026-01-05", "amount": 10.0}]
    result = _run([_field("transactions", transactions)], document_type="bank_statement")
    row_dates = [d for d in result.dates_extracted if d.field == "transactions[0].date"]
    assert len(row_dates) == 1
    assert row_dates[0].label == "transaction date"


def test_unmapped_date_field_falls_back_to_a_humanized_label() -> None:
    result = _run([_field("some_custom_date", "2026-01-05")])
    matches = [d for d in result.dates_extracted if d.field == "some_custom_date"]
    assert len(matches) == 1
    assert matches[0].label == "some custom date"




# ---------------------------------------------------------------------------
# AC7 — Unable to Assess
# ---------------------------------------------------------------------------


def test_unable_to_assess_when_no_dates_extracted() -> None:
    result = _run([_field("net_pay", "5000")])
    assert result.unable_to_assess is True
    assert result.dates_extracted == []
    assert result.findings == []


# ---------------------------------------------------------------------------
# Skipped checks + needs_review contract (mirrors numerical_validation.py's)
# ---------------------------------------------------------------------------


def test_missing_fields_are_skipped_with_a_reason() -> None:
    result = _run([_field("incorporation_date", "2020-01-01")], document_type="bank_statement")
    skipped_codes = {s.check_code for s in result.skipped_checks}
    assert "BS.PERIOD_BOUNDS" in skipped_codes


def test_low_confidence_field_downgrades_its_finding_to_needs_review() -> None:
    result = _run(
        [_field("financial_year_end", "2026-12-31", confidence=0.99),
         _field("signing_date", "2026-06-01", confidence=0.1)],
        document_type="source_of_wealth",
    )
    findings = _codes(result, "FS.DATE_LOGIC")
    assert len(findings) == 1
    assert findings[0].needs_review is True


# ---------------------------------------------------------------------------
# GEN.* / ID.* — universal date checks
# ---------------------------------------------------------------------------


def test_date_valid_flags_future_date() -> None:
    result = _run([_field("declaration_date", "2099-01-01")])
    assert len(_codes(result, "GEN.DATE_VALID")) == 1


def test_date_valid_flags_unparseable_date() -> None:
    result = _run([_field("declaration_date", "not a date")])
    assert len(_codes(result, "GEN.DATE_VALID")) == 1


def test_date_valid_parses_a_date_with_a_trailing_timestamp() -> None:
    # A "printed on"/"generated at" style value ("05 Sep 2026 08:12:17")
    # is still just a date for GEN.DATE_VALID's purposes — the time
    # portion shouldn't make it unparseable.
    result = _run([_field("printed_on", "05 Sep 2026 08:12:17")])
    assert _codes(result, "GEN.DATE_VALID") == []


def test_date_valid_parses_dash_separated_day_first_dates() -> None:
    # dd-mm-yyyy (dashes) is the same shape as dd/mm/yyyy (slashes) — both
    # show up depending on the document/locale.
    result = _run([_field("date_of_issue", "20-12-2025")])
    assert _codes(result, "GEN.DATE_VALID") == []


def test_date_valid_does_not_flag_forward_looking_fields_in_the_future() -> None:
    # A loan's review date, a bill's due date, a document's expiry — these
    # are supposed to be after today; only their own dedicated checks (or
    # none at all) should judge them, not the generic "not in the future"
    # rule.
    result = _run(
        [_field("review_date", "2099-01-01"), _field("due_date", "2099-01-01"),
         _field("date_of_expiry", "2099-01-01"), _field("contract_end_date", "2099-01-01")],
    )
    assert _codes(result, "GEN.DATE_VALID") == []


def test_date_valid_still_flags_unparseable_forward_looking_field() -> None:
    result = _run([_field("review_date", "not a date")])
    assert len(_codes(result, "GEN.DATE_VALID")) == 1


def test_nric_birthyear_must_match_prefix_digits_for_1968_onward() -> None:
    nric = _valid_nric("S", "9012345")  # implies birth year ..90
    result = _run([_field("nric_number", nric), _field("date_of_birth", "1985-05-01")])
    assert len(_codes(result, "ID.NRIC_BIRTHYEAR")) == 1


def test_nric_prefix_vs_birth_flags_t_prefix_before_2000() -> None:
    nric = _valid_nric("T", "9012345")
    result = _run([_field("nric_number", nric), _field("date_of_birth", "1990-01-01")])
    assert len(_codes(result, "ID.PREFIX_VS_BIRTH")) == 1


# ---------------------------------------------------------------------------
# Bank statement
# ---------------------------------------------------------------------------


def test_period_bounds_flags_transaction_outside_statement_period() -> None:
    transactions = [{"date": "2026-01-15", "amount": 1.0, "balance": 1.0}]
    result = _run(
        [
            _field("transactions", transactions),
            _field("statement_period_start", "2026-02-01"),
            _field("statement_period_end", "2026-02-28"),
        ],
        document_type="bank_statement",
    )
    assert len(_codes(result, "BS.PERIOD_BOUNDS")) == 1


# ---------------------------------------------------------------------------
# Source of wealth (financial statements)
# ---------------------------------------------------------------------------


def test_fs_date_logic_flags_signing_before_year_end() -> None:
    result = _run(
        [_field("financial_year_end", "2026-12-31"), _field("signing_date", "2026-06-01")],
        document_type="source_of_wealth",
    )
    assert len(_codes(result, "FS.DATE_LOGIC")) == 1


# ---------------------------------------------------------------------------
# Notice of assessment
# ---------------------------------------------------------------------------


def test_noa_date_logic_flags_notice_before_its_own_year() -> None:
    result = _run(
        [_field("noa_date", "2020-01-01"), _field("year_of_assessment", "2026")],
        document_type="notice_of_assessment",
    )
    assert len(_codes(result, "NOA.DATE_LOGIC")) == 1


# ---------------------------------------------------------------------------
# Payslip
# ---------------------------------------------------------------------------


def test_payslip_date_logic_flags_payment_far_after_pay_period() -> None:
    result = _run(
        [_field("pay_period_end", "2026-01-31"), _field("pay_date", "2026-06-01")],
        document_type="payslip",
    )
    assert len(_codes(result, "PAY.DATE_LOGIC")) == 1


# ---------------------------------------------------------------------------
# Credit report
# ---------------------------------------------------------------------------


def test_cr_earliest_account_flags_mismatch() -> None:
    result = _run(
        [_field("accounts", [{"date_opened": "2015-01-01"}, {"date_opened": "2018-01-01"}]),
         _field("earliest_account_date", "2016-01-01")],
        document_type="credit_report",
    )
    assert len(_codes(result, "CR.EARLIEST_ACCOUNT")) == 1


def test_cr_age_at_opening_flags_underage_card() -> None:
    result = _run(
        [_field("accounts", [{"product_type": "Credit Card", "date_opened": "2020-01-01"}]),
         _field("date_of_birth", "2005-01-01")],
        document_type="credit_report",
    )
    assert len(_codes(result, "CR.AGE_AT_OPENING")) == 1


def test_cr_date_logic_flags_closing_before_opening() -> None:
    result = _run(
        [_field("accounts", [{"date_opened": "2020-01-01", "date_closed": "2019-01-01"}])],
        document_type="credit_report",
    )
    assert len(_codes(result, "CR.DATE_LOGIC")) == 1


def test_cr_date_logic_flags_account_opened_in_the_future_as_impossible() -> None:
    # A date genuinely in the future (vs today) is impossible regardless of
    # what the report_date field says — and this must still fire even
    # without report_date extracted at all.
    result = _run(
        [_field("accounts", [{"date_opened": "2099-01-01"}])],
        document_type="credit_report",
    )
    findings = _codes(result, "CR.DATE_LOGIC")
    assert len(findings) == 1
    assert findings[0].severity == "impossible"


def test_cr_date_logic_does_not_double_flag_a_future_date_against_report_date() -> None:
    # The account date is beyond both today and report_date — only the
    # stronger "impossible" finding should fire, not a second
    # "inconsistent vs report_date" one for the same fact.
    result = _run(
        [_field("report_date", "2026-06-02"), _field("accounts", [{"date_opened": "2030-11-07"}])],
        document_type="credit_report",
    )
    findings = _codes(result, "CR.DATE_LOGIC")
    assert len(findings) == 1
    assert findings[0].severity == "impossible"


# ---------------------------------------------------------------------------
# List of loans
# ---------------------------------------------------------------------------


def test_loan_date_logic_flags_acceptance_before_offer() -> None:
    result = _run(
        [_field("offer_date", "2026-02-01"), _field("acceptance_date", "2026-01-01")],
        document_type="list_of_loans",
    )
    assert len(_codes(result, "LOAN.DATE_LOGIC")) == 1


# ---------------------------------------------------------------------------
# Proof of address
# ---------------------------------------------------------------------------


def test_poa_period_logic_flags_period_ending_after_bill_date() -> None:
    result = _run(
        [_field("billing_period_end", "2026-03-01"), _field("bill_date", "2026-02-01")],
        document_type="proof_of_address",
    )
    assert len(_codes(result, "POA.PERIOD_LOGIC")) == 1


def test_poa_due_date_flags_due_date_far_beyond_normal_window() -> None:
    result = _run(
        [_field("bill_date", "2026-01-01"), _field("due_date", "2027-01-01")],
        document_type="proof_of_address",
    )
    assert len(_codes(result, "POA.DUE_DATE")) == 1


def test_poa_contract_term_flags_unusually_long_contract() -> None:
    result = _run(
        [_field("bill_date", "2026-01-01"), _field("contract_end_date", "2036-01-01")],
        document_type="proof_of_address",
    )
    assert len(_codes(result, "POA.CONTRACT_TERM")) == 1


# ---------------------------------------------------------------------------
# Certificate of incorporation
# ---------------------------------------------------------------------------


def test_coi_uen_year_flags_mismatch() -> None:
    result = _run(
        [_field("uen", "202312345K"), _field("incorporation_date", "2020-01-01")],
        document_type="certificate_of_incorporation",
    )
    assert len(_codes(result, "COI.UEN_YEAR")) == 1


def test_coi_date_logic_flags_name_change_before_incorporation() -> None:
    result = _run(
        [_field("incorporation_date", "2020-01-01"),
         _field("company_former_name_change_date", "2019-01-01")],
        document_type="certificate_of_incorporation",
    )
    assert len(_codes(result, "COI.DATE_LOGIC")) == 1


def test_coi_event_after_issue_flags_name_change_after_certificate_date() -> None:
    result = _run(
        [_field("certificate_date", "2020-01-01"),
         _field("company_former_name_change_date", "2021-01-01")],
        document_type="certificate_of_incorporation",
    )
    assert len(_codes(result, "COI.EVENT_AFTER_ISSUE")) == 1


# ---------------------------------------------------------------------------
# Certificate of incumbency (ACRA)
# ---------------------------------------------------------------------------


def test_acra_date_logic_flags_officer_appointed_before_incorporation() -> None:
    result = _run(
        [_field("incorporation_date", "2020-01-01"),
         _field("officers", [{"role": "Director", "date_appointed": "2019-01-01"}])],
        document_type="certificate_of_incumbency",
    )
    assert len(_codes(result, "ACRA.DATE_LOGIC")) == 1


def test_acra_filing_vs_fye_flags_agm_too_late() -> None:
    result = _run(
        [_field("financial_year_end", "2025-12-31"), _field("last_agm_date", "2026-12-01")],
        document_type="certificate_of_incumbency",
    )
    assert len(_codes(result, "ACRA.FILING_VS_FYE")) == 1


def test_acra_secretary_timing_flags_late_appointment() -> None:
    result = _run(
        [_field("incorporation_date", "2020-01-01"),
         _field("officers", [{"role": "Secretary", "date_appointed": "2020-12-01"}])],
        document_type="certificate_of_incumbency",
    )
    assert len(_codes(result, "ACRA.SECRETARY_TIMING")) == 1


# ---------------------------------------------------------------------------
# Company constitution
# ---------------------------------------------------------------------------


def test_const_date_logic_flags_adoption_before_incorporation() -> None:
    result = _run(
        [_field("incorporation_date", "2020-01-01"), _field("adoption_date", "2019-01-01")],
        document_type="company_constitution",
    )
    assert len(_codes(result, "CONST.DATE_LOGIC")) == 1


# ---------------------------------------------------------------------------
# NRIC / passport (document-specific date logic)
# ---------------------------------------------------------------------------


def test_nric_date_logic_flags_issue_before_birth() -> None:
    result = _run(
        [_field("date_of_issue", "1990-01-01"), _field("date_of_birth", "2000-01-01")],
        document_type="nric",
    )
    assert len(_codes(result, "ID.DATE_LOGIC")) == 1


def test_passport_validity_period_flags_wrong_length() -> None:
    result = _run(
        [_field("date_of_issue", "2020-01-01"), _field("date_of_expiry", "2025-01-01")],
        document_type="passport",
    )
    assert len(_codes(result, "ID.VALIDITY_PERIOD")) == 1


def test_passport_expired_is_a_signal() -> None:
    result = _run([_field("date_of_expiry", "2000-01-01")], document_type="passport")
    findings = _codes(result, "ID.EXPIRED")
    assert len(findings) == 1
    assert findings[0].severity == "signal"


# ---------------------------------------------------------------------------
# FATCA/CRS declaration
# ---------------------------------------------------------------------------


def test_fatca_date_logic_flags_future_declaration() -> None:
    result = _run(
        [_field("declaration_date", "2099-01-01")], document_type="fatca_crs_declaration",
    )
    assert len(_codes(result, "FATCA.DATE_LOGIC")) == 1
