"""
Regression tests (SCRUM-246) for matching documents to their case — "Test
Cases - Sprint 3.xlsx", Document Ingestion sheet, Check Document Matches
Current Case Data (SCRUM-110): DI-039 – DI-047.

Run against real cases captured from Supabase (scripts/capture_regression_fixture.py):
  anchorpoint   GLDB-02 — the "C001" role: a complete case whose
                certificate of incumbency is verified by its other documents.
  harbor-light  GLDB-16 — the "C002" role: another company's documents.

The live runs used C001/C002, which no longer exist in Supabase. Case match
is deterministic (no AI), so these run the real check_case_match.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.case_match import (
    CaseMatchResult,
    CheckOutcome,
    DocumentCaseMatch,
    DocumentMatchStatus,
    IncumbencyStatus,
    check_case_match,
)
from app.models import CaseRecord, DocumentRecord

FIXTURES = Path(__file__).parent / "fixtures" / "regression"


def _load(name: str) -> tuple[CaseRecord, list[DocumentRecord]]:
    fixture = json.loads((FIXTURES / f"{name}.json").read_text())
    return CaseRecord(**fixture["case"]), [DocumentRecord(**row) for row in fixture["rows"]]


def _by_name(documents: list[DocumentRecord], part: str) -> DocumentRecord:
    [match] = [d for d in documents if part in d.filename]
    return match


def _foreign(document: DocumentRecord, case: CaseRecord) -> DocumentRecord:
    """Another company's document uploaded into this case by mistake."""
    return document.model_copy(update={"case_id": case.id, "id": f"foreign-{document.id}"})


def _entry(result: CaseMatchResult, document: DocumentRecord) -> DocumentCaseMatch:
    return next(m for m in result.documents if m.document_id == document.id)


@pytest.fixture
def anchorpoint():
    return _load("anchorpoint")


@pytest.fixture
def harbor_light() -> list[DocumentRecord]:
    """Only its documents are used: they belong to another company than anchorpoint's case."""
    return _load("harbor-light")[1]


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-110", "DI-039")
@pytest.mark.parametrize("filename", ["BizFile_333996", "Anchorpoint_IRAS_NOA", "Statement_128262",
                                      "AnchorpointInsuranceBrokers_Constitution"])
def test_a_company_document_matching_the_case_is_a_match(anchorpoint, filename):
    case, documents = anchorpoint
    result = check_case_match(case, documents)

    assert result.incumbency.status == IncumbencyStatus.VERIFIED
    entry = _entry(result, _by_name(documents, filename))
    assert entry.status == DocumentMatchStatus.MATCH
    assert entry.can_proceed_to_fraud_detection is True
    assert entry.case_mismatch is False


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-110", "DI-040")
@pytest.mark.parametrize("filename", ["Passport_6086", "MuhammadD_NRIC_front", "MuhammadD_Payslip_Jan2026",
                                      "Credit_Bureau_Report_8027"])
def test_an_individual_document_matching_a_person_on_the_case_is_a_match(anchorpoint, filename):
    case, documents = anchorpoint
    entry = _entry(check_case_match(case, documents), _by_name(documents, filename))

    assert entry.status == DocumentMatchStatus.MATCH
    assert entry.can_proceed_to_fraud_detection is True
    assert any(c.check.startswith("individual") and c.outcome == CheckOutcome.MATCH for c in entry.comparisons)


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-110", "DI-041")
def test_a_document_from_a_different_company_is_a_case_mismatch(anchorpoint, harbor_light):
    case, documents = anchorpoint
    other = _foreign(_by_name(harbor_light, "C014_SourceWealth_BS"), case)

    result = check_case_match(case, [*documents, other])
    entry = _entry(result, other)

    assert entry.case_mismatch is True
    assert entry.status == DocumentMatchStatus.MANUAL_REVIEW
    assert other.id in result.mismatched_document_ids
    [company] = [c for c in entry.comparisons if c.check.startswith("company")]
    assert company.outcome == CheckOutcome.MISMATCH


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-110", "DI-042")
def test_an_individual_document_of_someone_not_on_the_case_is_a_case_mismatch(anchorpoint, harbor_light):
    case, documents = anchorpoint
    passport = _foreign(_by_name(harbor_light, "Passport_5187"), case)  # TEO KAI MING

    entry = _entry(check_case_match(case, [*documents, passport]), passport)

    assert entry.case_mismatch is True
    # Same triage as a foreign company: it looks misfiled, so an officer reviews it.
    assert entry.status == DocumentMatchStatus.MANUAL_REVIEW
    assert entry.can_proceed_to_fraud_detection is False
    [person] = [c for c in entry.comparisons if c.check.startswith("individual")]
    assert person.outcome == CheckOutcome.MISMATCH
    assert person.document_value == "S7273147P"
    assert "TEO KAI MING" in person.reason
    # The case value lists every officer/shareholder it was compared against.
    assert "S9413875H" in (person.case_value or "")


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-110", "DI-043")
def test_a_document_from_another_company_cannot_proceed_to_fraud_detection(anchorpoint, harbor_light):
    case, documents = anchorpoint
    other = _foreign(_by_name(harbor_light, "C014_ListLoans"), case)

    entry = _entry(check_case_match(case, [*documents, other]), other)

    assert entry.can_proceed_to_fraud_detection is False


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-110", "DI-044")
def test_every_matching_document_can_proceed_to_fraud_detection(anchorpoint):
    case, documents = anchorpoint
    result = check_case_match(case, documents)

    assert result.mismatched_document_ids == []
    assert all(m.can_proceed_to_fraud_detection for m in result.documents)


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-110", "DI-045")
def test_a_mismatch_names_the_case_value_and_the_document_value(anchorpoint, harbor_light):
    case, documents = anchorpoint
    other = _foreign(_by_name(harbor_light, "C014_SourceWealth_BS"), case)

    entry = _entry(check_case_match(case, [*documents, other]), other)

    # The document carries a UEN, so the UENs are compared (company name is the fallback).
    [company] = [c for c in entry.comparisons if c.check.startswith("company")]
    assert company.outcome == CheckOutcome.MISMATCH
    assert company.document_value == "201634564N"
    assert company.case_value == "201630968D"
    assert "201634564N" in company.reason


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-110", "DI-046")
def test_without_a_certificate_of_incumbency_no_company_check_runs(anchorpoint, harbor_light):
    case, documents = anchorpoint
    without_incumbency = [d for d in documents if d.document_type != "certificate_of_incumbency"]
    other = _foreign(_by_name(harbor_light, "C014_SourceWealth_BS"), case)

    result = check_case_match(case, [*without_incumbency, other])

    assert result.incumbency.status == IncumbencyStatus.MISSING
    assert result.mismatched_document_ids == []
    assert all(m.status == DocumentMatchStatus.PENDING for m in result.documents)


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-110", "DI-047")
def test_another_companys_incumbency_is_not_used_as_the_reference(anchorpoint, harbor_light):
    # The live run's remark: the wrong incumbency itself is sent to manual
    # review ("N other documents agree on UEN ..."), and nothing else is
    # flagged against it.
    case, documents = anchorpoint
    without_incumbency = [d for d in documents if d.document_type != "certificate_of_incumbency"]
    wrong = _foreign(_by_name(harbor_light, "C014_CertIncumbency"), case)

    result = check_case_match(case, [*without_incumbency, wrong])

    assert result.incumbency.status == IncumbencyStatus.MANUAL_REVIEW
    assert result.mismatched_document_ids == []
    assert _entry(result, wrong).status == DocumentMatchStatus.MANUAL_REVIEW
    assert "201630968D" in " ".join(result.incumbency.reasons)
