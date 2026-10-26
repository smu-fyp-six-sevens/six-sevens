"""
Regression tests (SCRUM-246) for updating and deleting cases and documents —
"Test Cases - Sprint 3.xlsx", Case Management sheet: Update Case
(SCRUM-116) CM-014 – CM-023 and Delete Case/Documents (SCRUM-117)
CM-024 – CM-028.

Backend half: the real PATCH /cases, DELETE /cases and DELETE /documents
endpoints with only the database faked, and the real checklist rebuilt from
GLDB-02's captured documents. The UI half (edit mode, cancel, confirmation
dialogs, what the queue shows) is in frontend/e2e/specs/case-management.spec.ts.
Existing tests in test_cases.py / test_documents.py that cover these cases
are tagged sprint3 centrally in conftest.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import ANY, patch

import pytest
from fastapi.testclient import TestClient

from app.case_service import build_summary
from app.main import app
from app.models import CaseDocumentsStatus, CaseRecord, CaseSummary, DocumentRecord, DocumentStatus

client = TestClient(app)
FIXTURES = Path(__file__).parent / "fixtures" / "regression"
_CREATED = datetime(2026, 8, 26, tzinfo=timezone.utc)


def _case(company_name: str = "IRONCLAD SECURITY") -> CaseRecord:
    return CaseRecord(
        id="case-1", greenlink_case_id="GLDB-01", company_name=company_name, status="open",
        submission_finalized=False, created_at=_CREATED, updated_at=_CREATED,
    )


def _patch_company_name(company_name: str):
    """PATCH /cases/case-1; returns the response and the (mocked) case_service.update_case."""
    with (
        patch("app.routers._dependencies.case_service") as lookup,
        patch("app.routers.cases.case_service") as service,
    ):
        lookup.get_case.return_value = _case()

        def summary_after_update(case_id: str) -> CaseSummary:
            saved_name = service.update_case.call_args.args[1]
            return CaseSummary(**_case(saved_name).model_dump(), documents_status=CaseDocumentsStatus.NO_DOCUMENTS)

        service.get_case_summary.side_effect = summary_after_update
        response = client.patch("/cases/case-1", json={"company_name": company_name})
    return response, service.update_case


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-116", "CM-015 / CM-023")
def test_the_company_name_is_saved_and_returned():
    response, update = _patch_company_name("IRONCLAD SECURITY SERVICES PTE LTD")

    assert response.status_code == 200
    assert response.json()["company_name"] == "IRONCLAD SECURITY SERVICES PTE LTD"
    update.assert_called_once_with("case-1", "IRONCLAD SECURITY SERVICES PTE LTD")


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-116", "CM-020 / CM-021")
@pytest.mark.parametrize("company_name", ["", "   "])
def test_an_empty_or_blank_company_name_is_not_saved(company_name):
    response, update = _patch_company_name(company_name)

    assert response.status_code == 422
    update.assert_not_called()


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-116", "CM-022")
def test_a_company_name_of_exactly_100_characters_is_saved():
    response, update = _patch_company_name("A" * 100)

    assert response.status_code == 200
    update.assert_called_once()


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-116", "CM-022")
def test_a_company_name_over_100_characters_is_not_saved():
    response, update = _patch_company_name("A" * 766)

    assert response.status_code == 422
    update.assert_not_called()


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-116", "CM-022")
def test_a_case_cannot_be_created_with_a_company_name_over_100_characters():
    with patch("app.routers.cases.case_service") as service:
        service.create_case.return_value = _case("A" * 100)
        too_long = client.post("/cases", json={"company_name": "A" * 101})
        exactly_100 = client.post("/cases", json={"company_name": "A" * 100})

    assert too_long.status_code == 422
    service.create_case.assert_called_once_with("A" * 100, ANY)
    assert exactly_100.status_code == 201


# --- SCRUM-117 Delete case / documents --------------------------------------------


def _document(status: DocumentStatus) -> DocumentRecord:
    return DocumentRecord(id="doc-1", case_id="case-1", filename="C035_NOA.pdf", status=status,
                          uploaded_at=datetime.now(timezone.utc))


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-117", "CM-024")
def test_a_deleted_document_is_removed_and_its_link_then_answers_not_found():
    document = _document(DocumentStatus.ANALYZED)
    with (
        patch("app.routers._dependencies.document_repository") as lookup,
        patch("app.routers.documents.document_repository") as repo,
    ):
        lookup.get_document.return_value = document
        deleted = client.delete("/documents/doc-1")
        lookup.get_document.return_value = None
        repo.get_document.return_value = None
        reopened = client.get("/documents/doc-1")

    assert deleted.status_code == 204
    repo.remove_document.assert_called_once_with(document)
    assert reopened.status_code == 404
    assert reopened.json()["detail"] == "Document not found"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-117", "CM-025")
def test_a_deleted_case_is_removed_and_its_link_then_answers_not_found():
    with (
        patch("app.routers._dependencies.case_service") as lookup,
        patch("app.routers.cases.case_service") as service,
        patch("app.routers.cases.document_repository") as documents,
    ):
        lookup.get_case.return_value = _case()
        documents.list_documents.return_value = [_document(DocumentStatus.ANALYZED)]
        deleted = client.delete("/cases/case-1")
        service.get_case_summary.return_value = None
        reopened = client.get("/cases/case-1")

    assert deleted.status_code == 204
    service.delete_case.assert_called_once_with("case-1")
    assert reopened.status_code == 404
    assert reopened.json()["detail"] == "Case not found"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-117", "CM-026")
def test_deleting_the_document_that_fulfilled_a_requirement_reverts_the_checklist():
    fixture = json.loads((FIXTURES / "anchorpoint.json").read_text())
    case = CaseRecord(**fixture["case"])
    documents = [DocumentRecord(**row) for row in fixture["rows"]]
    noa = [d for d in documents if d.document_type == "notice_of_assessment"]
    assert len(noa) == 1

    before = build_summary(case, documents)
    after = build_summary(case, [d for d in documents if d not in noa])

    assert "notice_of_assessment" in before.fulfilled_requirements
    assert "notice_of_assessment" not in after.fulfilled_requirements


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-117", "CM-027")
@pytest.mark.parametrize("status", [DocumentStatus.QUEUED, DocumentStatus.PROCESSING, DocumentStatus.EXTRACTING,
                                    DocumentStatus.DETECTING])
def test_a_document_still_being_processed_cannot_be_deleted(status):
    with (
        patch("app.routers._dependencies.document_repository") as lookup,
        patch("app.routers.documents.document_repository") as repo,
    ):
        lookup.get_document.return_value = _document(status)
        response = client.delete("/documents/doc-1")

    assert response.status_code == 409
    repo.remove_document.assert_not_called()
