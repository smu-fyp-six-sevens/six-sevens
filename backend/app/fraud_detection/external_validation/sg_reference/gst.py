"""Singapore GST rate by date.

Source: IRAS, "GST Rate Change": 7% to 8% from 1 January 2023, 8% to 9% from
1 January 2024 (https://www.iras.gov.sg/taxes/goods-services-tax-(gst)/gst-rate-change/gst-rate-change-for-business/preparing-for-gst-rate-change).
7% applied from 1 July 2007.
"""

from __future__ import annotations

from datetime import date

GST_RATES: dict[str, float] = {"2007-07-01": 0.07, "2023-01-01": 0.08, "2024-01-01": 0.09}


def gst_rate_on(on: date) -> float | None:
    applicable = [k for k in GST_RATES if date.fromisoformat(k) <= on]
    return GST_RATES[max(applicable)] if applicable else None


def possible_rates(on: date) -> set[float]:
    """The rate in force, plus the previous one for a bill in the first month of a new rate.

    IRAS lets a supply that straddles a rate change be charged at the old rate under
    its transitional rules, so a January bill can genuinely show last year's rate.
    """
    current = gst_rate_on(on)
    rates = {current} if current is not None else set()
    if on.month == 1 and f"{on.year}-01-01" in GST_RATES:
        previous = gst_rate_on(date(on.year - 1, 12, 31))
        if previous is not None:
            rates.add(previous)
    return rates
