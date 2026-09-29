from __future__ import annotations

import random
from unittest.mock import patch

from app.fraud_detection.font_consistency import ExtractedField
from app.fraud_detection.numerical_validation import (
    BENFORD_MIN_SAMPLE,
    NumericalValue,
    _benford,
    _check_balance_sheet_identity,
    _check_holdings,
    _check_running_balance,
    _check_share_allocation,
    _extract_dates,
    _parse_values,
    check_numerical_validation,
)


def _pages(*texts: str) -> list[dict]:
    return [{"page_number": 1, "elements": [{"text": t} for t in texts]}]


def _values(numbers: list[float], kind: str = "currency") -> list[NumericalValue]:
    return [
        NumericalValue(raw=str(n), value=n, kind=kind, page=1, section=0, location="x")
        for n in numbers
    ]


def _run(pages: list[dict], fields: list[ExtractedField] | None = None):
    with patch("app.fraud_detection.numerical_validation.ingest_document") as ingest:
        ingest.return_value = {"raw_text": "x", "pages": pages}
        return check_numerical_validation("doc-1", b"bytes", "doc.pdf", fields)


# AC1 — format validation and classification -----------------------------


def test_classifies_currency_percentage_date_and_quantity() -> None:
    values = _parse_values(
        _pages("Opening balance $12,345.67 on 01-Jun-2026", "Rate 4.5% over 12 units")
    )
    by_kind = {v.kind for v in values}
    assert by_kind == {"currency", "percentage", "date", "quantity"}

    currency = next(v for v in values if v.kind == "currency")
    assert currency.value == 12345.67
    percentage = next(v for v in values if v.kind == "percentage")
    assert percentage.value == 4.5


def test_flags_impossible_calendar_date() -> None:
    result = _run(_pages("Statement dated 2026-02-30"))
    assert len(result.findings) == 1
    assert result.findings[0].finding_type == "format"
    assert "not a valid calendar date" in result.findings[0].summary


def test_accepts_both_day_first_and_month_first_dates() -> None:
    # "12/25/2026" is nonsense read day-first but a normal US date, and
    # nothing in a document says which convention it used — so both
    # readings are accepted and only a date that works under neither
    # (e.g. 32/25) is reported.
    for text in ("Dated 25/12/2026", "Dated 12/25/2026"):
        assert _extract_dates(text)[0] == [(text.split()[-1], True)]

    assert _extract_dates("Dated 32/25/2026")[0] == [("32/25/2026", False)]


def test_does_not_flag_unfamiliar_digit_grouping() -> None:
    # A European decimal comma ("2,5") and East-Asian 4-digit grouping
    # ("1,2345") are well-formed, not malformed — an earlier version of
    # this check flagged both.
    result = _run(_pages("Paid 2,5 EUR and 1,2345 JPY"))
    assert [f for f in result.findings if f.finding_type == "format"] == []


def test_parses_accounting_negatives_and_currency_words() -> None:
    values = _parse_values(_pages("Debit (1,200.00) and SGD 3,400.50 credit"))
    amounts = sorted(v.value for v in values if v.kind == "currency")
    assert amounts == [-1200.00, 3400.50]


# AC2/AC3/AC4 — arithmetic reconciliation --------------------------------


def test_flags_running_balance_that_does_not_reconcile() -> None:
    # The check that catches an edited transaction: change one amount and
    # every balance below it stops adding up.
    transactions = [
        {"description": "Opening", "amount": 0, "balance": 10000.00},
        {"description": "Salary", "amount": 5000.00, "balance": 15000.00},
        {"description": "Rent", "amount": -2000.00, "balance": 14500.00},
    ]
    findings = _check_running_balance({"transactions": transactions})
    assert len(findings) == 1
    assert findings[0].severity == "high"
    # AC4: both the stated and the calculated value are recorded.
    assert findings[0].stated == "14,500.00"
    assert findings[0].expected == "13,000.00"
    assert findings[0].field == "transactions[2].balance"


def test_running_balance_that_reconciles_is_clean() -> None:
    transactions = [
        {"amount": 0, "balance": 10000.00},
        {"amount": 5000.00, "balance": 15000.00},
        {"amount": -2000.00, "balance": 13000.00},
    ]
    assert _check_running_balance({"transactions": transactions}) == []


def test_running_balance_tolerates_a_rounding_cent() -> None:
    transactions = [
        {"amount": 0, "balance": 100.00},
        {"amount": 0.10, "balance": 100.11},
    ]
    assert _check_running_balance({"transactions": transactions}) == []


def test_running_balance_skips_rows_with_no_balance_shown() -> None:
    # EXTRA_FIELD_GUIDANCE allows balance to be null when the statement
    # doesn't print a running total on that line.
    transactions = [
        {"amount": 0, "balance": 500.00},
        {"amount": 100.00, "balance": None},
        {"amount": 50.00, "balance": 650.00},
    ]
    assert _check_running_balance({"transactions": transactions}) == []


def test_running_balance_carries_forward_through_a_line_with_no_balance() -> None:
    # Regression: the unprinted line's own amount was being dropped, so the
    # next line was compared against a stale total and wrongly flagged.
    transactions = [
        {"amount": 0, "balance": 500.00},
        {"amount": 100.00, "balance": None},
        {"amount": 50.00, "balance": 650.00},
    ]
    assert _check_running_balance({"transactions": transactions}) == []


def test_one_edited_transaction_produces_one_finding_not_a_cascade() -> None:
    # After reporting a break, the check resyncs to the stated figure —
    # otherwise a single tampered amount would flag every line beneath it
    # and bury the actual point of failure.
    transactions = [
        {"amount": 0, "balance": 1000.00},
        {"amount": 100.00, "balance": 9999.00},
        {"amount": 50.00, "balance": 10049.00},
        {"amount": 25.00, "balance": 10074.00},
    ]
    findings = _check_running_balance({"transactions": transactions})
    assert len(findings) == 1
    assert findings[0].field == "transactions[1].balance"


def test_running_balance_drops_the_thread_on_an_unreadable_amount() -> None:
    transactions = [
        {"amount": 0, "balance": 500.00},
        {"amount": "n/a", "balance": None},
        {"amount": 50.00, "balance": 9999.00},
    ]
    # The middle row leaves no usable running total, so the row after it
    # has nothing trustworthy to be compared against.
    assert _check_running_balance({"transactions": transactions}) == []


def test_arithmetic_ignores_non_list_and_non_dict_field_shapes() -> None:
    assert _check_running_balance({"transactions": "not a list"}) == []
    assert _check_holdings({"holdings": ["not a row", 42]}) == []
    assert _check_share_allocation({"issued_shares": 10, "subscribers": []}) == []


def test_arithmetic_ignores_unparseable_and_boolean_values() -> None:
    assert _check_balance_sheet_identity(
        {"total_assets": True, "total_liabilities": 1, "net_asset_value": 5}
    ) == []
    assert _check_balance_sheet_identity(
        {"total_assets": "not a number", "total_liabilities": 1, "net_asset_value": 5}
    ) == []


def test_arithmetic_reads_a_double_encoded_json_field() -> None:
    # Some rows store a further JSON-encoded string on top of the column's
    # own JSON — the quirk spelling_anomaly documents.
    fields = [
        ExtractedField(
            field_name="transactions",
            field_type="array",
            field_value='[{"amount": 0, "balance": 100.0}, {"amount": 10.0, "balance": 999.0}]',
            confidence=0.9,
        )
    ]
    result = _run(_pages("Statement"), fields)
    assert [f.finding_type for f in result.findings] == ["arithmetic"]


def test_accounting_negative_in_a_string_field_is_read_as_negative() -> None:
    transactions = [
        {"amount": 0, "balance": "1,000.00"},
        {"amount": "(250.00)", "balance": "750.00"},
    ]
    assert _check_running_balance({"transactions": transactions}) == []


def test_flags_holding_whose_market_value_is_not_quantity_times_price() -> None:
    holdings = [
        {"name": "Acme", "quantity": 100, "closing_price": 12.50, "market_value": 1250.00},
        {"name": "Beta", "quantity": 200, "closing_price": 3.00, "market_value": 900.00},
    ]
    findings = _check_holdings({"holdings": holdings})
    assert len(findings) == 1
    assert findings[0].stated == "900.00"
    assert findings[0].expected == "600.00"


def test_flags_net_asset_value_breaking_the_accounting_identity() -> None:
    findings = _check_balance_sheet_identity(
        {"total_assets": 500000, "total_liabilities": 200000, "net_asset_value": 310000}
    )
    assert len(findings) == 1
    assert findings[0].stated == "310,000.00"
    assert findings[0].expected == "300,000.00"


def test_balance_sheet_identity_needs_all_three_figures() -> None:
    assert _check_balance_sheet_identity({"total_assets": 500000}) == []


def test_flags_subscriber_shares_not_summing_to_issued_shares() -> None:
    findings = _check_share_allocation(
        {"issued_shares": 500000, "subscribers": [{"shares": 300000}, {"shares": 150000}]}
    )
    assert len(findings) == 1
    assert findings[0].stated == "500,000"
    assert findings[0].expected == "450,000"


def test_share_allocation_skipped_when_any_holding_is_unknown() -> None:
    # A partial list would always "fail" for an entirely innocent reason.
    assert _check_share_allocation(
        {"issued_shares": 500000, "subscribers": [{"shares": 300000}, {"shares": None}]}
    ) == []


def test_arithmetic_reads_values_given_as_formatted_strings() -> None:
    # Extracted field values aren't always JSON numbers.
    transactions = [
        {"amount": "0", "balance": "$1,000.00"},
        {"amount": "$250.00", "balance": "$1,500.00"},
    ]
    findings = _check_running_balance({"transactions": transactions})
    assert len(findings) == 1
    assert findings[0].expected == "1,250.00"


def test_arithmetic_findings_reach_the_result_via_extracted_fields() -> None:
    fields = [
        ExtractedField(
            field_name="transactions",
            field_type="array",
            field_value=[
                {"amount": 0, "balance": 100.00},
                {"amount": 10.00, "balance": 999.00},
            ],
            confidence=0.9,
        )
    ]
    result = _run(_pages("Statement"), fields)
    assert [f.finding_type for f in result.findings] == ["arithmetic"]


# AC5 — Benford's Law ----------------------------------------------------


def test_benford_reports_all_nine_digits_with_counts_and_both_distributions() -> None:
    random.seed(7)
    natural = [10 ** random.uniform(0, 5) for _ in range(400)]
    analysis, findings = _benford(_values(natural))

    assert analysis.unable_to_assess is False
    assert [row.digit for row in analysis.digits] == list(range(1, 10))
    assert sum(row.count for row in analysis.digits) == analysis.sample_size
    # Benford's own expected curve, independent of the sample.
    assert round(analysis.digits[0].expected_percentage, 1) == 30.1
    assert all(row.observed_percentage >= 0 for row in analysis.digits)
    # Log-uniform magnitudes are the textbook conforming case.
    assert analysis.conforms is True
    assert findings == []


def test_benford_does_not_flag_genuine_data_at_document_scale() -> None:
    """
    The test that drove the switch away from Nigrini's MAD bands. Those
    bands assume audit-scale samples: measured against data that genuinely
    follows Benford, median MAD is 0.032 at n=50 and 0.016 at n=200, both
    past the 0.015 "nonconformity" line — so they flagged most legitimate
    documents. Chi-square keeps the false-positive rate at its nominal 1%.
    """
    flagged = 0
    trials = 60
    for seed in range(trials):
        random.seed(seed)
        _, findings = _benford(_values([10 ** random.uniform(0, 6) for _ in range(150)]))
        flagged += len(findings)

    assert flagged / trials < 0.10, f"{flagged}/{trials} genuine samples flagged"


def test_benford_records_the_statistic_it_judged_on() -> None:
    random.seed(7)
    analysis, _ = _benford(_values([10 ** random.uniform(0, 5) for _ in range(400)]))
    assert analysis.chi_square is not None
    assert analysis.chi_square_critical == 20.090
    # MAD is still reported as a descriptive summary, just not the basis
    # for the verdict.
    assert analysis.mean_absolute_deviation is not None


def test_benford_unable_to_assess_below_minimum_sample() -> None:
    analysis, findings = _benford(_values([123.0, 456.0]))
    assert analysis.unable_to_assess is True
    assert str(BENFORD_MIN_SAMPLE) in analysis.unable_to_assess_reason
    # AC5 still wants the full 1-9 table shape back.
    assert [row.digit for row in analysis.digits] == list(range(1, 10))
    assert findings == []


def test_benford_flags_a_fabricated_distribution() -> None:
    random.seed(7)
    fabricated = [float(random.choice([5000, 6000, 7500, 8000, 9500])) for _ in range(400)]
    analysis, findings = _benford(_values(fabricated))
    assert analysis.conforms is False
    assert len(findings) == 1
    assert findings[0].finding_type == "benford"
    # Suggestive only — deliberately not high severity, and carrying a note
    # that it describes the distribution rather than any single figure.
    assert findings[0].severity == "low"
    assert findings[0].severity_note is not None


def test_benford_ignores_structural_numbering() -> None:
    # Clause numbers, page numbers and years are administratively assigned
    # and have no reason to follow Benford. Running against a real
    # FATCA/CRS form, counting them produced a "nonconforming" verdict on a
    # perfectly genuine document.
    analysis, findings = _benford(_values([float(n % 9 + 1) for n in range(400)], kind="quantity"))
    assert analysis.unable_to_assess is True
    assert findings == []


def test_benford_excludes_dates_and_percentages() -> None:
    analysis, _ = _benford(
        _values([4.5] * 200, kind="percentage") + _values([1.0] * 200, kind="date")
    )
    assert analysis.unable_to_assess is True


# AC6 — page and location ------------------------------------------------


def test_format_findings_carry_page_and_location() -> None:
    pages = [
        {"page_number": 3, "elements": [{"text": "Issued 2026-02-30 by the registrar"}]}
    ]
    result = _run(pages)
    finding = result.findings[0]
    assert finding.page == 3
    assert "registrar" in finding.location


def test_arithmetic_findings_may_have_no_page() -> None:
    # AC6 says "where available" — a finding derived from extracted fields
    # has no single place in the text to point at.
    findings = _check_balance_sheet_identity(
        {"total_assets": 10, "total_liabilities": 1, "net_asset_value": 5}
    )
    assert findings[0].page is None
    assert findings[0].location is None


# AC7 — unable to assess -------------------------------------------------


def test_unable_to_assess_when_no_numbers_anywhere() -> None:
    result = _run(_pages("This document contains no figures at all."))
    assert result.unable_to_assess is True
    assert "No numerical values" in result.unable_to_assess_reason
    assert result.benford.unable_to_assess is True


def test_unable_to_assess_when_text_extraction_fails() -> None:
    with patch("app.fraud_detection.numerical_validation.ingest_document") as ingest:
        ingest.side_effect = RuntimeError("corrupt file")
        result = check_numerical_validation("doc-1", b"bytes", "doc.pdf")

    assert result.unable_to_assess is True
    assert "corrupt file" in result.unable_to_assess_reason


def test_still_assessable_when_only_extracted_fields_have_figures() -> None:
    # An image-only scan whose text layer is unreadable can still have a
    # good extraction pass behind it, and those figures can still fail to
    # reconcile — so the whole check isn't unassessable just because the
    # text gave us nothing.
    fields = [
        ExtractedField(
            field_name="transactions",
            field_type="array",
            field_value=[
                {"amount": 0, "balance": 100.00},
                {"amount": 10.00, "balance": 999.00},
            ],
            confidence=0.9,
        )
    ]
    result = _run(_pages("no figures here"), fields)
    assert result.unable_to_assess is False
    assert len(result.findings) == 1


def test_unparseable_and_out_of_range_tokens_are_handled() -> None:
    # A year-like token that isn't a date, a malformed month name, and a
    # bare separator shouldn't crash the scan.
    result = _run(_pages("Ref 99/99/9999 and 01-Xyz-2026 and 1,"))
    assert isinstance(result.values_analysed, int)


def test_holdings_tolerate_rounded_display_values() -> None:
    holdings = [{"quantity": 3, "closing_price": 33.333, "market_value": 100.00}]
    assert _check_holdings({"holdings": holdings}) == []


def test_rejects_placeholder_year_zero_date() -> None:
    # calendar.monthrange accepts year 0, so this reads as a valid date
    # without an explicit bound — and "0000-01-01" is a classic
    # uninitialised-default value rather than a real date.
    assert _extract_dates("Dated 0000-01-01")[0] == [("0000-01-01", False)]
    assert _extract_dates("Dated 1970-01-01")[0] == [("1970-01-01", True)]


def test_to_float_guards_against_an_unparseable_body() -> None:
    # Defensive contract: the number regex can only hand this well-formed
    # digits today, but the helper must not raise if that ever changes.
    from app.fraud_detection.numerical_validation import _to_float

    assert _to_float("abc") is None
    assert _to_float("1,234.50") == 1234.50


def test_decode_passes_through_a_plain_string_field() -> None:
    from app.fraud_detection.numerical_validation import _decode

    assert _decode("not json at all") == "not json at all"
    assert _decode('{"a": 1}') == {"a": 1}


def test_reconciling_balance_sheet_and_shares_produce_no_findings() -> None:
    assert _check_balance_sheet_identity(
        {"total_assets": 500000, "total_liabilities": 200000, "net_asset_value": 300000}
    ) == []
    assert _check_share_allocation(
        {"issued_shares": 450000, "subscribers": [{"shares": 300000}, {"shares": 150000}]}
    ) == []


def test_holdings_skip_rows_missing_a_figure() -> None:
    assert _check_holdings({"holdings": [{"quantity": 10, "closing_price": None}]}) == []


def test_benford_tolerates_ordinary_drift_without_flagging() -> None:
    # A real document drifts from the ideal curve; only a gap beyond
    # chance at p<0.01 is reported.
    random.seed(3)
    values = [10 ** random.uniform(0, 4) for _ in range(200)]
    analysis, findings = _benford(_values(values))
    assert analysis.conforms is True
    assert findings == []


def test_value_scan_is_capped_on_a_pathological_document() -> None:
    from app.fraud_detection.numerical_validation import _MAX_VALUES

    huge = " ".join(f"{n}.00" for n in range(_MAX_VALUES + 500))
    assert len(_parse_values(_pages(huge))) <= _MAX_VALUES


def test_clean_document_is_assessed_not_unable() -> None:
    result = _run(_pages("Total $1,234.56 paid"))
    assert result.unable_to_assess is False
    assert result.findings == []
    assert result.values_analysed >= 1
