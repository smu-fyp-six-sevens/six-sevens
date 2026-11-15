"""The stand-in ACRA register for the synthetic test companies (registry_lookups/synthetic_acra.py)."""

from __future__ import annotations

import asyncio

import pytest

from app.fraud_detection.external_validation.registry_lookups import acra, synthetic_acra

ENTITY = {
    "uen": "201657409E", "name": "TIDEWATER MARINE SUPPLIES PTE LTD", "status": "Live Company",
    "incorporation_date": "2016-11-06", "address": "5012 ANG MO KIO AVENUE 5, #01-21, SINGAPORE 569876",
    "postal_code": "569876", "former_names": [],
}


@pytest.fixture
def register(monkeypatch):
    monkeypatch.setattr(synthetic_acra, "_entities", lambda: {"201657409E": ENTITY})

    async def no_network(*args, **kwargs):
        raise AssertionError("data.gov.sg was called")

    monkeypatch.setattr(acra, "_uen_datasets", no_network)
    return monkeypatch


def _check(fields):
    return asyncio.run(acra.check_entity(fields, "certificate_of_incumbency"))


def test_a_test_uen_is_found_without_calling_data_gov(register) -> None:
    register.setattr(acra.settings, "SYNTHETIC_ACRA_REGISTRY", True)
    findings, skipped = _check({
        "uen": "201657409E", "company_name": "Tidewater Marine Supplies Pte Ltd",
        "incorporation_date": "06 Nov 2016", "company_address": ENTITY["address"],
    })
    assert findings == [] and skipped == []


def test_a_forged_value_is_still_caught_against_the_record(register) -> None:
    register.setattr(acra.settings, "SYNTHETIC_ACRA_REGISTRY", True)
    findings, _ = _check({"uen": "201657409E", "company_name": "Tidewater Marin Suplies Pte Ltd",
                          "incorporation_date": "06 Nov 2006"})
    assert {f.check_code for f in findings} == {"REG.ENTITY_NAME", "REG.INCORPORATION_DATE"}


def test_off_by_default_the_register_is_not_read(register) -> None:
    register.setattr(acra.settings, "SYNTHETIC_ACRA_REGISTRY", False)
    with pytest.raises(AssertionError, match="data.gov.sg was called"):
        _check({"uen": "201657409E"})


def test_the_built_register_holds_every_test_company() -> None:
    synthetic_acra._entities.cache_clear()
    entities = synthetic_acra._entities()
    assert len(entities) >= 30
    assert synthetic_acra.lookup("2016-57409E")["name"] == "TIDEWATER MARINE SUPPLIES PTE LTD"
