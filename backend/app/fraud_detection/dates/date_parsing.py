"""Reading a date out of an extracted field, in whatever form the document printed it.

Extraction keeps dates as printed, so one parser has to read every form a Singapore document uses:

* numeric, year first:   2026-08-23, 2026/08/23, 2026.08.23, 20260823, 2026-08-23T10:15:00Z
* numeric, day first:    23/08/2026, 23-08-2026, 23.08.2026, 23/08/26
* with a month name:     23 Aug 2026, 23-Aug-26, 23rd August, 2026, Aug 23, 2026, August 23rd 2026,
                         2026 Aug 23, Sunday, 23 August 2026, the 23rd day of August 2026, Sept 23 2026
* a trailing time:       23 Aug 2026 08:12:17, 23/08/2026 2:30 PM, 2026-08-23 10:15 SGT

Numeric dates are day first, as Singapore writes them. Month first is used only when day first is
impossible ("08/23/2026" has no month 23), which is how a US-style date on a foreign form reads.

Two-digit years are 20xx up to ten years past the current year and 19xx before that ("26" is 2026,
"85" is 1985).

Some printed dates are real but incomplete: a transaction line "23 Aug" or a financial year end "31/03"
(no year), or a pay period "September 2026" (no day). parse_date_parts reads those too, and says what is
missing; parse_date gives a full date only, filling a missing year from default_year when the caller knows it
(a statement's transactions take their year from the statement period).
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_WEEKDAYS = (
    "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    "|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun"
)

# A time of day after the date, with optional seconds, am/pm and a zone ("08:12:17", "2:30 PM", "10:15 SGT",
# "T10:15:00Z", "T10:15:00+08:00").
_TRAILING_TIME_RE = re.compile(
    r"(?:[T\s]+)\d{1,2}[:.]\d{2}(?:[:.]\d{2}(?:\.\d+)?)?\s*(?:[ap]\.?m\.?)?\s*(?:z|[a-z]{2,4}|[+-]\d{2}:?\d{2})?$",
    re.IGNORECASE,
)
_WEEKDAY_RE = re.compile(rf"\b(?:{_WEEKDAYS})\b\.?,?", re.IGNORECASE)
_ORDINAL_RE = re.compile(r"(\d)(?:st|nd|rd|th)\b", re.IGNORECASE)
# Legal wording: "the 23rd day of August 2026", "23 day of March 2015".
_FILLER_RE = re.compile(r"\b(?:the|day|of)\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r"\d+|[a-z]+", re.IGNORECASE)
_SEPARATORS = re.compile(r"^[\s,./\-]*$")

# How far ahead of the current year a two-digit year may still be read as 20xx.
_TWO_DIGIT_YEAR_WINDOW = 10


@dataclass(frozen=True)
class DateParts:
    """A printed date, possibly incomplete: day or year is None when the document didn't print it."""
    day: int | None
    month: int
    year: int | None

    @property
    def complete(self) -> bool:
        return self.day is not None and self.year is not None


def _full_year(text: str) -> int:
    year = int(text)
    if len(text) > 2:
        return year
    pivot = (date.today().year + _TWO_DIGIT_YEAR_WINDOW) % 100
    return 2000 + year if year <= pivot else 1900 + year


def _valid(day: int | None, month: int, year: int | None) -> bool:
    if not 1 <= month <= 12:
        return False
    if year is not None and not 1000 <= year <= 9999:
        return False
    if day is None:
        return True
    # A day with no year: allow 29 February (it exists in some year).
    last = calendar.monthrange(year if year is not None else 2000, month)[1]
    return 1 <= day <= last


def _parts(day: int | None, month: int, year: int | None) -> DateParts | None:
    return DateParts(day, month, year) if _valid(day, month, year) else None


def _clean(text: str) -> str:
    text = _TRAILING_TIME_RE.sub("", text.strip())
    text = _WEEKDAY_RE.sub(" ", text)
    text = _ORDINAL_RE.sub(r"\1", text)
    text = _FILLER_RE.sub(" ", text)
    return text.strip(" ,.")


def _numeric(numbers: list[str]) -> DateParts | None:
    """Digits only: 2026-08-23, 23/08/2026, 23/08/26, 20260823, and the incomplete 23/08 and 08/2026."""
    if len(numbers) == 1 and len(numbers[0]) == 8:
        token = numbers[0]
        return _parts(int(token[6:]), int(token[4:6]), int(token[:4]))
    if len(numbers) == 3:
        first, second, third = numbers
        if len(first) == 4:
            return _parts(int(third), int(second), int(first))
        if len(third) not in (2, 4):
            return None
        year = _full_year(third)
        return _parts(int(first), int(second), year) or _parts(int(second), int(first), year)
    if len(numbers) == 2:
        first, second = numbers
        if len(second) == 4:  # 08/2026: a month and year
            return _parts(None, int(first), int(second))
        if len(first) <= 2 and len(second) <= 2:  # 31/03: a day and month
            return _parts(int(first), int(second), None)
    return None


def _with_month_name(tokens: list[str]) -> DateParts | None:
    """One month name and up to two numbers, in any of the orders documents use."""
    months = [i for i, t in enumerate(tokens) if t.lower() in _MONTHS]
    if len(months) != 1 or len(tokens) > 3:
        return None
    month = _MONTHS[tokens[months[0]].lower()]
    numbers = [t for t in tokens if t.isdigit()]
    if len(numbers) != len(tokens) - 1:
        return None  # a word that isn't a month name
    if not numbers:
        return None
    if len(numbers) == 1:
        value = numbers[0]
        if len(value) == 4:  # September 2026
            return _parts(None, month, int(value))
        if len(value) <= 2:  # 23 Aug / Aug 23
            return _parts(int(value), month, None)
        return None
    first, second = numbers
    if len(first) == 4:  # 2026 Aug 23
        return _parts(int(second), month, int(first))
    if len(second) in (2, 4) and len(first) <= 2:  # 23 Aug 2026 / Aug 23, 2026 / 23-Aug-26
        return _parts(int(first), month, _full_year(second))
    return None


def parse_date_parts(value: Any) -> DateParts | None:
    """The date a field's value prints, complete or not; None if it isn't a date at all."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = _clean(value)
    tokens = _TOKEN_RE.findall(text)
    if not tokens or not _SEPARATORS.match(_TOKEN_RE.sub("", text)):
        return None  # leftover characters that aren't separators: not a plain date
    if all(t.isdigit() for t in tokens):
        return _numeric(tokens)
    return _with_month_name(tokens)


def date_in_period(value: Any, start: date | None, end: date | None) -> date | None:
    """A printed date that may have no year (a statement's "20 JUN"), placed by the period it belongs to: a
    complete date as printed, else the year that puts it inside start..end. A date the period can't place
    stays undated rather than guessed."""
    parts = parse_date_parts(value)
    if parts is None or parts.day is None:
        return None
    if parts.year is not None:
        return date(parts.year, parts.month, parts.day)
    if start is not None:
        years = range(start.year, (end.year if end is not None else start.year + 1) + 1)
    elif end is not None:
        years = range(end.year - 1, end.year + 1)
    else:
        return None
    for year in years:
        if not _valid(parts.day, parts.month, year):
            continue
        candidate = date(year, parts.month, parts.day)
        if (start is None or candidate >= start) and (end is None or candidate <= end):
            return candidate
    return None


def parse_date(value: Any, default_year: int | None = None) -> date | None:
    """A complete date from a field's value. A printed date with no year takes default_year if given;
    one with no day (a month and year) is not a date."""
    parts = parse_date_parts(value)
    if parts is None or parts.day is None:
        return None
    year = parts.year if parts.year is not None else default_year
    if year is None or not _valid(parts.day, parts.month, year):
        return None
    return date(year, parts.month, parts.day)
