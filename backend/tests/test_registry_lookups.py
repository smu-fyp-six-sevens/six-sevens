"""Registry lookups with the HTTP layer mocked: no test calls a live service."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.fraud_detection.font_consistency import ExtractedField
from app.fraud_detection.registry_lookups import verify_against_registries
from app.fraud_detection.registry_lookups.hdb import same_street
from app.fraud_detection.registry_lookups.http import LookupUnavailable
from app.fraud_detection.registry_lookups.mas import _licensed_name

# Real records as data.gov.sg returned them for OCBC.
_OCBC_BASIC = {"uen": "193200032W", "uen_status_desc": "Registered", "entity_name": "OVERSEA-CHINESE BANKING CORPORATION LIMITED",
               "uen_issue_date": "1932-10-31", "reg_street_name": "CHULIA STREET", "reg_postal_code": "049514"}
_OCBC_DETAIL = {"uen": "193200032W", "entity_name": "OVERSEA-CHINESE BANKING CORPORATION LIMITED",
                "entity_status_description": "Live Company", "registration_incorporation_date": "1932-10-31",
                "block": "63", "street_name": "CHULIA STREET", "building_name": "OCBC CENTRE EAST",
                "level_no": "10", "unit_no": "00", "postal_code": "049514", "former_entity_name1": "na"}


@pytest.fixture(autouse=True)
def no_network():
    """Fails any test that would reach a live registry."""
    with patch("app.fraud_detection.registry_lookups.http.httpx.AsyncClient",
               side_effect=AssertionError("test tried to call a live registry")):
        yield


def _field(name: str, value) -> ExtractedField:
    return ExtractedField(field_name=name, field_type="string", field_value=value, confidence=0.99)


def _datastore(records_by_dataset: dict[str, list[dict]]):
    async def search(resource_id, filters, limit=10):
        return records_by_dataset.get(resource_id, [])
    return search


def _codes(result, code: str) -> list:
    return [f for f in result.findings if f.check_code == code]


@pytest.fixture
def ocbc_registry():
    datasets = {"d_3f960c10fed6145404ca7b821f263b87": [_OCBC_BASIC], "d_5c4ef48b025fdfbc80056401f06e3df9": [_OCBC_DETAIL]}
    with patch("app.fraud_detection.registry_lookups.acra.datastore_search", side_effect=_datastore(datasets)):
        yield


async def test_acra_passes_a_matching_company(ocbc_registry) -> None:
    result = await verify_against_registries("d", [
        _field("uen", "193200032W"), _field("company_name", "Oversea-Chinese Banking Corporation Limited"),
        _field("incorporation_date", "31 Oct 1932"),
    ], "certificate_of_incorporation")
    assert result.findings == []


async def test_acra_flags_wrong_name_and_incorporation_date(ocbc_registry) -> None:
    result = await verify_against_registries("d", [
        _field("uen", "193200032W"), _field("company_name", "Overseas Chinese Trading Pte Ltd"),
        _field("incorporation_date", "2001-01-01"),
    ], "certificate_of_incorporation")
    assert len(_codes(result, "REG.ENTITY_NAME")) == 1
    assert _codes(result, "REG.INCORPORATION_DATE")[0].expected == "1932-10-31"


async def test_acra_flags_a_registered_address_mismatch_on_an_incumbency(ocbc_registry) -> None:
    raffles = [{"BLK_NO": "1", "ROAD_NAME": "RAFFLES PLACE", "BUILDING": "ONE RAFFLES PLACE",
                "ADDRESS": "1 RAFFLES PLACE ONE RAFFLES PLACE SINGAPORE 048616", "POSTAL": "048616"}]
    with patch("app.fraud_detection.registry_lookups.onemap.addresses_at_postal_code", AsyncMock(return_value=raffles)):
        result = await verify_against_registries("d", [
            _field("uen", "193200032W"), _field("company_address", "1 Raffles Place, Singapore 048616"),
        ], "certificate_of_incumbency")
    assert _codes(result, "REG.REGISTERED_ADDRESS")[0].severity == "inconsistent"
    assert _codes(result, "REG.ADDRESS_MATCH") == []


async def test_acra_flags_a_struck_off_company() -> None:
    basic = {"uen": "201201936C", "uen_status_desc": "Deregistered", "entity_name": "MILLENNIUM AUTOMATION & SYSTEMS PTE. LTD."}
    detail = {**basic, "entity_status_description": "Struck Off"}
    datasets = {"d_3f960c10fed6145404ca7b821f263b87": [basic], "d_9af9317c646a1c881bb5591c91817cc6": [detail]}
    with patch("app.fraud_detection.registry_lookups.acra.datastore_search", side_effect=_datastore(datasets)):
        result = await verify_against_registries("d", [_field("uen", "201201936C")], "certificate_of_incorporation")
    assert _codes(result, "REG.ENTITY_STATUS")[0].severity == "impossible"


async def test_acra_flags_a_uen_that_does_not_exist() -> None:
    with patch("app.fraud_detection.registry_lookups.acra.datastore_search", side_effect=_datastore({})):
        result = await verify_against_registries("d", [_field("uen", "209912345Z")], "certificate_of_incorporation")
    assert _codes(result, "REG.UEN_EXISTS")[0].severity == "impossible"


async def test_rate_limited_registry_is_a_skip_not_a_finding() -> None:
    with patch("app.fraud_detection.registry_lookups.acra.datastore_search",
               AsyncMock(side_effect=LookupUnavailable("rate limited"))):
        result = await verify_against_registries("d", [_field("uen", "193200032W")], "certificate_of_incorporation")
    assert result.findings == []
    assert any("rate limited" in s.reason for s in result.skipped_checks)


# --- addresses -----------------------------------------------------------------------------------

_HOUGANG_207 = [{"BLK_NO": "207", "ROAD_NAME": "HOUGANG STREET 21", "BUILDING": "PAYA LEBAR KOVAN COMMUNITY CLUB",
                 "ADDRESS": "207 HOUGANG STREET 21 PAYA LEBAR KOVAN COMMUNITY CLUB SINGAPORE 530207", "POSTAL": "530207"}]


async def test_address_flags_a_street_that_is_not_at_the_postal_code() -> None:
    # The C028 credit report's address: 530207 is in Hougang, not Jurong West.
    with patch("app.fraud_detection.registry_lookups.onemap.addresses_at_postal_code", AsyncMock(return_value=_HOUGANG_207)):
        result = await verify_against_registries("d", [_field("address", "34 Jurong West Avenue, #11-80, Singapore 530207")], "credit_report")
    assert len(_codes(result, "REG.ADDRESS_MATCH")) == 1


async def test_address_flags_a_postal_code_that_does_not_exist() -> None:
    with patch("app.fraud_detection.registry_lookups.onemap.addresses_at_postal_code", AsyncMock(return_value=[])):
        result = await verify_against_registries("d", [_field("individual_address", "1 Nowhere Road Singapore 999999")], "proof_of_address")
    assert _codes(result, "REG.POSTAL_CODE_EXISTS")[0].severity == "impossible"


async def test_address_flags_a_home_address_at_a_non_residential_hdb_block() -> None:
    hdb_row = [{"blk_no": "207", "street": "HOUGANG ST 21", "residential": "N", "commercial": "Y"}]
    with (
        patch("app.fraud_detection.registry_lookups.onemap.addresses_at_postal_code", AsyncMock(return_value=_HOUGANG_207)),
        patch("app.fraud_detection.registry_lookups.hdb.datastore_search", AsyncMock(return_value=hdb_row)),
    ):
        result = await verify_against_registries("d", [_field("individual_address", "Blk 207 Hougang Street 21 Singapore 530207")], "proof_of_address")
    assert len(_codes(result, "REG.RESIDENTIAL_ADDRESS")) == 1


async def test_company_address_at_a_commercial_block_is_not_flagged_as_non_residential() -> None:
    with patch("app.fraud_detection.registry_lookups.onemap.addresses_at_postal_code", AsyncMock(return_value=_HOUGANG_207)):
        result = await verify_against_registries("d", [_field("company_address", "207 Hougang Street 21 Singapore 530207")], "payslip")
    assert result.findings == []


def test_hdb_street_abbreviations_match_onemap_road_names() -> None:
    assert same_street("HOUGANG ST 21", "HOUGANG STREET 21")
    assert same_street("C'WEALTH CRES", "COMMONWEALTH CRESCENT")
    assert same_street("BEDOK STH AVE 1", "BEDOK SOUTH AVENUE 1")
    assert not same_street("BEDOK NTH AVE 1", "BEDOK SOUTH AVENUE 1")


# --- MAS -------------------------------------------------------------------------------------------

_MAS = ["DBS BANK LTD.", "OVERSEA-CHINESE BANKING CORPORATION LIMITED", "UNITED OVERSEAS BANK LIMITED",
        "P.T. BANK NEGARA INDONESIA (PERSERO) TBK", "HSBC BANK (SINGAPORE) LIMITED", "GREEN LINK DIGITAL BANK PTE. LTD."]


def test_mas_matches_statement_names_to_licensed_banks() -> None:
    for name in ("OCBC Bank", "POSB", "UOB (United Overseas Bank)", "Bank Negara Indonesia", "HSBC", "Green Link Digital Bank"):
        assert _licensed_name(name, _MAS), name
    for name in ("Bank", "Revolut", "Sunrise Provisions Bank"):
        assert _licensed_name(name, _MAS) is None, name


async def test_mas_signals_an_unlicensed_bank() -> None:
    with patch("app.fraud_detection.registry_lookups.mas.licensed_banks", AsyncMock(return_value=_MAS)):
        result = await verify_against_registries("d", [_field("bank_name", "Revolut")], "bank_statement")
    assert _codes(result, "REG.BANK_LICENSED")[0].severity == "signal"
