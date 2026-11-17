"""Singapore's public holidays, live from MOM's list on data.gov.sg.

MOM publishes them in the "Singapore Public Holidays" collection (691): one dataset per
year plus a consolidated one, including observed in-lieu days, and adds each year's
holidays months ahead. The datasets are found from the catalogue, never hardcoded.

The live list wins for every year it covers; the bundled copy in sg_reference/holidays.py
fills in any other year, and is used on its own when data.gov.sg can't be reached.
"""

from __future__ import annotations

import logging
from datetime import date

from ...numerical_validation.common import parse_field_date
from ..sg_reference.holidays import BUNDLED_HOLIDAYS, PublicHolidays
from .datagov import collection, dataset_name, datastore_search
from .http import LookupUnavailable

logger = logging.getLogger("fraud_detection.registry_lookups")

HOLIDAY_COLLECTION = 691  # "Singapore Public Holidays"
SOURCE = "MOM's public holiday list (data.gov.sg)"
# Some rows carry a mangled apostrophe ("New Year�s Day").
_APOSTROPHES = str.maketrans({"�": "'", "’": "'"})


async def _holiday_datasets() -> list[str]:
    """The consolidated dataset if there is one, otherwise every dataset in the collection."""
    dataset_ids = (await collection(HOLIDAY_COLLECTION)).dataset_ids
    for dataset_id in dataset_ids:
        if "consolidated" in (await dataset_name(dataset_id)).lower():
            return [dataset_id]
    return list(dataset_ids)


async def _live_holidays() -> dict[date, str]:
    days: dict[date, str] = {}
    for dataset_id in await _holiday_datasets():
        for row in await datastore_search(dataset_id, {}, limit=1000):
            day = parse_field_date(row.get("date"))
            name = " ".join(str(row.get("holiday") or "").translate(_APOSTROPHES).split())
            if day and name:
                days[day] = name
    return days


async def public_holidays() -> PublicHolidays:
    """Never raises: falls back to the bundled copy if data.gov.sg can't answer."""
    try:
        live = await _live_holidays()
    except LookupUnavailable as exc:
        logger.warning("Using the bundled public holiday list: %s", exc)
        return BUNDLED_HOLIDAYS
    if not live:
        return BUNDLED_HOLIDAYS
    live_years = frozenset(d.year for d in live)
    days = {d: n for d, n in BUNDLED_HOLIDAYS.days.items() if d.year not in live_years} | live
    return PublicHolidays(days=days, years=live_years | BUNDLED_HOLIDAYS.years, source=SOURCE)
