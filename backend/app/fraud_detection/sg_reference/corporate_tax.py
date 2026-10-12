"""Singapore corporate income tax by Year of Assessment (YA), for checking a Notice of Assessment.

Every figure is from IRAS's "Corporate Income Tax Rate, Rebates & Tax Exemption Schemes"
page (iras.gov.sg, read 10 Oct 2026):

* Tax rate: a flat 17% of chargeable income.
* Partial tax exemption (most companies): 75% of the first $10,000, then 50% of the next
  $190,000 from YA 2020; 50% of the next $290,000 for YA 2019 and before.
* Start-up tax exemption (a new company's first 3 YAs): 75% of the first $100,000 and 50%
  of the next $100,000 from YA 2020; 100% of the first $100,000 and 50% of the next
  $200,000 for YA 2019 and before.
* CIT Rebate, from IRAS's "Rebate Percentages & Caps" table. YA 2021 to 2023 have no
  rebate (the table skips them). From YA 2024 a company meeting the local employee
  condition also gets a cash grant, and its rebate is given net of it: "If CIT Rebate <=
  $2,000, no CIT Rebate to be given. If CIT Rebate > $2,000, CIT Rebate (capped at
  $40,000) less $2,000 to be given."
* YA 2026 has two genuine versions: Budget 2026 announced 40% with a $1,500 cash grant and
  $30,000 total cap, then on 7 Apr 2026 enhanced it to 50%, $2,000 and $40,000. A notice
  issued before the enhancement (and not yet amended) carries the first version.

Years before YA 2013 or after YA 2026 aren't covered: a notice for one is skipped, not
checked against another year's rules.
"""

from __future__ import annotations

from dataclasses import dataclass

TAX_RATE = 0.17


@dataclass(frozen=True)
class Exemption:
    name: str
    first_pct: float
    first_cap: int
    next_pct: float
    next_cap: int


@dataclass(frozen=True)
class Rebate:
    pct: float
    cap: int
    # Paid separately and taken off the rebate, for a company meeting the local employee condition.
    cash_grant: int = 0


_PARTIAL_FROM_2020 = Exemption("partial tax exemption", 0.75, 10_000, 0.50, 190_000)
_PARTIAL_TO_2019 = Exemption("partial tax exemption", 0.75, 10_000, 0.50, 290_000)
_STARTUP_FROM_2020 = Exemption("start-up tax exemption", 0.75, 100_000, 0.50, 100_000)
_STARTUP_TO_2019 = Exemption("start-up tax exemption", 1.00, 100_000, 0.50, 200_000)

_YA2024_ONWARDS = Rebate(0.50, 40_000, cash_grant=2_000)

CIT_REBATES: dict[int, tuple[Rebate, ...]] = {
    2013: (Rebate(0.30, 30_000),), 2014: (Rebate(0.30, 30_000),), 2015: (Rebate(0.30, 30_000),),
    2016: (Rebate(0.50, 20_000),),
    2017: (Rebate(0.50, 25_000),),
    2018: (Rebate(0.40, 15_000),),
    2019: (Rebate(0.20, 10_000),),
    2020: (Rebate(0.25, 15_000),),
    2021: (), 2022: (), 2023: (),
    2024: (_YA2024_ONWARDS,),
    2025: (_YA2024_ONWARDS,),
    2026: (_YA2024_ONWARDS, Rebate(0.40, 30_000, cash_grant=1_500)),
}


def exemptions_for(year_of_assessment: int) -> tuple[Exemption, Exemption]:
    """The partial and start-up exemptions in force for the YA (partial first: most companies)."""
    if year_of_assessment >= 2020:
        return _PARTIAL_FROM_2020, _STARTUP_FROM_2020
    return _PARTIAL_TO_2019, _STARTUP_TO_2019


def rebates_for(year_of_assessment: int) -> tuple[Rebate, ...] | None:
    """The genuine rebate terms for the YA (empty: no rebate that year), or None if not covered."""
    return CIT_REBATES.get(year_of_assessment)
