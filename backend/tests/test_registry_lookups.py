"""Registry lookups with the HTTP layer mocked: no test calls a live service."""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.fraud_detection.font_consistency import ExtractedField
from app.fraud_detection.external_validation.registry_lookups import verify_against_registries
from app.fraud_detection.external_validation.registry_lookups.datagov import Collection
from app.fraud_detection.external_validation.registry_lookups.hdb import same_street
from app.fraud_detection.external_validation.registry_lookups.http import LookupUnavailable

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
    with patch("app.fraud_detection.external_validation.registry_lookups.http.httpx.AsyncClient",
               side_effect=AssertionError("test tried to call a live registry")):
        yield


# The catalogue as data.gov.sg returned it (collection 1, and the collection 2 datasets used below).
_ACRA_UEN, _OTHER_UEN = "d_3f960c10fed6145404ca7b821f263b87", "d_b1d2b840ab9e993570c037b706b39bb8"
_DETAIL_O, _DETAIL_M = "d_5c4ef48b025fdfbc80056401f06e3df9", "d_9af9317c646a1c881bb5591c91817cc6"
_DATASET_NAMES = {
    _ACRA_UEN: "Entities Registered with ACRA",
    _OTHER_UEN: "Entities Registered with Other UEN Issuance Agencies",
    _DETAIL_O: "ACRA Information on Corporate Entities ('O')",
    _DETAIL_M: "ACRA Information on Corporate Entities ('M')",
}


def _catalogue(last_updated: datetime):
    collections = {1: Collection((_OTHER_UEN, _ACRA_UEN), last_updated), 2: Collection((_DETAIL_M, _DETAIL_O), last_updated)}

    async def collection(collection_id):
        return collections[collection_id]

    async def dataset_name(dataset_id):
        return _DATASET_NAMES[dataset_id]

    return (patch("app.fraud_detection.external_validation.registry_lookups.acra.collection", side_effect=collection),
            patch("app.fraud_detection.external_validation.registry_lookups.acra.dataset_name", side_effect=dataset_name))


@pytest.fixture(autouse=True)
def no_hdb_blocks():
    """An empty HDB register unless a test supplies blocks: every address is a private building."""
    with patch("app.fraud_detection.external_validation.registry_lookups.hdb.datastore_search", AsyncMock(return_value=[])):
        yield


@pytest.fixture(autouse=True)
def fresh_catalogue():
    first, second = _catalogue(datetime.now(timezone.utc) - timedelta(days=10))
    with first, second:
        yield


def _field(name: str, value) -> ExtractedField:
    return ExtractedField(field_name=name, field_type="string", field_value=value, confidence=0.99)


def _datastore(records_by_dataset: dict[str, list[dict]]):
    async def search(resource_id, filters, limit=10):
        return records_by_dataset.get(resource_id, [])
    return search


def _codes(result, code: str) -> list:
    return [f for f in result.findings if f.check_code == code]


def _skipped_codes(result, code: str) -> list:
    return [check for check in result.skipped_checks if check.check_code == code]


@pytest.fixture
def ocbc_registry():
    datasets = {"d_3f960c10fed6145404ca7b821f263b87": [_OCBC_BASIC], "d_5c4ef48b025fdfbc80056401f06e3df9": [_OCBC_DETAIL]}
    with patch("app.fraud_detection.external_validation.registry_lookups.acra.datastore_search", side_effect=_datastore(datasets)):
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
    with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code", AsyncMock(return_value=raffles)):
        result = await verify_against_registries("d", [
            _field("uen", "193200032W"), _field("company_address", "1 Raffles Place, Singapore 048616"),
        ], "certificate_of_incumbency")
    assert _codes(result, "REG.REGISTERED_ADDRESS")[0].severity == "inconsistent"
    assert _codes(result, "REG.ADDRESS_MATCH") == []


async def test_acra_flags_a_struck_off_company() -> None:
    basic = {"uen": "201201936C", "uen_status_desc": "Deregistered", "entity_name": "MILLENNIUM AUTOMATION & SYSTEMS PTE. LTD."}
    detail = {**basic, "entity_status_description": "Struck Off"}
    datasets = {"d_3f960c10fed6145404ca7b821f263b87": [basic], "d_9af9317c646a1c881bb5591c91817cc6": [detail]}
    with patch("app.fraud_detection.external_validation.registry_lookups.acra.datastore_search", side_effect=_datastore(datasets)):
        result = await verify_against_registries("d", [_field("uen", "201201936C")], "certificate_of_incorporation")
    assert _codes(result, "REG.ENTITY_STATUS")[0].severity == "impossible"


async def test_acra_flags_a_uen_that_does_not_exist() -> None:
    with patch("app.fraud_detection.external_validation.registry_lookups.acra.datastore_search", side_effect=_datastore({})):
        result = await verify_against_registries("d", [_field("uen", "209912345Z")], "certificate_of_incorporation")
    assert _codes(result, "REG.UEN_EXISTS")[0].severity == "impossible"


async def test_acra_does_not_report_a_missing_uen_against_a_stale_register() -> None:
    first, second = _catalogue(datetime.now(timezone.utc) - timedelta(days=90))
    with first, second, patch("app.fraud_detection.external_validation.registry_lookups.acra.datastore_search", side_effect=_datastore({})):
        result = await verify_against_registries("d", [_field("uen", "209912345Z")], "certificate_of_incorporation")
    assert _codes(result, "REG.UEN_EXISTS") == []
    assert any(s.check_code == "REG.UEN_EXISTS" and "last updated" in s.reason for s in result.skipped_checks)


async def test_acra_finds_datasets_from_the_catalogue_whatever_their_order() -> None:
    # The catalogue lists the "other agencies" dataset first; ACRA's own must still be searched first,
    # and the detail record found by its ('O') name, not by a fixed ID.
    searched = []

    async def search(resource_id, filters, limit=10):
        searched.append(resource_id)
        return {_ACRA_UEN: [_OCBC_BASIC], _DETAIL_O: [_OCBC_DETAIL]}.get(resource_id, [])

    with patch("app.fraud_detection.external_validation.registry_lookups.acra.datastore_search", side_effect=search):
        result = await verify_against_registries("d", [_field("uen", "193200032W"),
                                                       _field("incorporation_date", "1932-10-31")], "certificate_of_incorporation")
    assert searched == [_ACRA_UEN, _DETAIL_O]
    assert result.findings == []


async def test_rate_limited_registry_is_a_skip_not_a_finding() -> None:
    with patch("app.fraud_detection.external_validation.registry_lookups.acra.datastore_search",
               AsyncMock(side_effect=LookupUnavailable("rate limited"))):
        result = await verify_against_registries("d", [_field("uen", "193200032W")], "certificate_of_incorporation")
    assert result.findings == []
    assert any("rate limited" in s.reason for s in result.skipped_checks)


async def test_a_passport_has_nothing_to_look_up() -> None:
    # No UEN and no address on a passport: no lookup runs (no_network would fail one), and the
    # detection tool reports it as nothing to check, not as a check that couldn't run.
    from app.langgraph.detection.tools import _result

    result = await verify_against_registries("d", [_field("passport_number", "K5690850D")], "passport")
    assert result.findings == [] and result.skipped_checks == []
    tool_output = _result("registry_tool", [], unable=result.unable_to_assess_reason)
    assert tool_output["nothing_to_check"] and not tool_output["unable_to_assess"]


# --- addresses -----------------------------------------------------------------------------------

_HOUGANG_207 = [{"BLK_NO": "207", "ROAD_NAME": "HOUGANG STREET 21", "BUILDING": "PAYA LEBAR KOVAN COMMUNITY CLUB",
                 "ADDRESS": "207 HOUGANG STREET 21 PAYA LEBAR KOVAN COMMUNITY CLUB SINGAPORE 530207", "POSTAL": "530207"}]


async def test_address_flags_a_street_that_is_not_at_the_postal_code() -> None:
    # The C028 credit report's address: 530207 is in Hougang, not Jurong West.
    with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code", AsyncMock(return_value=_HOUGANG_207)):
        result = await verify_against_registries("d", [_field("address", "34 Jurong West Avenue, #11-80, Singapore 530207")], "credit_report")
    assert len(_codes(result, "REG.ADDRESS_MATCH")) == 1


async def test_a_payslip_checks_its_address_but_never_looks_up_acra() -> None:
    with patch("app.fraud_detection.external_validation.registry_lookups.acra.datastore_search",
               AsyncMock(side_effect=AssertionError("a payslip has no UEN to look up"))), \
         patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code", AsyncMock(return_value=_HOUGANG_207)):
        result = await verify_against_registries(
            "d", [_field("company_address", "207 Hougang Street 21, Singapore 530207")], "payslip")
    assert result.findings == [] and result.skipped_checks == [] and not result.unable_to_assess


async def test_address_flags_a_postal_code_that_does_not_exist() -> None:
    with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code", AsyncMock(return_value=[])):
        result = await verify_against_registries("d", [_field("individual_address", "1 Nowhere Road Singapore 999999")], "proof_of_address")
    assert _codes(result, "REG.POSTAL_CODE_EXISTS")[0].severity == "impossible"


async def test_address_flags_a_home_address_at_a_non_residential_hdb_block() -> None:
    hdb_row = [{"blk_no": "207", "street": "HOUGANG ST 21", "residential": "N", "commercial": "Y"}]
    with (
        patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code", AsyncMock(return_value=_HOUGANG_207)),
        patch("app.fraud_detection.external_validation.registry_lookups.hdb.datastore_search", AsyncMock(return_value=hdb_row)),
    ):
        result = await verify_against_registries("d", [_field("individual_address", "Blk 207 Hougang Street 21 Singapore 530207")], "proof_of_address")
    assert len(_codes(result, "REG.RESIDENTIAL_ADDRESS")) == 1


async def test_company_address_at_a_commercial_block_is_not_flagged_as_non_residential() -> None:
    with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code", AsyncMock(return_value=_HOUGANG_207)):
        result = await verify_against_registries("d", [_field("company_address", "207 Hougang Street 21 Singapore 530207")], "payslip")
    assert result.findings == []


def test_hdb_street_abbreviations_match_onemap_road_names() -> None:
    assert same_street("HOUGANG ST 21", "HOUGANG STREET 21")
    assert same_street("C'WEALTH CRES", "COMMONWEALTH CRESCENT")
    assert same_street("BEDOK STH AVE 1", "BEDOK SOUTH AVENUE 1")
    assert not same_street("BEDOK NTH AVE 1", "BEDOK SOUTH AVENUE 1")


_BEDOK_22 = [{"BLK_NO": "22", "ROAD_NAME": "BEDOK SOUTH AVENUE 1", "BUILDING": "NIL",
              "ADDRESS": "22 BEDOK SOUTH AVENUE 1 SINGAPORE 460022", "POSTAL": "460022"}]


async def test_address_needs_the_whole_road_name_not_just_the_block_and_town() -> None:
    # Regression: the NRIC_8823 back gives "22 Bedok North Avenue 3", but 460022 is 22 Bedok South Avenue 1.
    # A matching block number and the word "Bedok" used to be enough.
    with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code", AsyncMock(return_value=_BEDOK_22)):
        wrong_road = await verify_against_registries(
            "d", [_field("individual_address", "22 BEDOK NORTH AVENUE 3 #10-12 SINGAPORE 460022")], "nric")
        wrong_block = await verify_against_registries(
            "d", [_field("company_address", "23 Bedok South Avenue 1 Singapore 460022")], "payslip")
        abbreviated = await verify_against_registries(
            "d", [_field("company_address", "Blk 22 Bedok Sth Ave 1 #03-11 S460022")], "payslip")
    assert len(_codes(wrong_road, "REG.ADDRESS_MATCH")) == 1
    assert len(_codes(wrong_block, "REG.ADDRESS_MATCH")) == 1
    assert abbreviated.findings == []


async def test_addresses_in_a_table_are_checked_once_however_often_they_are_printed() -> None:
    # The NRIC_8823 shareholder's address, printed three ways across the constitution's fields.
    fields = [
        _field("shareholders", [{"name": "NG BEE LENG", "address": "22 BEDOK NORTH AVENUE 3, #10-12, SINGAPORE 460022"}]),
        _field("subscriber_2_address", "22 Bedok North Avenue 3, #10-12, Singapore 460022"),
        _field("individual_address", ["Blk 22 Bedok Nth Ave 3 #10-12 S460022"]),
    ]
    lookup = AsyncMock(return_value=_BEDOK_22)
    with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code", lookup):
        result = await verify_against_registries("d", fields, "company_constitution")
    findings = _codes(result, "REG.ADDRESS_MATCH")
    assert len(findings) == 1 and lookup.await_count == 1
    assert findings[0].field == "subscriber_2_address"
    assert findings[0].summary.startswith("Subscriber 2 Address gives")
    assert "Also printed in Shareholder 1 (NG BEE LENG), Address and Individual Address 1" in findings[0].summary



# --- building use: HDB blocks and URA zoning -------------------------------------------------------

def _onemap(blk: str, road: str, postal: str, building: str = "NIL"):
    return AsyncMock(return_value=[{"BLK_NO": blk, "ROAD_NAME": road, "BUILDING": building, "POSTAL": postal,
                                    "ADDRESS": f"{blk} {road} SINGAPORE {postal}",
                                    "LATITUDE": "1.33", "LONGITUDE": "103.70"}])


def _zone(zone: str | None):
    return patch("app.fraud_detection.external_validation.registry_lookups.land_use.zone_at", AsyncMock(return_value=zone))


async def test_a_home_address_in_an_industrial_zone_is_inconsistent() -> None:
    # Tradehub 21 (18 Boon Lay Way) is zoned Business 1: URA's Written Statement lists no homes there.
    with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code",
               _onemap("18", "BOON LAY WAY", "609966", "TRADEHUB 21")), _zone("BUSINESS 1"):
        result = await verify_against_registries(
            "d", [_field("individual_address", "18 Boon Lay Way #06-140 Singapore 609966")], "proof_of_address")
    finding = _codes(result, "REG.RESIDENTIAL_ADDRESS")[0]
    assert finding.severity == "inconsistent" and "Business 1" in finding.summary


async def test_a_home_address_in_a_commercial_or_white_zone_is_not_flagged() -> None:
    # Homes are allowed in Commercial (mixed use) and White zones: The Sail condo is zoned White.
    for zone in ("COMMERCIAL", "WHITE", "RESIDENTIAL"):
        with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code",
                   _onemap("2", "MARINA BOULEVARD", "018987", "THE SAIL @ MARINA BAY")), _zone(zone):
            result = await verify_against_registries(
                "d", [_field("individual_address", "2 Marina Boulevard #40-01 Singapore 018987")], "nric")
        assert result.findings == [], zone


async def test_a_company_registered_at_a_private_home_is_a_signal() -> None:
    with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code",
               _onemap("4", "SIGLAP AVENUE", "456264", "FRANKEL ESTATE")), _zone("RESIDENTIAL"):
        result = await verify_against_registries(
            "d", [_field("company_address", "4 Siglap Avenue Singapore 456264")], "certificate_of_incumbency")
    finding = _codes(result, "REG.COMPANY_ADDRESS_RESIDENTIAL")[0]
    assert finding.severity == "signal" and "Home Office Scheme" in (finding.severity_note or "")


async def test_a_company_at_an_hdb_flat_is_a_signal_but_not_at_an_hdb_shop_block() -> None:
    def run(residential: str, commercial: str):
        row = [{"blk_no": "22", "street": "BEDOK STH AVE 1", "residential": residential, "commercial": commercial}]
        return (patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code",
                      _onemap("22", "BEDOK SOUTH AVENUE 1", "460022")),
                patch("app.fraud_detection.external_validation.registry_lookups.hdb.datastore_search", AsyncMock(return_value=row)))
    fields = [_field("company_address", "Blk 22 Bedok South Avenue 1 #03-11 Singapore 460022")]
    first, second = run("Y", "N")
    with first, second:
        flat = await verify_against_registries("d", fields, "certificate_of_incumbency")
    first, second = run("Y", "Y")
    with first, second:
        shops = await verify_against_registries("d", fields, "certificate_of_incumbency")
    assert len(_codes(flat, "REG.COMPANY_ADDRESS_RESIDENTIAL")) == 1
    assert shops.findings == []


async def test_zoning_unavailable_is_a_skip_not_a_finding() -> None:
    # conftest: no land-use index in tests, so the zone lookup reports itself unavailable.
    with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code",
               _onemap("18", "BOON LAY WAY", "609966", "TRADEHUB 21")):
        result = await verify_against_registries(
            "d", [_field("individual_address", "18 Boon Lay Way #06-140 Singapore 609966")], "proof_of_address")
    assert result.findings == []
    assert any(s.check_code == "REG.BUILDING_USE" for s in result.skipped_checks)


def test_land_use_index_finds_the_zone_containing_a_point() -> None:
    from app.fraud_detection.external_validation.registry_lookups.land_use import build_index

    square = [[[103.0, 1.0], [103.2, 1.0], [103.2, 1.2], [103.0, 1.2], [103.0, 1.0]]]
    with_hole = [[[104.0, 1.0], [104.4, 1.0], [104.4, 1.4], [104.0, 1.4], [104.0, 1.0]],
                 [[104.1, 1.1], [104.3, 1.1], [104.3, 1.3], [104.1, 1.3], [104.1, 1.1]]]
    geojson = {"features": [
        {"geometry": {"type": "Polygon", "coordinates": square}, "properties": {"LU_DESC": "BUSINESS 2"}},
        {"geometry": {"type": "MultiPolygon", "coordinates": [with_hole]}, "properties": {"LU_DESC": "RESIDENTIAL"}},
    ]}
    index = build_index(geojson, "2025-12-26")
    assert index.zones_at(103.1, 1.1) == ["BUSINESS 2"]
    assert index.zones_at(104.05, 1.05) == ["RESIDENTIAL"]
    assert index.zones_at(104.2, 1.2) == []  # inside the hole
    assert index.zones_at(105.0, 1.0) == []


# Imported at collection, before conftest swaps current_index out for every test.
from app.fraud_detection.external_validation.registry_lookups.land_use import current_index as _real_current_index  # noqa: E402


async def test_a_deployment_never_builds_the_land_use_index_itself(monkeypatch) -> None:
    from app.fraud_detection.external_validation.registry_lookups import land_use
    from app.config import settings

    built = []
    monkeypatch.setattr(settings, "LAND_USE_RUNTIME_REBUILD", False)
    monkeypatch.setattr(land_use, "_build_in_background", lambda published: built.append(published))
    monkeypatch.setattr(land_use, "_state", {"index": None, "loaded": False, "building": False, "checked_at": None})

    monkeypatch.setattr(land_use, "load_index", lambda: None)
    with pytest.raises(LookupUnavailable, match="isn't included in this deployment"):
        await _real_current_index()

    shipped = land_use.build_index({"features": []}, "2025-12-26")
    monkeypatch.setattr(land_use, "_state", {"index": None, "loaded": False, "building": False, "checked_at": None})
    monkeypatch.setattr(land_use, "load_index", lambda: shipped)
    assert await _real_current_index() is shipped
    assert built == []  # no download or build was started either time


def _ura(answer: bool):
    return patch("app.fraud_detection.external_validation.registry_lookups.ura.approved_residential_use", AsyncMock(return_value=answer))


@pytest.fixture
def ura_key(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "URA_API_KEY", "test-key")


async def test_a_home_address_at_an_office_unit_is_implausible(ura_key) -> None:
    # MBFC Tower 3 is zoned White, like The Sail condo: only URA's approved use tells the office unit apart.
    with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code",
               _onemap("12", "MARINA BOULEVARD", "018982", "MARINA BAY FINANCIAL CENTRE (TOWER 3)")), \
            _zone("WHITE"), _ura(False):
        result = await verify_against_registries(
            "d", [_field("individual_address", "12 Marina Boulevard #17-01 Singapore 018982")], "proof_of_address")
    finding = _codes(result, "REG.RESIDENTIAL_ADDRESS")[0]
    assert finding.severity == "implausible" and "#17-01" in finding.summary


async def test_an_approved_condo_unit_or_a_shophouse_home_is_not_flagged(ura_key) -> None:
    cases = [("WHITE", True, "2 Marina Boulevard #40-01 Singapore 018987"),
             ("RESIDENTIAL WITH COMMERCIAL AT 1ST STOREY", False, "2 Marina Boulevard #02-01 Singapore 018987"),
             ("RESIDENTIAL WITH COMMERCIAL AT 1ST STOREY", False, "2 Marina Boulevard Singapore 018987")]
    for zone, approved, text in cases:
        with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code",
                   _onemap("2", "MARINA BOULEVARD", "018987")), _zone(zone), _ura(approved):
            result = await verify_against_registries("d", [_field("individual_address", text)], "nric")
        assert result.findings == [], (zone, text)


async def test_an_unknown_unit_in_a_residential_zone_is_only_a_signal(ura_key) -> None:
    with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code",
               _onemap("204", "OCEAN DRIVE", "098628")), _zone("RESIDENTIAL"), _ura(False):
        result = await verify_against_registries(
            "d", [_field("individual_address", "204 Ocean Drive #70-01 Singapore 098628")], "nric")
    assert _codes(result, "REG.RESIDENTIAL_ADDRESS")[0].severity == "signal"


async def test_a_company_at_an_approved_condo_unit_is_a_signal_even_in_a_white_zone(ura_key) -> None:
    with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code",
               _onemap("2", "MARINA BOULEVARD", "018987")), _zone("WHITE"), _ura(True):
        result = await verify_against_registries(
            "d", [_field("company_address", "2 Marina Boulevard #40-01 Singapore 018987")], "certificate_of_incumbency")
    finding = _codes(result, "REG.COMPANY_ADDRESS_RESIDENTIAL")[0]
    assert finding.source == "URA Approved Residential Use" and finding.severity == "signal"


def test_ura_unit_numbers_are_read_as_singapore_prints_them() -> None:
    from app.fraud_detection.external_validation.registry_lookups.ura import unit_of

    assert unit_of("2 Marina Boulevard #40-01 The Sail") == ("40", "01")
    assert unit_of("Blk 2 #4-1") == ("04", "01")
    assert unit_of("10 Jalan Besar #B1-12A") == ("B1", "12A")
    assert unit_of("4 Siglap Avenue South") is None


@pytest.mark.parametrize("address, unit", [
    ("Unit 05-01, 2 Marina Boulevard", ("05", "01")),
    ("2 Marina Boulevard Unit #05-01", ("05", "01")),
    ("Blk 123 Bedok North Road 05-01 Singapore 460123", ("05", "01")),
    ("123 Bedok North Road, 05-01, Singapore 460123", ("05", "01")),
    ("10 Jalan Besar B1-12 Singapore 208787", ("B1", "12")),
    ("2 Marina Boulevard #05 01 Singapore 018987", ("05", "01")),
    ("2 Marina Boulevard Level 5 Unit 1 Singapore 018987", ("05", "01")),
    ("2 Marina Boulevard Floor 05, #01 Singapore 018987", ("05", "01")),
    ("2 Marina Boulevard L5-01 Singapore 018987", ("05", "01")),
    ("2 Marina Boulevard #05\u201301 Singapore 018987", ("05", "01")),
])
def test_the_common_singapore_unit_forms_are_read(address, unit) -> None:
    from app.fraud_detection.external_validation.registry_lookups.ura import unit_of

    assert unit_of(address) == unit


@pytest.mark.parametrize("address", [
    "10-12 Bedok Road Singapore 460010",  # a house number range
    "Blk 10-12 Bedok Road Singapore 460010",
    "2 Marina Boulevard #05 Singapore 018987",  # the postal code is not the unit
    "2 Marina Boulevard Tel 6123-4567",
    "2 Marina Boulevard 12-01-2024",
    "4 Siglap Avenue South Singapore 456264",
])
def test_numbers_that_are_not_a_unit_are_not_read_as_one(address) -> None:
    from app.fraud_detection.external_validation.registry_lookups.ura import unit_of

    assert unit_of(address) is None


async def test_a_unit_that_cannot_be_read_is_not_reported_as_absent(ura_key) -> None:
    result = await _home("2 Marina Boulevard Unit 7 Singapore 018987", "WHITE", approved=False)
    [skip] = _skipped_codes(result, "REG.UNIT_MISSING")
    assert "couldn't be read" in skip.reason and "no unit number" not in skip.reason


async def test_a_missing_unit_still_says_none_was_provided(ura_key) -> None:
    result = await _home("2 Marina Boulevard Singapore 018987", "WHITE", approved=False)
    [skip] = _skipped_codes(result, "REG.UNIT_MISSING")
    assert "no unit number was provided" in skip.reason


@pytest.mark.parametrize("fields", [
    {"individual_address": ["2 Marina Boulevard", "#40-01", "Singapore 018987"]},
    {"individual_address": '["2 Marina Boulevard", "#40-01", "Singapore 018987"]'},
    {"individual_address_lines": ["2 Marina Boulevard", "#40-01", "Singapore 018987"]},
])
def test_an_address_split_over_lines_keeps_its_unit(fields) -> None:
    from app.fraud_detection.external_validation.registry_lookups.addresses import _addresses
    from app.fraud_detection.external_validation.registry_lookups.ura import unit_of

    [address] = _addresses(fields, "nric")
    assert unit_of(address.text) == ("40", "01") and address.field == "individual_address"


def test_a_list_of_several_addresses_stays_separate() -> None:
    from app.fraud_detection.external_validation.registry_lookups.addresses import _addresses

    found = _addresses({"individual_address": ["2 Marina Boulevard #40-01 Singapore 018987", "22 Bedok South Avenue 1 #14-11 Singapore 460022"]}, "nric")
    assert len(found) == 2



async def test_a_home_address_without_a_unit_cannot_be_checked_at_unit_level(ura_key) -> None:
    # Without a unit, URA cannot identify which dwelling to check; do not infer fraud from the omission.
    with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code",
               _onemap("65", "CHULIA STREET", "049513", "OCBC CENTRE")), _zone("COMMERCIAL"), _ura(False):
        result = await verify_against_registries(
            "d", [_field("individual_address", "65 Chulia Street OCBC Centre Singapore 049513")], "nric")
    assert _codes(result, "REG.UNIT_MISSING") == []
    assert len(_skipped_codes(result, "REG.UNIT_MISSING")) == 1


async def test_an_approved_landed_house_needs_no_unit(ura_key) -> None:
    with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code",
               _onemap("4", "SIGLAP AVENUE SOUTH", "456264")), _zone("RESIDENTIAL"), _ura(True):
        result = await verify_against_registries(
            "d", [_field("individual_address", "4 Siglap Avenue South Singapore 456264")], "nric")
    assert result.findings == []


async def test_hdb_units_are_checked_against_the_blocks_storeys_but_missing_unit_is_unverified() -> None:
    row = [{"blk_no": "22", "street": "BEDOK STH AVE 1", "residential": "Y", "commercial": "N", "max_floor_lvl": "14"}]

    async def run(text: str):
        with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code",
                   _onemap("22", "BEDOK SOUTH AVENUE 1", "460022")),                 patch("app.fraud_detection.external_validation.registry_lookups.hdb.datastore_search", AsyncMock(return_value=row)):
            return await verify_against_registries("d", [_field("individual_address", text)], "nric")

    assert (await run("Blk 22 Bedok South Avenue 1 #14-11 Singapore 460022")).findings == []
    too_high = _codes(await run("Blk 22 Bedok South Avenue 1 #15-11 Singapore 460022"), "REG.UNIT_STOREY")
    assert [f.severity for f in too_high] == ["impossible"] and "14 storeys" in too_high[0].summary
    missing_unit = await run("Blk 22 Bedok South Avenue 1 Singapore 460022")
    assert _codes(missing_unit, "REG.UNIT_MISSING") == []
    assert len(_skipped_codes(missing_unit, "REG.UNIT_MISSING")) == 1


# --- URA approved use x zone: each zone class, with and without a unit -------------------------------------------

_CONDO = _onemap("2", "MARINA BOULEVARD", "018987", "THE SAIL @ MARINA BAY")


async def _home(text: str, zone: str | None, approved: bool, doc_type: str = "nric"):
    with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code", _CONDO), \
            _zone(zone), _ura(approved):
        return await verify_against_registries("d", [_field("individual_address", text)], doc_type)


@pytest.mark.parametrize("zone, severity", [
    ("WHITE", "implausible"), ("COMMERCIAL", "implausible"), ("HOTEL", "implausible"),
    ("COMMERCIAL & RESIDENTIAL", "implausible"), ("BUSINESS PARK", "implausible"),
    ("RESIDENTIAL", "signal"), ("RESIDENTIAL / INSTITUTION", "signal"),
    (None, "signal"),
])
async def test_a_unit_ura_does_not_approve_is_rated_by_its_zone(ura_key, zone, severity) -> None:
    result = await _home("2 Marina Boulevard #70-01 Singapore 018987", zone, approved=False)
    finding = _codes(result, "REG.RESIDENTIAL_ADDRESS")[0]
    assert finding.severity == severity, zone


async def test_an_unknown_zone_never_reads_as_a_non_home_unit(ura_key) -> None:
    # Regression: with no zone (no index, or the point is in no polygon), "implausible" was reported.
    result = await _home("2 Marina Boulevard #70-01 Singapore 018987", None, approved=False)
    finding = _codes(result, "REG.RESIDENTIAL_ADDRESS")[0]
    assert finding.severity == "signal" and "zoning wasn't available" in finding.summary


async def test_a_missing_land_use_index_gives_a_signal_and_a_skip_not_an_implausible_finding(ura_key) -> None:
    # No zone patch: conftest makes the land-use index unavailable, as on a deployment built without it.
    with patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code", _CONDO), _ura(False):
        result = await verify_against_registries(
            "d", [_field("individual_address", "2 Marina Boulevard #70-01 Singapore 018987")], "nric")
    assert [f.severity for f in _codes(result, "REG.RESIDENTIAL_ADDRESS")] == ["signal"]
    assert any(s.check_code == "REG.BUILDING_USE" for s in result.skipped_checks)


@pytest.mark.parametrize("zone", ["RESIDENTIAL WITH COMMERCIAL AT 1ST STOREY"])
async def test_a_shophouse_zone_is_exempt_with_or_without_a_unit(ura_key, zone) -> None:
    for text in ("2 Marina Boulevard #02-01 Singapore 018987", "2 Marina Boulevard Singapore 018987"):
        assert (await _home(text, zone, approved=False)).findings == [], text


async def test_a_home_address_in_an_industrial_zone_is_inconsistent_whatever_ura_says(ura_key) -> None:
    for approved in (True, False):
        result = await _home("2 Marina Boulevard #02-01 Singapore 018987", "BUSINESS 2", approved=approved)
        assert [f.severity for f in _codes(result, "REG.RESIDENTIAL_ADDRESS")] == ["inconsistent"], approved


@pytest.mark.parametrize("zone", [None, "RESIDENTIAL", "WHITE", "COMMERCIAL"])
async def test_a_home_with_no_unit_at_a_building_ura_does_not_approve_is_unverified(ura_key, zone) -> None:
    result = await _home("2 Marina Boulevard Singapore 018987", zone, approved=False)
    assert _codes(result, "REG.UNIT_MISSING") == [], zone
    assert len(_skipped_codes(result, "REG.UNIT_MISSING")) == 1, zone
    assert _codes(result, "REG.RESIDENTIAL_ADDRESS") == []


async def test_an_approved_home_is_never_flagged_in_any_zone(ura_key) -> None:
    for zone in (None, "RESIDENTIAL", "WHITE", "COMMERCIAL", "HOTEL"):
        result = await _home("2 Marina Boulevard #40-01 Singapore 018987", zone, approved=True)
        assert result.findings == [], zone


# --- a company address: URA's answer outranks zoning ------------------------------------------------------------

async def _company(zone: str | None, approved: bool | None):
    """approved=None: URA gives no answer (no access key configured), so only zoning can speak."""
    with ExitStack() as stack:
        stack.enter_context(patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code", _CONDO))
        stack.enter_context(_zone(zone))
        if approved is not None:
            stack.enter_context(_ura(approved))
        return await verify_against_registries(
            "d", [_field("company_address", "2 Marina Boulevard #40-01 Singapore 018987")], "certificate_of_incumbency")


async def test_a_company_address_ura_answers_na_is_not_flagged_by_zoning(ura_key) -> None:
    # Regression: a Residential zone flagged the company even though URA said "not approved / no record".
    assert (await _company("RESIDENTIAL", approved=False)).findings == []


async def test_a_company_address_is_flagged_by_zoning_only_when_ura_gave_no_answer() -> None:
    # No ura_key fixture: URA is unconfigured, so zoning alone speaks.
    finding = _codes(await _company("RESIDENTIAL", approved=None), "REG.COMPANY_ADDRESS_RESIDENTIAL")[0]
    assert finding.severity == "signal" and finding.source == "URA Master Plan land use (data.gov.sg)"
    for zone in ("WHITE", "COMMERCIAL", None):
        assert (await _company(zone, approved=None)).findings == [], zone


async def test_a_company_at_an_approved_home_is_a_signal_in_any_zone(ura_key) -> None:
    for zone in (None, "RESIDENTIAL", "WHITE", "COMMERCIAL"):
        result = await _company(zone, approved=True)
        assert [f.severity for f in _codes(result, "REG.COMPANY_ADDRESS_RESIDENTIAL")] == ["signal"], zone
