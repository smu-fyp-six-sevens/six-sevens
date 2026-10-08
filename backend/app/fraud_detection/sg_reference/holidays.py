"""Singapore public holidays, and the days GIRO is processed.

Holidays: MOM's "Singapore Public Holidays (consolidated)" dataset on data.gov.sg,
including observed in-lieu days. It's looked up live (registry_lookups/public_holidays.py);
the table below is a copy of it, used when data.gov.sg can't be reached.

GIRO processing days:
* Interbank GIRO runs on business days only: Singapore moved to a 5-day clearing week
  in 2005 ("no crediting or debiting of customer accounts with cheques, GIRO or
  Interbank funds transfers" on Saturdays, UOB 5DCW FAQ).
* Same-bank GIRO can run on Saturdays at some banks (DBS iDEAL cut-off times: DBS/POSB
  beneficiaries Mon-Sat; UOB BIBPlus: GIRO Express value dates exclude only Sundays and
  public holidays). A statement doesn't say which bank the other party uses, so
  Saturday GIRO is never treated as an anomaly.
* No bank processes GIRO on a Sunday or public holiday (DBS: "No payment will be
  processed on Sundays and Public Holidays except for FAST Payment, Account Transfer
  and Intra Company Transfer").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

SG_PUBLIC_HOLIDAYS: dict[date, str] = {
    date(2023, 1, 1): "New Year's Day", date(2023, 1, 2): "New Year's Day (Observed)",
    date(2023, 1, 22): "Chinese New Year", date(2023, 1, 23): "Chinese New Year",
    date(2023, 1, 24): "Chinese New Year (Observed)", date(2023, 4, 7): "Good Friday",
    date(2023, 4, 22): "Hari Raya Puasa", date(2023, 5, 1): "Labour Day", date(2023, 6, 2): "Vesak Day",
    date(2023, 6, 29): "Hari Raya Haji", date(2023, 8, 9): "National Day", date(2023, 9, 1): "Polling Day",
    date(2023, 11, 12): "Deepavali", date(2023, 11, 13): "Deepavali (Observed)", date(2023, 12, 25): "Christmas Day",

    date(2024, 1, 1): "New Year's Day", date(2024, 2, 10): "Chinese New Year", date(2024, 2, 11): "Chinese New Year",
    date(2024, 2, 12): "Chinese New Year (Observed)", date(2024, 3, 29): "Good Friday",
    date(2024, 4, 10): "Hari Raya Puasa", date(2024, 5, 1): "Labour Day", date(2024, 5, 22): "Vesak Day",
    date(2024, 6, 17): "Hari Raya Haji", date(2024, 8, 9): "National Day", date(2024, 10, 31): "Deepavali",
    date(2024, 12, 25): "Christmas Day",

    date(2025, 1, 1): "New Year's Day", date(2025, 1, 29): "Chinese New Year", date(2025, 1, 30): "Chinese New Year",
    date(2025, 3, 31): "Hari Raya Puasa", date(2025, 4, 18): "Good Friday", date(2025, 5, 1): "Labour Day",
    date(2025, 5, 3): "Polling Day", date(2025, 5, 12): "Vesak Day", date(2025, 6, 7): "Hari Raya Haji",
    date(2025, 8, 9): "National Day", date(2025, 10, 20): "Deepavali", date(2025, 12, 25): "Christmas Day",

    date(2026, 1, 1): "New Year's Day", date(2026, 2, 17): "Chinese New Year", date(2026, 2, 18): "Chinese New Year",
    date(2026, 3, 21): "Hari Raya Puasa", date(2026, 4, 3): "Good Friday", date(2026, 5, 1): "Labour Day",
    date(2026, 5, 27): "Hari Raya Haji", date(2026, 5, 31): "Vesak Day", date(2026, 6, 1): "Vesak Day (Observed)",
    date(2026, 8, 9): "National Day", date(2026, 8, 10): "National Day (Observed)", date(2026, 11, 8): "Deepavali",
    date(2026, 11, 9): "Deepavali (Observed)", date(2026, 12, 25): "Christmas Day",
}

HOLIDAY_YEARS = frozenset(d.year for d in SG_PUBLIC_HOLIDAYS)


@dataclass(frozen=True)
class PublicHolidays:
    days: dict[date, str]
    years: frozenset[int]  # the years whose holidays are known
    source: str

    def covers(self, day: date) -> bool:
        return day.year in self.years


BUNDLED_HOLIDAYS = PublicHolidays(days=SG_PUBLIC_HOLIDAYS, years=HOLIDAY_YEARS, source="the bundled copy of MOM's list")


def giro_closed_reason(day: date, holidays: PublicHolidays = BUNDLED_HOLIDAYS) -> str | None:
    """Why no bank processes GIRO on this day, or None if it can.

    Sundays are always closed. Public holidays are only known for holidays.years;
    outside them only the Sunday rule applies.
    """
    if day in holidays.days:
        return f"a public holiday ({holidays.days[day]})"
    if day.weekday() == 6:
        return "a Sunday"
    return None
