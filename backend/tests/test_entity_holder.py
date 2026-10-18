"""A statement's or bill's holder is read as an individual or a corporate, and read that way everywhere."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.case_pipeline.cross_document.address_matching import find_address_mismatches
from app.fraud_detection.external_validation.registry_lookups.runner import verify_against_registries
from app.fraud_detection.font_consistency import ExtractedField
from app.holder import holder_kind, looks_corporate
from app.langgraph.extract import _apply_entity_fields
from app.langgraph.extraction_prompts import build_extraction_prompt
from app.models import DocumentRecord, DocumentStatus, compute_fulfilled_requirements, is_personal_bank_statement


def _result(**fields) -> dict:
    return {
        "extracted_fields": {k: v for k, v in fields.items()},
        "field_confidences": {k: 0.95 for k in fields},
        "field_reasons": {},
        "confidence": 0.95,
    }


@pytest.mark.parametrize("name, expected", [
    ("BRIGHTWAVE ANALYTICS PTE LTD", True), ("Acme Holdings Limited", True), ("TAN WEI MING", False), (None, False),
])
def test_company_names_are_recognised(name, expected) -> None:
    assert looks_corporate(name) is expected


def test_a_corporate_holder_is_stored_as_company_fields_not_an_individuals() -> None:
    result = _apply_entity_fields(
        _result(entity_name="BRIGHTWAVE ANALYTICS PTE LTD", entity_address="21 MEDIA CIRCLE #03-05 SINGAPORE 138562",
                entity_type="corporate", bank_name="DBS"),
        "bank_statement",
    )
    fields = result["extracted_fields"]

    assert fields["company_name"] == "BRIGHTWAVE ANALYTICS PTE LTD"
    assert fields["company_address"].startswith("21 MEDIA CIRCLE")
    assert fields["entity_type"] == "corporate"
    assert not {"individual_name", "individual_address", "entity_name", "entity_address"} & fields.keys()
    assert result["field_confidences"]["company_address"] == 0.95


def test_an_individual_holder_is_stored_as_individual_fields() -> None:
    fields = _apply_entity_fields(
        _result(entity_name="Goh Ka Yee", entity_address="27 HOUGANG AVENUE #02-13 SINGAPORE 224278", entity_type="individual"),
        "bank_statement",
    )["extracted_fields"]

    assert (fields["individual_name"], fields["entity_type"]) == ("Goh Ka Yee", "individual")
    assert "company_name" not in fields


def test_a_type_that_contradicts_the_name_becomes_corporate_at_low_confidence() -> None:
    result = _apply_entity_fields(_result(entity_name="ACME TRADING PTE LTD", entity_type="individual"), "bank_statement")

    assert result["extracted_fields"]["entity_type"] == "corporate"
    assert result["field_confidences"]["entity_type"] < 0.9
    assert "company's" in result["field_reasons"]["entity_type"]


def test_a_missing_type_is_taken_from_the_name_at_low_confidence() -> None:
    result = _apply_entity_fields(_result(entity_name="Goh Ka Yee"), "proof_of_address")

    assert result["extracted_fields"]["entity_type"] == "individual"
    assert result["field_confidences"]["entity_type"] < 0.9


def test_other_document_types_are_left_alone() -> None:
    result = _result(entity_name="X", company_name="Y")

    assert _apply_entity_fields(result, "payslip")["extracted_fields"] == {"entity_name": "X", "company_name": "Y"}


def test_holder_kind_reads_the_stored_type_and_older_statements() -> None:
    assert holder_kind("bank_statement", {"entity_type": "corporate", "individual_name": "X"}) == "corporate"
    assert holder_kind("bank_statement", {"company_name": "A Pte Ltd", "individual_address": "1 Road"}) == "corporate"
    assert holder_kind("bank_statement", {"individual_name": "Goh"}) == "individual"
    assert holder_kind("bank_statement", {}) is None
    assert is_personal_bank_statement("bank_statement", {"entity_type": "individual"})
    assert not is_personal_bank_statement("bank_statement", {"entity_type": "corporate", "individual_name": "X"})


def test_a_corporate_bill_does_not_fulfil_personal_proof_of_address() -> None:
    fulfilled = compute_fulfilled_requirements(
        "proof_of_address", {"entity_type": "corporate", "company_name": "A Pte Ltd", "company_address": "1 Road"})

    assert "proof_of_address" not in fulfilled and "company_proof_of_address" in fulfilled


def test_the_prompt_asks_for_the_holder_not_the_old_split() -> None:
    for document_type in ("bank_statement", "proof_of_address"):
        prompt = build_extraction_prompt(document_type, "text")
        assert all(name in prompt for name in ("entity_name", "entity_address", "entity_type"))
        assert "individual_address" not in prompt


def _field(name: str, value: str) -> ExtractedField:
    return ExtractedField(field_name=name, field_type="text", field_value=value, confidence=0.95)


_MEDIA_CIRCLE = [{"BLK_NO": "21", "ROAD_NAME": "MEDIA CIRCLE", "ADDRESS": "21 MEDIA CIRCLE SINGAPORE 138562", "POSTAL": "138562"}]


async def _building_use_findings(fields: list[ExtractedField]) -> list:
    with (
        patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code",
              AsyncMock(return_value=_MEDIA_CIRCLE)),
        patch("app.fraud_detection.external_validation.registry_lookups.addresses._building_use",
              AsyncMock(return_value=([], []))) as building_use,
    ):
        await verify_against_registries("d", fields, "bank_statement")
    return building_use.await_args.args[0:1] if building_use.await_args else []


async def test_an_older_corporate_statement_address_is_not_checked_as_a_home() -> None:
    [address] = await _building_use_findings([
        _field("company_name", "BRIGHTWAVE ANALYTICS PTE LTD"),
        _field("individual_address", "21 MEDIA CIRCLE #03-05 SINGAPORE 138562"),
    ])

    assert address.company and not address.residential


async def test_a_personal_statement_address_is_still_checked_as_a_home() -> None:
    [address] = await _building_use_findings([
        _field("individual_name", "Goh Ka Yee"),
        _field("individual_address", "21 MEDIA CIRCLE #03-05 SINGAPORE 138562"),
    ])

    assert address.residential and not address.company


def _document(doc_id: str, fields: dict, document_type: str) -> DocumentRecord:
    return DocumentRecord(
        id=doc_id, case_id="c", filename=f"{doc_id}.pdf", document_type=document_type, status=DocumentStatus.ANALYZED,
        uploaded_at=datetime(2026, 10, 14, tzinfo=timezone.utc), extracted_fields=fields,
    )


def test_a_corporate_statements_address_is_compared_with_the_companys_other_addresses() -> None:
    constitution = _document("coi", {
        "company_name": "Acme Pte Ltd", "company_address": "10 Bedok North, Singapore 460022",
    }, "company_constitution")
    statement = _document("statement", {
        "entity_type": "corporate", "company_name": "Acme Pte Ltd", "company_address": "10 Bedok North, Singapore 460023",
    }, "bank_statement")
    older_statement = _document("older", {
        "company_name": "Acme Pte Ltd", "individual_address": "10 Bedok North, Singapore 460024",
    }, "bank_statement")

    assert len(find_address_mismatches([constitution, statement])) == 1
    assert len(find_address_mismatches([constitution, older_statement])) == 1
