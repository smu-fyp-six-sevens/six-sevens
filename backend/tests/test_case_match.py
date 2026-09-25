from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.case_match import (
    CheckOutcome,
    DocumentMatchStatus,
    IncumbencyStatus,
    check_case_match,
    company_names_match,
    individual_names_match,
    normalize_uen,
)
from app.main import app
from app.models import CaseRecord, DocumentRecord, DocumentStatus

client = TestClient(app)

UEN = "201912345K"
OTHER_UEN = "202054321M"
_BASE_TIME = datetime(2026, 9, 1, tzinfo=timezone.utc)

# Same shape as real extracted certificate of incumbency fields — flat,
# role-prefixed keys per officer/shareholder (see REAL_INCUMBENCY_FIELDS).
INCUMBENCY_FIELDS = {
    "company_name": "ACME Trading Pte. Ltd.",
    "uen": UEN,
    "individual_name": "TAN WEI MING",
    "id_number": "S1234567A",
    "director_name": "TAN WEI MING",
    "director_id_number": "S1234567A",
    "secretary_name": "LIM HUI LING",
    "secretary_id_number": "S7654321B",
    "shareholder_1_name": "TAN WEI MING",
    "shareholder_1_id_number": "S1234567A",
    "audit_firm_name": "TAN & PARTNERS LLP",
}

# Copied from a real documents.extracted_fields row (C014_CertIncumbency.pdf).
REAL_INCUMBENCY_FIELDS = {
    "uen": "202023453H", "id_number": "S9023453H", "share_class": "ORDINARY",
    "status_date": "15/04/2020", "company_name": "IRONCLAD SECURITY SERVICES PTE LTD",
    "company_type": "PRIVATE COMPANY LIMITED BY SHARES",
    "director_name": "VIKRAM SINGH S/O HARJIT", "document_date": "28/03/2026",
    "share_capital": "500,000.00 SGD", "audit_firm_uen": "T09LL0512C",
    "company_status": "LIVE COMPANY", "receipt_number": "ACRA668230965120",
    "secretary_name": "RAJESWARI D/O MUTHUSAMY", "audit_firm_name": "TAN & PARTNERS LLP",
    "company_address": "1 KAKI BUKIT AVENUE 1, #02-24, ENTERPRISE HUB, SINGAPORE 417941",
    "date_of_last_ar": "10/09/2025", "individual_name": "VIKRAM SINGH S/O HARJIT",
    "date_of_last_agm": "02/08/2025",
    "director_address": "13 EUNOS CRESCENT, #06-01, SINGAPORE 643780",
    "number_of_shares": "500,000",
    "issuing_authority": "ACCOUNTING AND CORPORATE REGULATORY AUTHORITY (ACRA)",
    "secretary_address": "3 CHURCH STREET, #12-06 SAMSUNG HUB, SINGAPORE 049483",
    "director_id_number": "S9023453H", "financial_year_end": "31/03",
    "incorporation_date": "15/04/2020",
    "individual_address": "13 EUNOS CRESCENT, #06-01, SINGAPORE 643780",
    "principal_activity": "RETAIL SALE IN NON-SPECIALISED STORES N.E.C. (47199)",
    "shareholder_1_name": "VIKRAM SINGH S/O HARJIT", "shareholder_2_name": "KWEK CHOON BENG",
    "secretary_id_number": "S7999362E", "director_nationality": "SINGAPORE CITIZEN",
    "shareholder_1_shares": "300,000", "shareholder_2_shares": "200,000",
    "authentication_number": "A43259608Z", "secretary_nationality": "SINGAPORE CITIZEN",
    "shareholder_1_address": "13 EUNOS CRESCENT, #06-01, SINGAPORE 643780",
    "shareholder_2_address": "23 MACPHERSON ROAD, #11-65, SINGAPORE 105962",
    "shareholder_1_id_number": "S9023453H", "shareholder_2_id_number": "S8003014B",
    "shareholder_1_share_type": "ORDINARY", "shareholder_2_share_type": "ORDINARY",
    "director_appointment_date": "15/04/2020",
    "shareholder_1_nationality": "SINGAPORE CITIZEN",
    "shareholder_2_nationality": "SINGAPORE CITIZEN",
    "secretary_appointment_date": "05/06/2020",
    "registered_office_address_date": "15/04/2020",
}


def _case(company_name: str | None = None) -> CaseRecord:
    return CaseRecord(
        id="case-1",
        greenlink_case_id="GLDB-1",
        company_name=company_name,
        created_at=_BASE_TIME,
        updated_at=_BASE_TIME,
    )


_counter = 0


def _doc(document_type: str, fields: dict | None, doc_id: str | None = None) -> DocumentRecord:
    global _counter
    _counter += 1
    return DocumentRecord(
        id=doc_id or f"doc-{_counter}",
        case_id="case-1",
        filename=f"{document_type}.pdf",
        document_type=document_type,
        status=DocumentStatus.ANALYZED,
        uploaded_at=_BASE_TIME + timedelta(minutes=_counter),
        extracted_fields=fields,
    )


def _incumbency(fields: dict | None = None, doc_id: str = "inc") -> DocumentRecord:
    return _doc("certificate_of_incumbency", fields or INCUMBENCY_FIELDS, doc_id)


def _corroborating(n: int = 3, uen: str = UEN) -> list[DocumentRecord]:
    types = ["certificate_of_incorporation", "company_constitution", "source_of_wealth", "list_of_loans"]
    return [_doc(types[i % len(types)], {"company_name": "ACME Trading Pte Ltd", "uen": uen}) for i in range(n)]


def _by_id(result, doc_id: str):
    return next(d for d in result.documents if d.document_id == doc_id)


# --- fuzzy matching -----------------------------------------------------------


@pytest.mark.parametrize(
    "a, b, expected",
    [
        ("ACME Trading Pte Ltd", "ACME Pte Ltd", True),
        ("ACME Trading Pte. Ltd.", "Acme Trading Private Limited", True),
        ("ACME Trading Pte Ltd", "ACNE Pte Ltd", False),
        ("ACME Trading Pte Ltd", "ACME Holdings Pte Ltd", False),
        ("ACME Trading Pte Ltd", "Global ACME Trading Pte Ltd", False),
        ("A&B Logistics Pte Ltd", "A AND B Logistics", True),
        ("", "ACME Pte Ltd", False),
    ],
)
def test_company_names_match(a: str, b: str, expected: bool) -> None:
    assert company_names_match(a, b) is expected


def test_individual_names_match_ignores_order_and_case() -> None:
    assert individual_names_match("Tan Wei Ming", "WEI MING TAN")
    assert not individual_names_match("Tan Wei Ming", "Tan Wei Min")


def test_normalize_uen_pulls_uen_out_of_labelled_value() -> None:
    assert normalize_uen("UEN: 2019-12345K") == UEN
    assert normalize_uen("t08ll1234a") == "T08LL1234A"


# --- step 1: incumbency verification ------------------------------------------


def test_missing_incumbency_leaves_every_document_pending() -> None:
    docs = _corroborating()
    result = check_case_match(_case(), docs)

    assert result.incumbency.status == IncumbencyStatus.MISSING
    assert all(d.status == DocumentMatchStatus.PENDING for d in result.documents)
    assert not any(d.can_proceed_to_fraud_detection for d in result.documents)


def test_incumbency_verified_once_three_documents_carry_its_uen() -> None:
    result = check_case_match(_case(), [_incumbency(), *_corroborating(3)])

    assert result.incumbency.status == IncumbencyStatus.VERIFIED
    assert len(result.incumbency.corroborating_document_ids) == 3
    assert _by_id(result, "inc").status == DocumentMatchStatus.MATCH
    assert all(d.can_proceed_to_fraud_detection for d in result.documents)


def test_fewer_than_three_corroborating_documents_is_insufficient() -> None:
    docs = [_incumbency(), *_corroborating(2)]
    result = check_case_match(_case(), docs)

    assert result.incumbency.status == IncumbencyStatus.INSUFFICIENT_EVIDENCE
    assert all(d.status == DocumentMatchStatus.PENDING for d in result.documents)
    # Comparisons are still shown, just not acted on.
    assert _by_id(result, docs[1].id).comparisons[0].outcome == CheckOutcome.MATCH


def test_incumbency_flagged_when_other_documents_agree_on_a_different_uen() -> None:
    docs = [_incumbency(), *_corroborating(3, uen=OTHER_UEN)]
    result = check_case_match(_case(), docs)

    assert result.incumbency.status == IncumbencyStatus.MANUAL_REVIEW
    assert OTHER_UEN in result.incumbency.reasons[0]
    assert _by_id(result, "inc").status == DocumentMatchStatus.MANUAL_REVIEW
    # The others aren't flagged against a reference that's itself in doubt.
    assert result.mismatched_document_ids == []
    assert all(d.status == DocumentMatchStatus.PENDING for d in result.documents[1:])


def test_company_name_match_counts_towards_verifying_incumbency() -> None:
    # Mirrors the C014 test set: 2 documents with the UEN plus a corporate
    # bank statement that only has the company name.
    bank_statement = _doc("bank_statement", {"company_name": "ACME Trading Pte Ltd", "bank_name": "DBS"})
    result = check_case_match(_case(), [_incumbency(), *_corroborating(2), bank_statement])

    assert result.incumbency.status == IncumbencyStatus.VERIFIED
    assert bank_statement.id in result.incumbency.corroborating_document_ids


def test_foreign_passport_goes_to_manual_review_once_incumbency_verified() -> None:
    bank_statement = _doc("bank_statement", {"company_name": "ACME Trading Pte Ltd", "bank_name": "DBS"})
    passport = _doc("passport", {"individual_name": "TEO KAI MING", "nric_number": "S7273147P"})
    result = check_case_match(
        _case(), [_incumbency(), *_corroborating(2), bank_statement, passport]
    )

    match = _by_id(result, passport.id)
    assert match.status == DocumentMatchStatus.MANUAL_REVIEW
    assert match.case_mismatch
    assert "S7273147P" in match.reason


def test_personal_documents_do_not_count_towards_verifying_incumbency() -> None:
    payslip = _doc("payslip", {"individual_name": "Tan Wei Ming", "company_name": "ACME Trading Pte Ltd"})
    result = check_case_match(_case(), [_incumbency(), *_corroborating(2), payslip])

    assert result.incumbency.status == IncumbencyStatus.INSUFFICIENT_EVIDENCE


def test_incumbency_flagged_when_other_documents_agree_on_a_different_company_name() -> None:
    others = [
        _doc("bank_statement", {"company_name": "Platinum Fitness Group Pte Ltd", "bank_name": "DBS"}),
        _doc("proof_of_address", {"company_name": "PLATINUM FITNESS GROUP PTE. LTD.", "company_address": "1 Road"}),
    ]
    result = check_case_match(_case(), [_incumbency(), *others])

    assert result.incumbency.status == IncumbencyStatus.MANUAL_REVIEW
    assert "PLATINUM FITNESS GROUP" in result.incumbency.reasons[0]


def test_incumbency_flagged_when_it_disagrees_with_case_company_name() -> None:
    result = check_case_match(_case("ACNE Pte Ltd"), [_incumbency(), *_corroborating(3)])

    assert result.incumbency.status == IncumbencyStatus.MANUAL_REVIEW
    assert "ACNE Pte Ltd" in result.incumbency.reasons[0]


def test_incumbency_without_uen_goes_to_manual_review() -> None:
    fields = {**INCUMBENCY_FIELDS, "uen": None}
    result = check_case_match(_case(), [_incumbency(fields), *_corroborating(3)])

    assert result.incumbency.status == IncumbencyStatus.MANUAL_REVIEW


def test_two_incumbencies_with_different_uens_go_to_manual_review() -> None:
    other = _doc("certificate_of_incumbency", {**INCUMBENCY_FIELDS, "uen": OTHER_UEN}, "inc-2")
    result = check_case_match(_case(), [_incumbency(), other, *_corroborating(3)])

    assert result.incumbency.status == IncumbencyStatus.MANUAL_REVIEW


# --- step 2: company check ----------------------------------------------------


def test_document_with_other_uen_is_a_case_mismatch_sent_to_manual_review() -> None:
    stray = _doc("notice_of_assessment", {"company_name": "ACME Trading", "tax_reference_number": OTHER_UEN})
    result = check_case_match(_case(), [_incumbency(), *_corroborating(3), stray])

    match = _by_id(result, stray.id)
    assert match.status == DocumentMatchStatus.MANUAL_REVIEW
    assert match.case_mismatch
    assert not match.can_proceed_to_fraud_detection
    assert result.mismatched_document_ids == [stray.id]
    # AC4: identifies both the document's value and the case's.
    comparison = match.comparisons[0]
    assert comparison.check == "company_uen"
    assert comparison.document_field == "tax_reference_number"
    assert comparison.document_value == OTHER_UEN
    assert comparison.case_value == UEN


def test_uen_takes_priority_over_company_name() -> None:
    # Name looks nothing alike, but the UEN is authoritative.
    doc = _doc("fatca_crs_declaration", {"company_name": "Something Else Pte Ltd", "tin": UEN})
    result = check_case_match(_case(), [_incumbency(), *_corroborating(3), doc])

    assert _by_id(result, doc.id).status == DocumentMatchStatus.MATCH


@pytest.mark.parametrize(
    "company_name, expected",
    [("ACME Pte Ltd", DocumentMatchStatus.MATCH), ("ACNE Pte Ltd", DocumentMatchStatus.MANUAL_REVIEW)],
)
def test_company_name_fuzzy_match_when_document_has_no_uen(company_name, expected) -> None:
    doc = _doc("bank_statement", {"company_name": company_name, "bank_name": "DBS"})
    result = check_case_match(_case(), [_incumbency(), *_corroborating(3), doc])

    match = _by_id(result, doc.id)
    assert match.status == expected
    assert match.case_mismatch is (expected == DocumentMatchStatus.MANUAL_REVIEW)
    assert match.comparisons[0].check == "company_name"


def test_payslip_employer_is_not_compared_to_the_case_company() -> None:
    payslip = _doc("payslip", {"individual_name": "Tan Wei Ming", "company_name": "Other Employer Pte Ltd"})
    result = check_case_match(_case(), [_incumbency(), *_corroborating(3), payslip])

    match = _by_id(result, payslip.id)
    assert match.status == DocumentMatchStatus.MATCH
    assert [c.check for c in match.comparisons] == ["individual_name"]


# --- step 3: individual check -------------------------------------------------


def test_nric_matched_by_id_number() -> None:
    nric = _doc("nric", {"nric_number": "s1234567a"})  # back side: no name
    result = check_case_match(_case(), [_incumbency(), *_corroborating(3), nric])

    match = _by_id(result, nric.id)
    assert match.status == DocumentMatchStatus.MATCH
    assert match.comparisons[0].check == "individual_id"


def test_id_decides_even_when_name_matches() -> None:
    nric = _doc("nric", {"nric_number": "S9999999Z", "individual_name": "Tan Wei Ming"})
    result = check_case_match(_case(), [_incumbency(), *_corroborating(3), nric])

    match = _by_id(result, nric.id)
    assert match.status == DocumentMatchStatus.MANUAL_REVIEW
    assert match.case_mismatch
    assert "S9999999Z" in match.reason


def test_individual_matched_by_name_when_no_id() -> None:
    doc = _doc("proof_of_address", {"individual_name": "Wei Ming Tan", "individual_address": "1 Road"})
    stranger = _doc("proof_of_address", {"individual_name": "John Doe", "individual_address": "2 Road"})
    result = check_case_match(_case(), [_incumbency(), *_corroborating(3), doc, stranger])

    assert _by_id(result, doc.id).status == DocumentMatchStatus.MATCH
    mismatch = _by_id(result, stranger.id)
    assert mismatch.status == DocumentMatchStatus.MANUAL_REVIEW
    assert mismatch.case_mismatch
    assert mismatch.comparisons[0].case_value == "TAN WEI MING, LIM HUI LING"


def test_constitution_checks_company_only_not_subscribers() -> None:
    # Subscribers are the company's founders, who may have left since —
    # not expected to match the current incumbency.
    constitution = _doc(
        "company_constitution",
        {"uen": UEN, "subscribers": [{"name": "Former Founder", "id_number": "S0000000X"}]},
    )
    result = check_case_match(_case(), [_incumbency(), *_corroborating(3), constitution])

    match = _by_id(result, constitution.id)
    assert match.status == DocumentMatchStatus.MATCH
    assert [c.check for c in match.comparisons] == ["company_uen"]


def test_real_incumbency_lists_every_officer_and_shareholder_once() -> None:
    incumbency = _incumbency(REAL_INCUMBENCY_FIELDS)
    others = _corroborating(3, uen="202023453H")
    result = check_case_match(_case("Ironclad Security Services"), [incumbency, *others])

    assert result.incumbency.status == IncumbencyStatus.VERIFIED
    assert result.incumbency.uen == "202023453H"
    # Director, secretary and both shareholders, with the director (also
    # shareholder_1 and individual_name) listed once — and the audit firm,
    # which isn't a person, not at all.
    assert sorted((p.name, p.id_number) for p in result.incumbency.individuals) == [
        ("KWEK CHOON BENG", "S8003014B"),
        ("RAJESWARI D/O MUTHUSAMY", "S7999362E"),
        ("VIKRAM SINGH S/O HARJIT", "S9023453H"),
    ]


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"nric_number": "S8003014B"}, DocumentMatchStatus.MATCH),  # shareholder_2
        ({"nric_number": "S7999362E"}, DocumentMatchStatus.MATCH),  # secretary
        ({"individual_name": "Vikram Singh s/o Harjit", "nric_number": "S9023453H"}, DocumentMatchStatus.MATCH),
        ({"nric_number": "S1111111A"}, DocumentMatchStatus.MANUAL_REVIEW),
    ],
)
def test_real_incumbency_individuals_matched_by_id(fields, expected) -> None:
    nric = _doc("nric", fields)
    docs = [_incumbency(REAL_INCUMBENCY_FIELDS), *_corroborating(3, uen="202023453H"), nric]
    result = check_case_match(_case(), docs)

    assert _by_id(result, nric.id).status == expected


def test_officers_list_shape_also_supported() -> None:
    fields = {
        "company_name": "ACME Trading Pte Ltd",
        "uen": UEN,
        "officers": [{"name": "LIM HUI LING", "id_number": "S7654321B"}],
    }
    nric = _doc("nric", {"nric_number": "S7654321B"})
    result = check_case_match(_case(), [_incumbency(fields), *_corroborating(3), nric])

    assert _by_id(result, nric.id).status == DocumentMatchStatus.MATCH


def test_incumbency_with_flat_joined_fields_still_lists_every_person() -> None:
    fields = {
        "company_name": "ACME Trading Pte Ltd",
        "uen": UEN,
        "individual_name": "TAN WEI MING; LIM HUI LING",
        "id_number": "S1234567A; S7654321B",
    }
    nric = _doc("nric", {"nric_number": "S7654321B"})
    result = check_case_match(_case(), [_incumbency(fields), *_corroborating(3), nric])

    assert len(result.incumbency.individuals) == 2
    assert _by_id(result, nric.id).status == DocumentMatchStatus.MATCH


# --- other document states ----------------------------------------------------


def test_unextracted_document_is_pending() -> None:
    doc = _doc("bank_statement", None)
    result = check_case_match(_case(), [_incumbency(), *_corroborating(3), doc])

    assert _by_id(result, doc.id).status == DocumentMatchStatus.PENDING


def test_document_with_nothing_to_compare_goes_to_manual_review() -> None:
    doc = _doc("unrecognised", {"document_title": "Letter"})
    result = check_case_match(_case(), [_incumbency(), *_corroborating(3), doc])

    match = _by_id(result, doc.id)
    assert match.status == DocumentMatchStatus.MANUAL_REVIEW
    assert not match.case_mismatch


# --- endpoints ----------------------------------------------------------------


def test_case_match_endpoint() -> None:
    docs = [_incumbency(), *_corroborating(3)]
    with patch("app.routers.cases.case_repository") as case_repo, patch(
        "app.routers.cases.document_repository"
    ) as doc_repo:
        case_repo.get_case.return_value = _case()
        doc_repo.list_documents.return_value = docs
        response = client.get("/cases/case-1/case-match")

    assert response.status_code == 200
    body = response.json()
    assert body["incumbency"]["status"] == "verified"
    assert len(body["documents"]) == 4
    doc_repo.list_documents.assert_called_once_with("case-1")


def test_case_match_endpoint_404_for_unknown_case() -> None:
    with patch("app.routers.cases.case_repository") as case_repo, patch(
        "app.routers.cases.document_repository"
    ):
        case_repo.get_case.return_value = None
        response = client.get("/cases/nope/case-match")

    assert response.status_code == 404


def test_document_case_match_endpoint_returns_that_documents_entry() -> None:
    stray = _doc("list_of_loans", {"uen": OTHER_UEN, "individual_name": "Tan Wei Ming"}, "stray")
    docs = [_incumbency(), *_corroborating(3), stray]
    with patch("app.routers.detection.case_repository") as case_repo, patch(
        "app.routers.detection.document_repository"
    ) as doc_repo:
        doc_repo.get_document.return_value = stray
        case_repo.get_case.return_value = _case()
        doc_repo.list_documents.return_value = docs
        response = client.get("/documents/stray/detection/case-match")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == "stray"
    assert body["status"] == "manual_review"
    assert body["case_mismatch"] is True
    assert body["can_proceed_to_fraud_detection"] is False


def test_document_case_match_endpoint_404_without_case() -> None:
    orphan = _doc("nric", {"nric_number": "S1234567A"}, "orphan").model_copy(update={"case_id": None})
    with patch("app.routers.detection.case_repository"), patch(
        "app.routers.detection.document_repository"
    ) as doc_repo:
        doc_repo.get_document.return_value = orphan
        response = client.get("/documents/orphan/detection/case-match")

    assert response.status_code == 404


def test_case_snapshot_includes_case_match_computed_from_plain_fields() -> None:
    from app import case_status
    from app.fraud_detection.font_consistency_v2 import ExtractedField

    passport = _doc("passport", {"individual_name": "TEO KAI MING", "nric_number": "S7273147P"}, "pp")
    docs = [_incumbency(), *_corroborating(3), passport]
    with patch("app.case_status.case_repository") as case_repo, patch(
        "app.case_status.document_repository"
    ) as doc_repo, patch("app.case_status.extracted_fields_repository") as fields_repo:
        case_repo.get_case.return_value = _case()
        doc_repo.list_documents.return_value = docs
        # Enrichment rewrites extracted_fields into {value, confidence}
        # wrappers — the case match must not see that shape.
        fields_repo.get_extracted_fields_for_documents.return_value = {
            "pp": [ExtractedField(field_name="nric_number", field_type="string", field_value="S7273147P", confidence=0.9)]
        }
        snapshot = case_status.build_case_snapshot("case-1")

    assert snapshot["case_match"]["incumbency"]["status"] == "verified"
    passport_match = next(d for d in snapshot["case_match"]["documents"] if d["document_id"] == "pp")
    assert passport_match["status"] == "manual_review"
    assert passport_match["case_mismatch"] is True
