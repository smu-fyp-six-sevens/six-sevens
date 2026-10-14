from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

from app.fraud_detection.date_anomalies import check_date_anomalies
from app.fraud_detection.font_consistency import ExtractedField
from app.fraud_detection.registry_lookups import public_holidays, public_holidays_for
from app.fraud_detection.registry_lookups.datagov import Collection
from app.fraud_detection.registry_lookups.http import LookupUnavailable
from app.fraud_detection.sg_reference.holidays import BUNDLED_HOLIDAYS

_MODULE = "app.fraud_detection.registry_lookups.public_holidays"
_YEARLY, _CONSOLIDATED = "d_2027", "d_consolidated"
# Rows as MOM's dataset returns them, including its mangled apostrophe.
_ROWS = [
    {"_id": 1, "date": "2027-01-01", "day": "Friday", "holiday": "New Year�s Day"},
    {"_id": 2, "date": "2027-02-08", "day": "Monday", "holiday": "Chinese New Year (Observed)"},
    {"_id": 3, "date": "2026-08-10", "day": "Monday", "holiday": "National Day (Observed)"},
]


def _live(rows=_ROWS, names=None):
    names = names or {_YEARLY: "Public Holidays for 2027", _CONSOLIDATED: "Singapore Public Holidays (consolidated)"}
    searched = []

    async def collection(collection_id):
        assert collection_id == 691
        return Collection(tuple(names), datetime(2026, 7, 3, tzinfo=timezone.utc))

    async def dataset_name(dataset_id):
        return names[dataset_id]

    async def search(resource_id, filters, limit=10):
        searched.append(resource_id)
        return rows

    return searched, (patch(f"{_MODULE}.collection", side_effect=collection),
                      patch(f"{_MODULE}.dataset_name", side_effect=dataset_name),
                      patch(f"{_MODULE}.datastore_search", side_effect=search))


async def test_reads_the_consolidated_dataset_found_from_the_catalogue() -> None:
    searched, (a, b, c) = _live()
    with a, b, c:
        holidays = await public_holidays()
    assert searched == [_CONSOLIDATED]
    assert holidays.days[date(2027, 1, 1)] == "New Year's Day"
    assert holidays.covers(date(2027, 5, 1)) and "MOM" in holidays.source


async def test_live_years_replace_the_bundled_copy_and_other_years_are_kept() -> None:
    _, (a, b, c) = _live()
    with a, b, c:
        holidays = await public_holidays()
    assert date(2026, 8, 9) not in holidays.days  # 2026 comes from the live list only
    assert holidays.days[date(2024, 2, 12)] == BUNDLED_HOLIDAYS.days[date(2024, 2, 12)]  # 2024 from the bundled copy


async def test_falls_back_to_the_bundled_copy_when_data_gov_sg_is_down() -> None:
    with patch(f"{_MODULE}.collection", side_effect=LookupUnavailable("rate limited")):
        assert await public_holidays() is BUNDLED_HOLIDAYS


async def test_only_bank_statements_make_the_lookup() -> None:
    with patch(f"{_MODULE}.collection", side_effect=AssertionError("should not be called")):
        assert await public_holidays_for("payslip") is BUNDLED_HOLIDAYS


def _giro(day: str, holidays=BUNDLED_HOLIDAYS):
    rows = [{"date": day, "description": "Giro Payment / GIRO / SP SERVICES LTD", "amount": -1}]
    fields = [ExtractedField(field_name="transactions", field_type="array", field_value=rows, confidence=0.99)]
    return check_date_anomalies("doc-1", fields, "bank_statement", holidays)


async def test_giro_check_uses_the_live_list_for_new_years() -> None:
    _, (a, b, c) = _live()
    with a, b, c:
        holidays = await public_holidays()
    result = _giro("08 Feb 2027", holidays)
    assert [f.check_code for f in result.findings] == ["BS.GIRO_BUSINESS_DAY"]
    assert "Chinese New Year (Observed)" in result.findings[0].summary
    assert not [s for s in result.skipped_checks if s.check_code == "BS.GIRO_BUSINESS_DAY"]


def test_giro_check_says_when_a_years_holidays_are_unknown() -> None:
    result = _giro("08 Feb 2027")  # a Monday holiday, but the bundled copy stops at 2026
    assert result.findings == []
    skip = next(s for s in result.skipped_checks if s.check_code == "BS.GIRO_BUSINESS_DAY")
    assert "2027" in skip.reason and "Sundays" in skip.reason
