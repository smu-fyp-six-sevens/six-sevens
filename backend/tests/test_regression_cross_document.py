"""
Regression tests (SCRUM-296) for cross-document validation — "Test Cases -
Sprint 4.xlsx", Cross Doc Validation sheet:

  SCRUM-255 Company identity consistency                    CD-001 – CD-007
  SCRUM-256 Public reference checks (address, SWIFT BIC)    CD-008 – CD-011
  SCRUM-253 Cross-document personal details validation      CD-012 – CD-017
  SCRUM-254 Address matching                                CD-018 – CD-021

CD-001 – CD-007 and CD-012 – CD-021 run the case analysis's code-side
comparisons (cross_document/deterministic.py: company names, addresses,
UENs, people) over the case's documents, exactly as a case analysis run
does before the agent sees anything. These are code, not Claude: what the
officer is shown as "Documents differ" / "Identity conflict" comes from here.
The same names, IDs and addresses as the live run's test data are used.

CD-008 – CD-011 run the document's real external validation
(fraud_detection/external_validation) with only the registries' HTTP
answers faked: OneMap's and HDB's records for the test addresses, as they
returned them in the live run. The bank's SWIFT BIC comes from the bundled
reference table, so nothing is faked there.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.case_pipeline.cross_document.company_identity import check_company_name_consistency
from app.case_pipeline.cross_document.deterministic import run_deterministic_checks
from app.case_pipeline.cross_document.evidence import build_context
from app.fraud_detection.external_validation.runner import run_external_validation
from app.fraud_detection.external_validation.sg_reference.holidays import BUNDLED_HOLIDAYS
from app.fraud_detection.font_consistency import ExtractedField
from app.models import DocumentRecord, DocumentStatus

ZENITH = "Zenith Renewable Energy Pte Ltd"
_UPLOADED = datetime(2026, 10, 21, tzinfo=timezone.utc)


def _doc(filename: str, document_type: str, fields: dict) -> DocumentRecord:
    return DocumentRecord(
        id=f"doc-{filename.split('.')[0].lower()}", case_id="case-1", filename=filename,
        document_type=document_type, status=DocumentStatus.DETECTED, uploaded_at=_UPLOADED, extracted_fields=fields,
    )


def coi(name: str = ZENITH) -> DocumentRecord:
    return _doc("S255_CertIncorp.pdf", "certificate_of_incorporation", {"company_name": name, "uen": "201912345K"})


def constitution(name: str) -> DocumentRecord:
    return _doc("S255_Constitution.pdf", "company_constitution", {"company_name": name})


def bank_statement(name: str) -> DocumentRecord:
    return _doc("S255_BankStatement.pdf", "bank_statement", {"company_name": name, "bank_name": "DBS Bank Ltd"})


def findings(*documents: DocumentRecord):
    return run_deterministic_checks(build_context("case-1", list(documents), {}))


def about(result, what: str) -> list:
    return [f for f in result.findings if what in f.summary]


# --- SCRUM-255 Company identity consistency -------------------------------------------


def _names_match(first: DocumentRecord, second: DocumentRecord) -> None:
    result = findings(first, second)
    assert about(result, "company name") == []
    assert any("Company name is the same" in line for line in result.consistent)
    check = check_company_name_consistency(first, [first, second])
    assert check.discrepancies == []
    assert [c.matches for c in check.compared_documents] == [True]


def _names_flagged(first: DocumentRecord, second: DocumentRecord) -> None:
    """A discrepancy shown to the officer: both names, and the document each came from."""
    [finding] = about(findings(first, second), "company name")
    for document in (first, second):
        assert document.extracted_fields["company_name"] in finding.summary
    assert sorted(finding.document_ids) == sorted([first.id, second.id])
    assert finding.category.value == "cross_document_variance"
    [discrepancy] = check_company_name_consistency(first, [first, second]).discrepancies
    assert (discrepancy.stated, discrepancy.conflicting) == (
        first.extracted_fields["company_name"], second.extracted_fields["company_name"])
    assert discrepancy.filenames == [second.filename]


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-255", "CD-001")
def test_company_names_differing_only_by_suffix_punctuation_match():
    _names_match(coi(), constitution("Zenith Renewable Energy Pte. Ltd."))


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-255", "CD-002")
def test_company_names_with_improper_suffix_punctuation_are_flagged():
    _names_flagged(coi(), constitution("Zenith Renewable Energy P.te Ltd"))


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-255", "CD-003")
def test_company_names_with_a_different_core_name_are_flagged():
    _names_flagged(coi(), bank_statement("Zen Renewable Energy"))


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-255", "CD-004")
def test_identical_company_names_on_every_company_document_are_not_flagged():
    noa = _doc("S255_NOA.pdf", "notice_of_assessment", {"company_name": ZENITH})
    result = findings(coi(), constitution(ZENITH), noa)

    assert about(result, "company name") == []
    assert "Company name is the same across 3 documents" in result.consistent


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-255", "CD-005")
def test_the_full_legal_suffix_and_its_abbreviation_match():
    _names_match(constitution("Zenith Renewable Energy Private Limited"), coi())


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-255", "CD-006")
def test_differences_in_letter_case_and_spacing_alone_match():
    payslip = _doc("S255_Payslip.pdf", "payslip", {"company_name": "ZENITH RENEWABLE  ENERGY PTE LTD"})
    _names_match(coi(), payslip)


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-255", "CD-007")
def test_a_different_entity_type_is_flagged():
    # Failed in the live run (LLP matched Pte Ltd), fixed later and re-tested: this keeps it fixed.
    first, second = coi(), bank_statement("Zenith Renewable Energy LLP")
    _names_flagged(first, second)
    [discrepancy] = check_company_name_consistency(first, [first, second]).discrepancies
    assert "The legal entity type differs (Pte Ltd vs LLP)." in discrepancy.summary


# --- SCRUM-256 Public reference checks -------------------------------------------------

_AMK_406 = [{"BLK_NO": "406", "ROAD_NAME": "ANG MO KIO AVENUE 10", "BUILDING": "NIL",
             "ADDRESS": "406 ANG MO KIO AVENUE 10 SINGAPORE 560406", "POSTAL": "560406"}]
_HDB_AMK_406 = [{"blk_no": "406", "street": "ANG MO KIO AVE 10", "residential": "Y", "commercial": "N"}]
_TAMPINES_201 = [{"BLK_NO": "201", "ROAD_NAME": "TAMPINES STREET 21", "BUILDING": "NIL",
                  "ADDRESS": "201 TAMPINES STREET 21 SINGAPORE 520201", "POSTAL": "520201"}]


@pytest.fixture
def registries(monkeypatch):
    """No live registry: OneMap and HDB answer from what the test supplies; anything else fails the test."""
    async def bundled_holidays():
        return BUNDLED_HOLIDAYS

    monkeypatch.setattr("app.fraud_detection.external_validation.runner.public_holidays", bundled_holidays)
    with (
        patch("app.fraud_detection.external_validation.registry_lookups.http.httpx.AsyncClient",
              side_effect=AssertionError("test tried to call a live registry")),
        patch("app.fraud_detection.external_validation.registry_lookups.onemap.addresses_at_postal_code",
              AsyncMock(return_value=[])) as onemap,
        patch("app.fraud_detection.external_validation.registry_lookups.hdb.datastore_search",
              AsyncMock(return_value=[])) as hdb,
    ):
        yield onemap, hdb


def _fields(**values) -> list[ExtractedField]:
    return [ExtractedField(field_name=k, field_type="string", field_value=v, confidence=0.98) for k, v in values.items()]


def _codes(result, code: str) -> list:
    return [f for f in result.findings if f.check_code == code]


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-256", "CD-008")
async def test_a_legitimate_residential_address_is_clean(registries):
    onemap, hdb = registries
    onemap.return_value, hdb.return_value = _AMK_406, _HDB_AMK_406

    result = await run_external_validation(
        "doc-1", "proof_of_address",
        _fields(individual_name="Tan Ah Kow", individual_address="Blk 406 Ang Mo Kio Ave 10 #05-123 Singapore 560406"),
        [],
    )

    assert result.findings == []
    onemap.assert_awaited_with("560406")


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-256", "CD-009")
async def test_an_address_that_is_not_at_its_postal_code_is_flagged(registries):
    onemap, _ = registries
    onemap.return_value = _TAMPINES_201

    result = await run_external_validation(
        "doc-1", "proof_of_address",
        _fields(individual_name="Ong Mei Ling", individual_address="BLK 204 TAMPINES STREET 21, #07-45, SINGAPORE 520201"),
        [],
    )

    [finding] = _codes(result, "REG.ADDRESS_MATCH")
    assert "but postal code 520201 is 201 TAMPINES STREET 21" in finding.summary


def _ocbc_statement(bic: str) -> list[ExtractedField]:
    return _fields(bank_name="OCBC Bank", bic_code=bic, account_number="5012345678",
                   company_name=ZENITH, statement_period_end="2026-08-31")


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-256", "CD-010")
async def test_a_statement_with_the_banks_real_swift_code_is_clean(registries):
    result = await run_external_validation("doc-1", "bank_statement", _ocbc_statement("OCBCSGSGXXX"), [])

    assert _codes(result, "BS.BANK_IDENTIFIERS") == []
    assert not any(s.check_code == "BS.BANK_IDENTIFIERS" for s in result.skipped_checks)


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-256", "CD-011")
async def test_a_statement_with_another_banks_swift_code_is_flagged(registries):
    result = await run_external_validation("doc-1", "bank_statement", _ocbc_statement("DBSSSGSGXXX"), [])

    [finding] = _codes(result, "BS.BANK_IDENTIFIERS")
    assert "prints OCBC Bank's SWIFT BIC as DBSSSGSGXXX, but the bank's actual SWIFT BIC is OCBCSGSG" in finding.summary
    assert finding.severity == "impossible"


# --- SCRUM-253 Cross-document personal details ------------------------------------------


def nric(name: str, id_number: str) -> DocumentRecord:
    return _doc("C010-P2_NRICPassport.pdf", "nric", {"individual_name": name, "nric_number": id_number})


def payslip(name: str, id_number: str) -> DocumentRecord:
    return _doc("C010-P2_Payslip.pdf", "payslip",
                {"individual_name": name, "nric_number": id_number, "company_name": ZENITH})


def about_people(result) -> list:
    return [f for f in result.findings if "person" in f.summary or "ID number" in f.summary]


def person_findings(*documents: DocumentRecord) -> list:
    return about_people(findings(*documents))


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-253", "CD-012")
def test_matching_name_and_id_across_documents_raise_nothing():
    result = findings(nric("Tan Wei Ming", "S1234567D"), payslip("Tan Wei Ming", "S1234567D"))

    assert about_people(result) == []
    assert any("ID number is the same" in line for line in result.consistent)
    assert any("name is the same" in line for line in result.consistent)


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-253", "CD-013")
def test_a_name_that_differs_only_in_letter_case_matches():
    assert person_findings(nric("TAN WEI MING", "S1234567D"), payslip("Tan Wei Ming", "S1234567D")) == []


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-253", "CD-014")
def test_a_name_that_differs_only_in_word_order_matches():
    assert person_findings(nric("Tan Wei Ming", "S1234567D"), payslip("Wei Ming Tan", "S1234567D")) == []


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-253", "CD-015")
def test_the_same_name_with_different_id_numbers_is_flagged():
    [finding] = person_findings(nric("Tan Wei Ming", "S1234567A"), payslip("Tan Wei Ming", "S1234568A"))

    assert finding.severity == "high"
    assert "ID number" in finding.summary
    assert finding.category.value == "identity_entity_conflict"


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-253", "CD-016")
def test_the_same_id_with_different_names_is_flagged():
    # Failed in the live run (the name wasn't flagged), fixed later and re-tested: this keeps it fixed.
    [finding] = person_findings(nric("Tan Wei Ming", "S1234567A"), payslip("Lim Wei Ming", "S1234567A"))

    assert finding.summary == 'The person\'s name is "Tan Wei Ming" on the NRIC, but "Lim Wei Ming" on the payslip.'
    assert sorted(finding.document_ids) == ["doc-c010-p2_nricpassport", "doc-c010-p2_payslip"]


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-253", "CD-017")
def test_only_the_same_person_is_compared_on_a_document_naming_several_people():
    incumbency = _doc("C010_CertIncumbency.pdf", "certificate_of_incumbency", {
        "company_name": ZENITH,
        "director_name": "Tan Wei Ming", "director_id_number": "S1234567D",
        "secretary_name": "Ng Bee Leng", "secretary_id_number": "S7654321B",
    })

    result = findings(incumbency, nric("Tan Wei Ming", "S1234567D"))

    assert about_people(result) == []
    assert not any("Ng Bee Leng" in f.summary or "S7654321B" in f.summary for f in result.findings)
    assert "The ID number is the same on the certificate of incumbency and the NRIC" in result.consistent


# --- SCRUM-254 Address matching -----------------------------------------------------------


def address_findings(*documents: DocumentRecord) -> list:
    return about(findings(*documents), "address")


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-254", "CD-018")
def test_the_same_person_at_a_different_address_is_flagged():
    nric_doc = _doc("OngMeiLing_NRIC.pdf", "nric", {
        "individual_name": "Ong Mei Ling", "nric_number": "S8812345F",
        "individual_address": "BLK 22 BEDOK SOUTH AVENUE 1 #03-11 SINGAPORE 460022"})
    bill = _doc("OngMeiLing_UtilityBill.pdf", "proof_of_address", {
        "individual_name": "Ong Mei Ling",
        "individual_address": "BLK 204 TAMPINES STREET 21, #07-45, SINGAPORE 520204"})

    [finding] = address_findings(nric_doc, bill)

    assert "BLK 22 BEDOK SOUTH AVENUE 1 #03-11 SINGAPORE 460022" in finding.summary
    assert "BLK 204 TAMPINES STREET 21, #07-45, SINGAPORE 520204" in finding.summary
    assert sorted(finding.document_ids) == sorted([nric_doc.id, bill.id])


def tan_ah_kow(filename: str, document_type: str, address: str) -> DocumentRecord:
    return _doc(filename, document_type, {"individual_name": "TAN AH KOW", "nric_number": "S1234567D",
                                          "individual_address": address})


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-254", "CD-019")
def test_the_same_postal_code_with_a_different_unit_is_flagged():
    [finding] = address_findings(
        tan_ah_kow("TanAhKow_NRIC.pdf", "nric", "22 Bedok South Avenue 1 #03-11 Singapore 460022"),
        tan_ah_kow("TanAhKow_CreditReport.pdf", "credit_report", "22 Bedok South Avenue 1 #03-12 Singapore 460022"),
    )

    assert "#03-11" in finding.summary and "#03-12" in finding.summary
    assert sorted(finding.document_ids) == ["doc-tanahkow_creditreport", "doc-tanahkow_nric"]


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-254", "CD-020")
def test_the_same_company_at_a_different_address_is_flagged():
    incumbency = _doc("C020_CertIncumbency.pdf", "certificate_of_incumbency", {
        "company_name": "Tuas Precision Engineering Pte Ltd", "uen": "201914237K",
        "company_address": "8 Tuas Avenue 10 Singapore 639137"})
    noa = _doc("C020_NOA.pdf", "notice_of_assessment", {
        "company_name": "Tuas Precision Engineering Pte Ltd", "uen": "201914237K",
        "company_address": "13 Tuas Avenue 10 Singapore 639137"})

    [finding] = address_findings(incumbency, noa)

    assert finding.summary.startswith("The company's address")
    assert "8 Tuas Avenue 10" in finding.summary and "13 Tuas Avenue 10" in finding.summary
    assert sorted(finding.document_ids) == sorted([incumbency.id, noa.id])


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-254", "CD-021")
def test_the_same_address_written_differently_is_not_flagged():
    result = findings(
        tan_ah_kow("TanAhKow_NRIC.pdf", "nric", "22 BEDOK SOUTH AVENUE 1 #03-11 SINGAPORE 460022"),
        tan_ah_kow("TanAhKow_CreditReport.pdf", "credit_report", "blk 22 bedok sth ave 1, #03-11, s460022"),
    )

    assert about(result, "address") == []
    assert "No address differs between documents for the same person or company" in result.consistent
