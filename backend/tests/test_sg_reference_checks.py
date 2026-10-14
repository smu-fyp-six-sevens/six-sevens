"""CPF, GST and bank account number checks, against the official reference data."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.fraud_detection.font_consistency import ExtractedField
from app.fraud_detection.numerical_validation.runner import run_numerical_checks
from app.fraud_detection.sg_reference.cpf import CPF_RATE_TABLES, band_for_age, bands_in_force, contribution
from app.fraud_detection.sg_reference.gst import gst_rate_on, possible_rates
from app.fraud_detection.sg_reference.holidays import giro_closed_reason


def _field(name: str, value) -> ExtractedField:
    return ExtractedField(field_name=name, field_type="string", field_value=value, confidence=0.99)


def _run(fields: list[ExtractedField], document_type: str):
    outcome = run_numerical_checks(document_type, fields, [])
    return SimpleNamespace(findings=outcome.findings, skipped=outcome.skipped)


def _codes(result, code: str) -> list:
    return [f for f in result.findings if f.check_code == code]


# --- CPF reference data: reproduces CPF Board's published maximums ---------------------------


def test_cpf_tables_reproduce_cpf_boards_published_maximum_contributions() -> None:
    # "Max. of" figures from each year's Table 1, at that year's ordinary wage ceiling.
    published = {
        ("2024-01-01", 6_800): [(2516, 1360), (2108, 1088), (1496, 714), (1122, 510), (850, 340)],
        ("2025-01-01", 7_400): [(2738, 1480), (2405, 1258), (1739, 851), (1221, 555), (925, 370)],
        ("2026-01-01", 8_000): [(2960, 1600), (2720, 1440), (2000, 1000), (1320, 600), (1000, 400)],
    }
    for (effective, ceiling), maximums in published.items():
        bands = CPF_RATE_TABLES[effective]
        assert [contribution(b, 20_000, ceiling) for b in bands] == maximums, effective


def test_cpf_low_wage_bands_follow_cpf_formula() -> None:
    bands = bands_in_force(date(2026, 3, 31))
    assert bands is not None
    under_55 = bands[0]
    assert contribution(under_55, 50, 8000) == (0, 0)
    assert contribution(under_55, 400, 8000) == (68, 0)  # 17% of TW, employer only
    # $600: 17% x 600 + 0.6 x 100 = 162 total, employee 0.6 x 100 = 60
    assert contribution(under_55, 600, 8000) == (162, 60)


def test_cpf_age_band_changes_the_month_after_the_birthday() -> None:
    bands = bands_in_force(date(2026, 1, 31))
    assert bands is not None
    born = date(1971, 3, 15)  # turns 55 in March 2026
    assert band_for_age(bands, born, date(2026, 3, 31)).label == "55 & below"
    assert band_for_age(bands, born, date(2026, 4, 30)).label == "above 55 to 60"


# --- PAY.CPF_RATE ----------------------------------------------------------------------------


def test_cpf_rate_accepts_a_correct_under_55_payslip() -> None:
    result = _run([_field("employee_cpf", "1000.00"), _field("employer_cpf", "850.00"),
                   _field("ordinary_wages", "5000"), _field("pay_date", "2026-03-31")], "payslip")
    assert _codes(result, "PAY.CPF_RATE") == []


def test_cpf_rate_accepts_a_senior_band_when_no_age_is_given() -> None:
    # Above 60 to 65 in 2026: 12.5% employee on $5,000 = $625, total 25% = $1,250.
    result = _run([_field("employee_cpf", "625"), _field("employer_cpf", "625"),
                   _field("ordinary_wages", "5000"), _field("pay_date", "2026-03-31")], "payslip")
    assert _codes(result, "PAY.CPF_RATE") == []


def test_cpf_rate_flags_a_contribution_above_any_band_as_impossible() -> None:
    result = _run([_field("employee_cpf", "1200"), _field("ordinary_wages", "5000"),
                   _field("pay_date", "2026-03-31")], "payslip")
    findings = _codes(result, "PAY.CPF_RATE")
    assert len(findings) == 1 and findings[0].severity == "impossible"


def test_cpf_rate_uses_the_date_of_birth_band() -> None:
    # 20% is right for under 55, but this employee is 62: 12.5% applies.
    result = _run([_field("employee_cpf", "1000"), _field("ordinary_wages", "5000"),
                   _field("pay_date", "2026-03-31"), _field("date_of_birth", "1964-01-10")], "payslip")
    assert len(_codes(result, "PAY.CPF_RATE")) == 1


def test_cpf_rate_flags_an_employer_share_that_does_not_fit_the_employee_share() -> None:
    result = _run([_field("employee_cpf", "1000"), _field("employer_cpf", "500"),
                   _field("ordinary_wages", "5000"), _field("pay_date", "2026-03-31")], "payslip")
    findings = _codes(result, "PAY.CPF_RATE")
    assert len(findings) == 1 and findings[0].severity == "inconsistent"


def test_cpf_rate_reads_cpf_from_a_deduction_line_when_not_extracted_separately() -> None:
    deductions = [{"description": "CPF Employee Contribution", "amount": "SGD 1,000.00"},
                  {"description": "CDAC Fund", "amount": "1.00"}]
    ok = _run([_field("deductions_items", deductions), _field("ordinary_wages", "5000"),
               _field("pay_date", "2026-03-31")], "payslip")
    assert _codes(ok, "PAY.CPF_RATE") == []
    assert not any(s.check_code == "PAY.CPF_RATE" for s in ok.skipped)

    too_high = _run([_field("deductions_items", [{"description": "CPF (Employee)", "amount": -1300}]),
                     _field("ordinary_wages", "5000"), _field("pay_date", "2026-03-31")], "payslip")
    findings = _codes(too_high, "PAY.CPF_RATE")
    assert len(findings) == 1 and findings[0].field == "deductions_items"


def test_cpf_rate_ignores_the_employer_cpf_line_and_skips_without_any_cpf() -> None:
    deductions = [{"description": "Employer CPF", "amount": 850}, {"description": "Tax withholding", "amount": 519.77}]
    result = _run([_field("deductions_items", deductions), _field("ordinary_wages", "5000"),
                   _field("pay_date", "2026-03-31")], "payslip")
    assert _codes(result, "PAY.CPF_RATE") == []
    assert any(s.check_code == "PAY.CPF_RATE" and "employee_cpf" in s.reason for s in result.skipped)


def test_cpf_rate_skips_a_month_with_a_bonus() -> None:
    result = _run([_field("employee_cpf", "3000"), _field("ordinary_wages", "5000"),
                   _field("additional_wages", "10000"), _field("pay_date", "2026-03-31")], "payslip")
    assert _codes(result, "PAY.CPF_RATE") == []
    assert any(s.check_code == "PAY.CPF_RATE" for s in result.skipped)


# --- GST --------------------------------------------------------------------------------------


def test_gst_rates_follow_iras_rate_changes() -> None:
    assert gst_rate_on(date(2022, 12, 31)) == 0.07
    assert gst_rate_on(date(2023, 6, 1)) == 0.08
    assert gst_rate_on(date(2024, 1, 1)) == 0.09
    assert possible_rates(date(2024, 1, 15)) == {0.08, 0.09}  # transitional January
    assert possible_rates(date(2024, 2, 15)) == {0.09}


def test_gst_accepts_9_percent_on_a_2026_bill() -> None:
    result = _run([_field("gst_amount", "9.00"), _field("charges_before_gst", "100.00"),
                   _field("bill_date", "2026-05-02")], "proof_of_address")
    assert _codes(result, "POA.GST") == []


def test_gst_flags_the_old_rate_on_a_2026_bill() -> None:
    result = _run([_field("gst_amount", "7.00"), _field("charges_before_gst", "100.00"),
                   _field("bill_date", "2026-05-02")], "proof_of_address")
    assert len(_codes(result, "POA.GST")) == 1


# --- Bank account number format ----------------------------------------------------------------


def test_account_number_format_accepts_each_banks_published_lengths() -> None:
    for bank, number in (("DBS Bank Ltd", "029-018889-1"), ("POSB", "183019992"), ("OCBC Bank", "987-654-321-0"),
                         ("OCBC", "501-123956-001"), ("UOB", "901-028-808-0")):
        result = _run([_field("bank_name", bank), _field("account_number", number)], "bank_statement")
        assert _codes(result, "BS.ACCOUNT_NUMBER_FORMAT") == [], bank


def test_account_number_format_flags_a_wrong_length() -> None:
    result = _run([_field("bank_name", "UOB"), _field("account_number", "12345678")], "bank_statement")
    assert len(_codes(result, "BS.ACCOUNT_NUMBER_FORMAT")) == 1


def test_account_number_format_skips_a_bank_without_a_published_format() -> None:
    result = _run([_field("bank_name", "Green Link Digital Bank"), _field("account_number", "1")], "bank_statement")
    assert _codes(result, "BS.ACCOUNT_NUMBER_FORMAT") == []


# --- GIRO processing days --------------------------------------------------------------------------


def test_giro_closed_on_sundays_and_public_holidays_but_not_saturdays() -> None:
    assert giro_closed_reason(date(2026, 7, 12)) == "a Sunday"
    assert giro_closed_reason(date(2026, 8, 10)) == "a public holiday (National Day (Observed))"
    assert giro_closed_reason(date(2025, 5, 3)) == "a public holiday (Polling Day)"  # a Saturday holiday
    assert giro_closed_reason(date(2026, 8, 1)) is None  # an ordinary Saturday
    assert giro_closed_reason(date(2026, 8, 3)) is None


# --- Payslips that itemise CPF as "Employee contribution" and show deductions as negatives ---------

# As extracted from a real test payslip (document f7514257).
_PAYSLIP = [
    ("earnings_items", [{"amount": "8,950.00", "description": "Basic salary"},
                        {"amount": "320.00", "description": "Allowances"},
                        {"amount": "140.00", "description": "Overtime"}]),
    ("deductions_items", [{"amount": "-1,790.00", "description": "Employee contribution"},
                          {"amount": "-58.93", "description": "Other deductions"}]),
    ("total_earnings", "9,410.00"), ("total_deductions", "1,848.93"), ("net_pay", "SGD 7,561.07"),
    ("basic_pay", "8,950.00"), ("pay_date", "2026-03-31"),
]


def test_negative_deduction_lines_match_a_positive_total() -> None:
    result = _run([_field(n, v) for n, v in _PAYSLIP], "payslip")
    assert _codes(result, "PAY.DEDUCTIONS_SUM") == [] and _codes(result, "PAY.NET") == []


def test_employee_contribution_line_is_the_cpf_and_earnings_are_the_ordinary_wages() -> None:
    result = _run([_field(n, v) for n, v in _PAYSLIP], "payslip")
    # $1,790 is 20% of basic pay; CPF's 2026 ceiling of $8,000 caps the employee's share at $1,600.
    ceiling = _codes(result, "PAY.CPF_CEILING")
    assert len(ceiling) == 1 and ceiling[0].field == "deductions_items"
    rate = _codes(result, "PAY.CPF_RATE")
    assert len(rate) == 1 and rate[0].severity == "impossible"


def test_self_help_group_contributions_are_not_cpf() -> None:
    deductions = [{"description": "CDAC contribution", "amount": -3}, {"description": "Employer contribution", "amount": 1500}]
    result = _run([_field("deductions_items", deductions), _field("ordinary_wages", "5000"),
                   _field("pay_date", "2026-03-31")], "payslip")
    assert any(s.check_code == "PAY.CPF_RATE" and "employee_cpf" in s.reason for s in result.skipped)


def test_ordinary_wages_are_not_taken_from_earnings_with_a_bonus() -> None:
    earnings = [{"description": "Basic salary", "amount": 5000}, {"description": "Performance bonus", "amount": 5000}]
    result = _run([_field("earnings_items", earnings), _field("employee_cpf", "1000"),
                   _field("pay_date", "2026-03-31")], "payslip")
    assert any(s.check_code == "PAY.CPF_RATE" and "ordinary_wages" in s.reason for s in result.skipped)
