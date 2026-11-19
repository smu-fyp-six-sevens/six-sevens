from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app import sg_time
from app.fraud_detection.font_consistency import ExtractedField
from app.fraud_detection.dates.date_anomalies import check_date_anomalies


def _field(name: str, value, confidence: float = 0.95) -> ExtractedField:
    return ExtractedField(field_name=name, field_type="string", field_value=value, confidence=confidence)


def _run(fields: list[ExtractedField], document_type: str | None = None):
    return check_date_anomalies("doc-1", fields, document_type)


def _codes(result, code: str) -> list:
    return [f for f in result.findings if f.check_code == code]


def _valid_nric(prefix: str, digits: str) -> str:
    from app.identifiers import nric_check_letter
    return f"{prefix}{digits}{nric_check_letter(prefix, digits)}"


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
# One finding per rule: each row builds a document that breaks exactly one
# date rule and expects exactly one finding with that check code.
# ---------------------------------------------------------------------------

_FLAGGED_ONCE = [
    # GEN.* / ID.* — universal date checks
    pytest.param(
        "GEN.DATE_VALID", None, [("declaration_date", "2099-01-01")],
        id="date_valid_flags_future_date",
    ),
    pytest.param(
        "GEN.DATE_VALID", None, [("declaration_date", "not a date")],
        id="date_valid_flags_unparseable_date",
    ),
    pytest.param(
        "GEN.DATE_VALID", None, [("review_date", "not a date")],
        id="date_valid_still_flags_unparseable_forward_looking_field",
    ),
    pytest.param(
        "ID.NRIC_BIRTHYEAR", None,
        # S9012345.. implies birth year ..90 — 1985 contradicts it (1968 onward).
        [("nric_number", _valid_nric("S", "9012345")), ("date_of_birth", "1985-05-01")],
        id="nric_birthyear_must_match_prefix_digits_for_1968_onward",
    ),
    pytest.param(
        "ID.PREFIX_VS_BIRTH", None,
        [("nric_number", _valid_nric("T", "9012345")), ("date_of_birth", "1990-01-01")],
        id="nric_prefix_vs_birth_flags_t_prefix_before_2000",
    ),
    # Bank statement
    pytest.param(
        "BS.PERIOD_BOUNDS", "bank_statement",
        [
            ("transactions", [{"date": "2026-01-15", "amount": 1.0, "balance": 1.0}]),
            ("statement_period_start", "2026-02-01"),
            ("statement_period_end", "2026-02-28"),
        ],
        id="period_bounds_flags_transaction_outside_statement_period",
    ),
    # Source of wealth (financial statements)
    pytest.param(
        "FS.DATE_LOGIC", "source_of_wealth",
        [("financial_year_end", "2026-12-31"), ("signing_date", "2026-06-01")],
        id="fs_date_logic_flags_signing_before_year_end",
    ),
    # Notice of assessment
    pytest.param(
        "NOA.DATE_LOGIC", "notice_of_assessment",
        [("noa_date", "2020-01-01"), ("year_of_assessment", "2026")],
        id="noa_date_logic_flags_notice_before_its_own_year",
    ),
    # Payslip
    pytest.param(
        "PAY.DATE_LOGIC", "payslip",
        [("pay_period_end", "2026-01-31"), ("pay_date", "2026-06-01")],
        id="payslip_date_logic_flags_payment_far_after_pay_period",
    ),
    # Credit report
    pytest.param(
        "CR.EARLIEST_ACCOUNT", "credit_report",
        [
            ("accounts", [{"date_opened": "2015-01-01"}, {"date_opened": "2018-01-01"}]),
            ("earliest_account_date", "2016-01-01"),
        ],
        id="cr_earliest_account_flags_mismatch",
    ),
    pytest.param(
        "CR.AGE_AT_OPENING", "credit_report",
        [
            ("accounts", [{"product_type": "Credit Card", "date_opened": "2020-01-01"}]),
            ("date_of_birth", "2005-01-01"),
        ],
        id="cr_age_at_opening_flags_underage_card",
    ),
    pytest.param(
        "CR.AGE_AT_OPENING", "credit_report",
        [
            ("accounts", [{"product_type": "Credit Card", "date_opened": "2007-01-22"}]),
            ("date_of_birth", "1986-01-23"),
        ],
        id="cr_age_at_opening_flags_a_card_the_day_before_21",
    ),
    pytest.param(
        "CR.DATE_LOGIC", "credit_report",
        [("accounts", [{"date_opened": "2020-01-01", "date_closed": "2019-01-01"}])],
        id="cr_date_logic_flags_closing_before_opening",
    ),
    # List of loans
    pytest.param(
        "LOAN.DATE_LOGIC", "list_of_loans",
        [("offer_date", "2026-02-01"), ("acceptance_date", "2026-01-01")],
        id="loan_date_logic_flags_acceptance_before_offer",
    ),
    # Proof of address
    pytest.param(
        "POA.PERIOD_LOGIC", "proof_of_address",
        [("billing_period_end", "2026-03-01"), ("bill_date", "2026-02-01")],
        id="poa_period_logic_flags_period_ending_after_bill_date",
    ),
    pytest.param(
        "POA.DUE_DATE", "proof_of_address",
        [("bill_date", "2026-01-01"), ("due_date", "2027-01-01")],
        id="poa_due_date_flags_due_date_far_beyond_normal_window",
    ),
    pytest.param(
        "POA.CONTRACT_TERM", "proof_of_address",
        [("bill_date", "2026-01-01"), ("contract_end_date", "2036-01-01")],
        id="poa_contract_term_flags_unusually_long_contract",
    ),
    # Certificate of incorporation
    pytest.param(
        "COI.UEN_YEAR", "certificate_of_incorporation",
        [("uen", "202312345K"), ("incorporation_date", "2020-01-01")],
        id="coi_uen_year_flags_mismatch",
    ),
    pytest.param(
        "COI.DATE_LOGIC", "certificate_of_incorporation",
        [("incorporation_date", "2020-01-01"), ("company_former_name_change_date", "2019-01-01")],
        id="coi_date_logic_flags_name_change_before_incorporation",
    ),
    pytest.param(
        "COI.EVENT_AFTER_ISSUE", "certificate_of_incorporation",
        [("certificate_date", "2020-01-01"), ("company_former_name_change_date", "2021-01-01")],
        id="coi_event_after_issue_flags_name_change_after_certificate_date",
    ),
    # Certificate of incumbency (ACRA)
    pytest.param(
        "ACRA.DATE_LOGIC", "certificate_of_incumbency",
        [
            ("incorporation_date", "2020-01-01"),
            ("officers", [{"role": "Director", "date_appointed": "2019-01-01"}]),
        ],
        id="acra_date_logic_flags_officer_appointed_before_incorporation",
    ),
    pytest.param(
        "ACRA.FILING_VS_FYE", "certificate_of_incumbency",
        [("financial_year_end", "2025-12-31"), ("last_agm_date", "2026-12-01")],
        id="acra_filing_vs_fye_flags_agm_too_late",
    ),
    pytest.param(
        "ACRA.SECRETARY_TIMING", "certificate_of_incumbency",
        [
            ("incorporation_date", "2020-01-01"),
            ("officers", [{"role": "Secretary", "date_appointed": "2020-12-01"}]),
        ],
        id="acra_secretary_timing_flags_late_appointment",
    ),
    # Company constitution
    pytest.param(
        "CONST.DATE_LOGIC", "company_constitution",
        [("incorporation_date", "2020-01-01"), ("adoption_date", "2019-01-01")],
        id="const_date_logic_flags_adoption_before_incorporation",
    ),
    # NRIC / passport (document-specific date logic)
    pytest.param(
        "ID.DATE_LOGIC", "nric",
        [("date_of_issue", "1990-01-01"), ("date_of_birth", "2000-01-01")],
        id="nric_date_logic_flags_issue_before_birth",
    ),
    pytest.param(
        "ID.VALIDITY_PERIOD", "passport",
        [("date_of_issue", "2020-01-01"), ("date_of_expiry", "2025-01-01")],
        id="passport_validity_period_flags_wrong_length",
    ),
    # FATCA/CRS declaration
    pytest.param(
        "FATCA.DATE_LOGIC", "fatca_crs_declaration",
        [("declaration_date", "2099-01-01")],
        id="fatca_date_logic_flags_future_declaration",
    ),
]


@pytest.mark.parametrize("check_code, document_type, fields", _FLAGGED_ONCE)
def test_rule_flags_exactly_one_finding(check_code, document_type, fields) -> None:
    result = _run([_field(name, value) for name, value in fields], document_type=document_type)
    assert len(_codes(result, check_code)) == 1


# ---------------------------------------------------------------------------
# GEN.DATE_VALID — values that must NOT be flagged
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Rules with extra assertions beyond "one finding"
# ---------------------------------------------------------------------------


def test_cr_date_logic_flags_account_opened_in_the_future_as_impossible() -> None:
    # A date genuinely in the future (vs today) is impossible regardless of
    # what the enquiry_date field says — and this must still fire even
    # without enquiry_date extracted at all.
    result = _run(
        [_field("accounts", [{"date_opened": "2099-01-01"}])],
        document_type="credit_report",
    )
    findings = _codes(result, "CR.DATE_LOGIC")
    assert len(findings) == 1
    assert findings[0].severity == "impossible"


def test_cr_date_logic_does_not_double_flag_a_future_date_against_enquiry_date() -> None:
    # The account date is beyond both today and enquiry_date — only the
    # stronger "impossible" finding should fire, not a second
    # "inconsistent vs enquiry_date" one for the same fact.
    result = _run(
        [_field("enquiry_date", "2026-06-02"), _field("accounts", [{"date_opened": "2030-11-07"}])],
        document_type="credit_report",
    )
    findings = _codes(result, "CR.DATE_LOGIC")
    assert len(findings) == 1
    assert findings[0].severity == "impossible"


def test_cr_date_logic_flags_an_account_dated_after_the_enquiry_date() -> None:
    # Reads the report's "Enquiry Date" (extracted as enquiry_date): an account can't be opened after the report
    # was pulled. Before today, so it's this check, not the future-date one, that catches it.
    result = _run(
        [_field("enquiry_date", "06/08/2026"), _field("accounts", [{"date_opened": "2026-09-15"}])],
        document_type="credit_report",
    )
    findings = _codes(result, "CR.DATE_LOGIC")
    assert len(findings) == 1
    assert findings[0].severity == "inconsistent"
    assert "enquiry date (2026-08-06)" in findings[0].summary


def test_acra_officer_date_checks_read_the_appointment_date_whatever_its_column_is_called() -> None:
    # "Date of Appointment" transcribed as date_of_appointment, role as position_held.
    result = _run(
        [_field("incorporation_date", "2020-01-01"),
         _field("officers", [
             {"position_held": "Director", "date_of_appointment": "2019-01-01"},
             {"position_held": "Secretary", "date_of_appointment": "2020-12-01"},
         ])],
        document_type="certificate_of_incumbency",
    )
    assert len(_codes(result, "ACRA.DATE_LOGIC")) == 1
    assert len(_codes(result, "ACRA.SECRETARY_TIMING")) == 1


def test_passport_expired_is_a_signal() -> None:
    result = _run([_field("date_of_expiry", "2000-01-01")], document_type="passport")
    findings = _codes(result, "ID.EXPIRED")
    assert len(findings) == 1
    assert findings[0].severity == "signal"


# ---------------------------------------------------------------------------
# Dates printed without a year (statement rows, a financial year end)
# ---------------------------------------------------------------------------


def test_a_date_printed_without_a_year_is_not_an_invalid_date() -> None:
    for value in ("23rd aug", "31/03", "September 2026"):
        result = _run([_field("document_date", value)])
        assert _codes(result, "GEN.DATE_VALID") == [], value
    # Text that isn't a date at all still is.
    assert len(_codes(_run([_field("document_date", "Bankruptcy Order")]), "GEN.DATE_VALID")) == 1


def _statement(rows: list[str]):
    transactions = [{"date": d, "amount": 10.0} for d in rows]
    return _run([
        _field("transactions", transactions),
        _field("statement_period_start", "15 Dec 2025"),
        _field("statement_period_end", "14 Jan 2026"),
    ], document_type="bank_statement")


def test_year_less_statement_rows_take_their_year_from_the_period() -> None:
    result = _statement(["16 DEC", "28 DEC", "03 JAN", "14 JAN"])
    assert _codes(result, "BS.PERIOD_BOUNDS") == []
    assert [d.value for d in result.dates_extracted if d.field.startswith("transactions")] == [
        "2025-12-16", "2025-12-28", "2026-01-03", "2026-01-14",
    ]


def test_a_year_less_statement_row_outside_the_period_is_still_caught() -> None:
    [finding] = _codes(_statement(["16 DEC", "20 FEB"]), "BS.PERIOD_BOUNDS")
    assert "Transaction 2" in finding.summary and "2026-02-20" in finding.summary


def test_two_digit_year_statement_rows_are_checked() -> None:
    result = _statement(["16-Dec-25", "20-Feb-26"])
    [finding] = _codes(result, "BS.PERIOD_BOUNDS")
    assert "2026-02-20" in finding.summary


def test_a_document_dated_today_is_not_future_dated_before_8am_singapore_time(monkeypatch) -> None:
    # 01:00 in Singapore is still yesterday in UTC, where Cloud Run's clock runs.
    monkeypatch.setattr(sg_time, "now_sg", lambda: datetime(2026, 11, 13, 1, 0, tzinfo=sg_time.SINGAPORE))
    assert _codes(_run([_field("declaration_date", "2026-11-13")]), "GEN.DATE_VALID") == []
    assert len(_codes(_run([_field("declaration_date", "2026-11-14")]), "GEN.DATE_VALID")) == 1


def test_a_card_opened_on_the_21st_birthday_is_not_underage() -> None:
    # Seen live: days / 365.25 put a card opened on the holder's 21st birthday at "age 21.0, below the minimum of 21".
    result = _run([
        _field("accounts", [{"product_type": "Unsecured Credit Card", "date_opened": "2007-01-23"}]),
        _field("date_of_birth", "1986-01-23"),
    ], document_type="credit_report")
    assert _codes(result, "CR.AGE_AT_OPENING") == []


def test_a_date_after_the_upload_is_caught_even_once_it_has_passed(monkeypatch) -> None:
    # Signed 23 Nov, uploaded 18 Nov: impossible, and still so when the detection re-runs on 1 Dec.
    monkeypatch.setattr(sg_time, "now_sg", lambda: datetime(2026, 12, 1, 9, 0, tzinfo=sg_time.SINGAPORE))
    uploaded = datetime(2026, 11, 18, 2, 0, tzinfo=timezone.utc)
    result = check_date_anomalies("doc-1", [_field("signing_date", "2026-11-23")], None, uploaded)
    [finding] = _codes(result, "GEN.DATE_VALID")
    assert "after the upload date (2026-11-18)" in finding.summary
    assert "can't be dated after it was uploaded" in finding.summary
    # Without the upload time it falls back to today, by which the date has passed.
    assert _codes(_run([_field("signing_date", "2026-11-23")]), "GEN.DATE_VALID") == []


def test_the_upload_day_is_singapores() -> None:
    # 20:00 UTC on 17 Nov is 04:00 on 18 Nov in Singapore: a document dated the 18th was not uploaded before it.
    uploaded = datetime(2026, 11, 17, 20, 0, tzinfo=timezone.utc)
    result = check_date_anomalies("doc-1", [_field("declaration_date", "2026-11-18")], None, uploaded)
    assert _codes(result, "GEN.DATE_VALID") == []


def test_a_passport_expired_before_it_was_uploaded_says_so() -> None:
    uploaded = datetime(2026, 11, 18, 2, 0, tzinfo=timezone.utc)
    result = check_date_anomalies(
        "doc-1",
        [_field("date_of_issue", "2016-11-01"), _field("date_of_expiry", "2026-11-01"), _field("date_of_birth", "1990-01-01")],
        "passport",
        uploaded,
    )
    [finding] = _codes(result, "ID.EXPIRED")
    assert finding.summary == "Passport expired on 2026-11-01, before it was uploaded (2026-11-18)"
