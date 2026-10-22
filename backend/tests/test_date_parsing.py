"""Every date form a document prints, read by the one shared parser (fraud_detection/dates/date_parsing.py)."""

from __future__ import annotations

from datetime import date

import pytest

from app.fraud_detection.dates.date_parsing import DateParts, date_in_period, parse_date, parse_date_parts


@pytest.mark.parametrize("text", [
    # numeric, year first
    "2026-08-23", "2026/08/23", "2026.08.23", "20260823", "2026-8-23",
    # numeric, day first
    "23/08/2026", "23-08-2026", "23.08.2026", "23/8/2026", "23/08/26",
    # month names, any order, ordinals, commas, abbreviations
    "23 Aug 2026", "23 August 2026", "23-Aug-2026", "23-Aug-26", "23 AUG 2026", "23 Aug. 2026",
    "23rd Aug 2026", "23rd August, 2026", "23RD AUGUST, 2026", "Aug 23, 2026", "August 23rd, 2026",
    "August 23 2026", "2026 Aug 23",
    # weekdays and legal wording
    "Sunday, 23 August 2026", "Sun 23 Aug 2026", "the 23rd day of August 2026", "23 day of August 2026", "Dated this 23 day of August 2026",
    # a trailing time
    "23 Aug 2026 08:12:17", "23/08/2026 2:30 PM", "2026-08-23T10:15:00Z", "2026-08-23T10:15:00+08:00",
    "2026-08-23 10:15 SGT", "23 Aug 2026, 10.30am",
])
def test_complete_dates_in_every_printed_form(text: str) -> None:
    assert parse_date(text) == date(2026, 8, 23), text


def test_sept_and_other_month_spellings() -> None:
    assert parse_date("23 Sept 2026") == date(2026, 9, 23)
    assert parse_date("1 June 2026") == date(2026, 6, 1)
    assert parse_date("30 jun 2026") == date(2026, 6, 30)


def test_numeric_dates_are_day_first_unless_that_is_impossible() -> None:
    assert parse_date("03/04/2026") == date(2026, 4, 3)  # Singapore order: 3 April
    assert parse_date("08/23/2026") == date(2026, 8, 23)  # no month 23, so month first


def test_two_digit_years_are_read_around_the_current_year() -> None:
    this_year = date.today().year
    assert parse_date("01-Jun-26") == date(2026, 6, 1)
    assert parse_date("12-Mar-85") == date(1985, 3, 12)
    assert parse_date(f"01/01/{(this_year + 10) % 100:02d}").year == this_year + 10
    assert parse_date(f"01/01/{(this_year + 11) % 100:02d}").year == this_year + 11 - 100


@pytest.mark.parametrize(("text", "parts"), [
    ("23rd aug", DateParts(23, 8, None)),
    ("23 Aug", DateParts(23, 8, None)),
    ("03 JUN", DateParts(3, 6, None)),
    ("Aug 23", DateParts(23, 8, None)),
    ("31/03", DateParts(31, 3, None)),
    ("29 Feb", DateParts(29, 2, None)),  # exists in a leap year
    ("September 2026", DateParts(None, 9, 2026)),
    ("Sept 2026", DateParts(None, 9, 2026)),
    ("08/2026", DateParts(None, 8, 2026)),
])
def test_incomplete_dates_are_read_with_what_is_missing(text: str, parts: DateParts) -> None:
    assert parse_date_parts(text) == parts
    assert parse_date(text) is None  # not a full date on its own


def test_a_date_with_no_year_takes_the_year_given() -> None:
    assert parse_date("23rd aug", default_year=2026) == date(2026, 8, 23)
    assert parse_date("29 Feb", default_year=2025) is None  # 2025 has no 29 February
    assert parse_date("September 2026", default_year=2026) is None  # no day


@pytest.mark.parametrize("text", [
    "", "   ", "SINGAPORE", "689 kWh", "Bankruptcy Order", "not less than 21 days' notice in writing",
    "31/02/2026", "32 Aug 2026", "23 Foo 2026", "2026-13-01", "23/08/2026/01", "123456", "Aug Sep 2026",
    "02 Jan 2026 - 31 Jan 2026",  # a range is two dates, not one
])
def test_text_that_is_not_one_valid_date(text: str) -> None:
    assert parse_date(text) is None, text
    assert parse_date_parts(text) is None, text


def test_non_strings_are_not_dates() -> None:
    assert parse_date(None) is None
    assert parse_date(20260823) is None


def test_a_statement_row_takes_its_year_from_the_period() -> None:
    # A December-January statement: "28 DEC" is in the first year, "03 JAN" in the next.
    start, end = date(2025, 12, 15), date(2026, 1, 14)
    assert date_in_period("28 DEC", start, end) == date(2025, 12, 28)
    assert date_in_period("03 JAN", start, end) == date(2026, 1, 3)
    assert date_in_period("01-Jun-26", start, end) == date(2026, 6, 1)  # complete as printed
    assert date_in_period("20 JUL", start, end) is None  # the period can't place it
