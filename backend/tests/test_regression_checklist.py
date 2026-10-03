"""
Regression tests (SCRUM-206) for the document checklist — "Test Cases -
Sprint 2.xlsx", Document Ingestion sheet: Classify Optional Documents
(SCRUM-114) DI-031 – DI-034 and Map Documents (SCRUM-118) DI-035 – DI-038.

Run against real cases captured from Supabase
(scripts/capture_regression_fixture.py), not invented data:
  anchorpoint   GLDB-02 — every required and optional document, two
                identities (NRIC front + back, and a passport).
  harbor-light  GLDB-16 — the C014 set, 7 requirements still missing.

The first test in each group is the strongest check: recomputing the
checklist from the case's real documents must reproduce exactly what
production computed when the fixture was captured. The Sprint 2 manual runs
used cases C060/C064, which no longer exist in Supabase.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.case_service import build_summary
from app.config import settings
from app.models import (
    ADDITIONAL_DOCUMENT_TYPES,
    REQUIRED_DOCUMENT_TYPES,
    CaseDocumentsStatus,
    CaseRecord,
    RoutingStatus,
    DocumentRecord,
    compute_fulfilled_requirements,
)

FIXTURES = Path(__file__).parent / "fixtures" / "regression"


def _load(name: str) -> tuple[CaseRecord, list[DocumentRecord], dict]:
    fixture = json.loads((FIXTURES / f"{name}.json").read_text())
    case = CaseRecord(**fixture["case"])
    documents = [DocumentRecord(**row) for row in fixture["rows"]]
    return case, documents, fixture["case"]


def _by_name(documents: list[DocumentRecord], part: str) -> DocumentRecord:
    matches = [d for d in documents if part in d.filename]
    assert len(matches) == 1, f"expected one document matching {part!r}, got {len(matches)}"
    return matches[0]


def _without(documents: list[DocumentRecord], *parts: str) -> list[DocumentRecord]:
    return [d for d in documents if not any(part in d.filename for part in parts)]


def _officer_confirmed(documents: list[DocumentRecord]) -> list[DocumentRecord]:
    """
    The fixtures predate the rule that an accepted document below the
    classification threshold only counts once an officer confirms its type
    (models.counts_towards_checklist). anchorpoint's two utility bills were
    accepted at 0.83; confirming them — which writes 1.0, as the type
    dropdown does — reproduces what production computed at capture time.
    """
    return [
        d.model_copy(update={"classification_confidence": 1.0})
        if d.routing_status == RoutingStatus.ACCEPTED
        and (d.classification_confidence or 0) < settings.CLASSIFICATION_CONFIDENCE_THRESHOLD
        else d
        for d in documents
    ]


# --- Classify Optional Documents (SCRUM-114) -----------------------------------


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-114", "DI-034")
@pytest.mark.parametrize("name", ["anchorpoint", "harbor-light"])
def test_checklist_recomputed_from_real_documents_matches_production(name):
    case, documents, captured = _load(name)

    summary = build_summary(case, _officer_confirmed(documents))

    assert set(summary.fulfilled_requirements) == set(captured["fulfilled_requirements"])
    assert summary.recommended_additional_documents == captured["recommended_additional_documents"]
    assert summary.proof_of_address_coverage.model_dump() == captured["proof_of_address_coverage"]
    assert summary.id_document_coverage.model_dump() == captured["id_document_coverage"]


def test_unconfirmed_low_confidence_documents_do_not_tick_the_checklist():
    # The same case as captured, before an officer confirms the two utility
    # bills accepted at 0.83: neither can tick a checklist item yet.
    case, documents, captured = _load("anchorpoint")
    assert "company_proof_of_address" in captured["fulfilled_requirements"]

    summary = build_summary(case, documents)

    assert "company_proof_of_address" not in summary.fulfilled_requirements


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-114", "DI-031")
def test_required_and_optional_requirements_are_separate_lists():
    assert set(REQUIRED_DOCUMENT_TYPES).isdisjoint(ADDITIONAL_DOCUMENT_TYPES)
    assert set(ADDITIONAL_DOCUMENT_TYPES) == {
        "list_of_loans",
        "company_proof_of_address",
        "source_of_personal_wealth",
    }
    assert len(REQUIRED_DOCUMENT_TYPES) == 11


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-114", "DI-032")
def test_missing_optional_documents_do_not_make_the_case_incomplete():
    case, documents, _ = _load("anchorpoint")
    no_optional = _without(
        _officer_confirmed(documents),
        "LoanFacility", "LoanOffer", "FacilityLetter",   # list of loans
        "Company_UtilityBill",                          # company proof of address
        "SimS_SourceOfWealth", "Wealth_Statement_scan",  # personal source of wealth
    )

    summary = build_summary(case, no_optional)

    assert summary.recommended_additional_documents == []
    assert set(REQUIRED_DOCUMENT_TYPES) <= set(summary.fulfilled_requirements)
    assert not set(ADDITIONAL_DOCUMENT_TYPES) & set(summary.fulfilled_requirements)
    assert summary.documents_status == CaseDocumentsStatus.ANALYZED


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-114", "DI-033")
@pytest.mark.parametrize(
    "filename, requirement",
    [
        ("FacilityLetter_AIB", "list_of_loans"),
        ("Company_UtilityBill", "company_proof_of_address"),
        ("SimS_SourceOfWealth", "source_of_personal_wealth"),
        ("Wealth_Statement_scan", "source_of_personal_wealth"),
    ],
)
def test_optional_document_maps_to_its_optional_requirement(filename, requirement):
    _, documents, _ = _load("anchorpoint")
    document = _by_name(documents, filename)

    fulfilled = compute_fulfilled_requirements(document.document_type, document.extracted_fields)

    assert fulfilled == [requirement]


# --- Map Documents (SCRUM-118) ---------------------------------------------------


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-118", "DI-035")
@pytest.mark.parametrize(
    "filename, requirements",
    [
        ("BizFile_333996", ["certificate_of_incorporation"]),
        ("Statement_128262", ["bank_statement"]),
        # Personal bank statement: proof of address, not the corporate item.
        ("MuhammadD_ProofAddr_BankStmt", ["proof_of_address"]),
        ("Financials_BalanceSheet", ["source_of_wealth"]),
        ("Passport_6086", ["id_document"]),
        ("POR_UtilityBill", ["proof_of_address"]),
    ],
)
def test_each_document_maps_to_the_requirement_it_fulfils(filename, requirements):
    _, documents, _ = _load("anchorpoint")
    document = _by_name(documents, filename)

    assert compute_fulfilled_requirements(document.document_type, document.extracted_fields) == requirements


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-118", "DI-036")
def test_nric_back_fulfils_both_identity_document_and_proof_of_address():
    _, documents, _ = _load("anchorpoint")
    back = _by_name(documents, "MuhammadD_NRIC_back")
    front = _by_name(documents, "MuhammadD_NRIC_front")

    assert compute_fulfilled_requirements(back.document_type, back.extracted_fields) == [
        "id_document",
        "proof_of_address",
    ]
    assert compute_fulfilled_requirements(front.document_type, front.extracted_fields) == ["id_document"]


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-118", "DI-036")
def test_nric_needs_both_sides_to_complete_that_identity():
    case, documents, _ = _load("anchorpoint")
    complete = build_summary(case, documents).id_document_coverage
    front_only = build_summary(case, _without(documents, "MuhammadD_NRIC_back")).id_document_coverage

    assert (complete.covered, complete.total) == (2, 2)
    assert (front_only.covered, front_only.total) == (1, 2)


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-118", "DI-037")
def test_documents_from_another_case_do_not_fulfil_this_case():
    harbor_case, harbor_documents, _ = _load("harbor-light")
    _, anchorpoint_documents, _ = _load("anchorpoint")
    assert {d.case_id for d in harbor_documents} == {harbor_case.id}
    assert harbor_case.id not in {d.case_id for d in anchorpoint_documents}

    # The case summary is built from the case's own documents only
    # (document_repository.list_documents(case_id=...)); Harbor Light's
    # missing items stay missing even though Anchorpoint has them.
    summary = build_summary(harbor_case, harbor_documents)
    assert "bank_statement" in summary.recommended_additional_documents


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-118", "DI-038")
def test_fulfilled_requirements_are_never_also_listed_as_missing():
    for name in ("anchorpoint", "harbor-light"):
        case, documents, _ = _load(name)
        summary = build_summary(case, documents)

        assert not set(summary.fulfilled_requirements) & set(summary.recommended_additional_documents)
        assert set(summary.recommended_additional_documents) == (
            set(REQUIRED_DOCUMENT_TYPES) - set(summary.fulfilled_requirements)
        )


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-118", "DI-038")
def test_only_accepted_documents_fulfil_requirements():
    case, documents, _ = _load("harbor-light")
    cert = _by_name(documents, "C014_CertIncorp")
    cert.routing_status = RoutingStatus.MANUAL_REVIEW

    summary = build_summary(case, documents)

    assert "certificate_of_incorporation" not in summary.fulfilled_requirements
    assert "certificate_of_incorporation" in summary.recommended_additional_documents
