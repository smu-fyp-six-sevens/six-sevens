"""
Regression tests (SCRUM-206) for approving a document — "Test Cases -
Sprint 2.xlsx", Validate Extracted Data and Support Manual Correction
(SCRUM-20): DI-025 (a completed document can proceed) and DI-026 (a document
with a missing required field can't).

Runs the real POST /documents/{id}/approve endpoint with only the database
faked, using real extraction results captured from Supabase
(tests/fixtures/regression/anchorpoint.json).

REG-001 (approval wasn't blocked by missing required fields) was fixed on
main by SCRUM-26's "block approval for missing required fields". That fix
introduced REG-003: the check treats some complete documents as missing
fields, so they can never be approved — pinned below as strict xfails that
flag themselves once fixed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.fraud_detection.font_consistency import ExtractedField
from app.main import app
from app.models import DocumentRecord, DocumentStatus
from app.case_service import missing_required_fields

client = TestClient(app)
ANCHORPOINT = json.loads((Path(__file__).parent / "fixtures" / "regression" / "anchorpoint.json").read_text())


def _real(filename_part: str) -> tuple[DocumentRecord, list[ExtractedField]]:
    captured = next(d for d in ANCHORPOINT["documents"].values() if filename_part in d["filename"])
    document = DocumentRecord(
        id=captured["id"],
        case_id=captured["case_id"],
        filename=captured["filename"],
        document_type=captured["document_type"],
        classification_confidence=captured["classification_confidence"],
        status=DocumentStatus.ANALYZED,
        uploaded_at=datetime.now(timezone.utc),
    )
    fields = [
        ExtractedField(field_name=name, field_type="string", field_value=f["value"], confidence=f["confidence"] or 0)
        for name, f in captured["extracted_fields"].items()
    ]
    return document, fields


def _approve(document: DocumentRecord, fields: list[ExtractedField]):
    with (
        patch("app.routers._dependencies.document_repository") as lookup,
        patch("app.routers.documents.document_repository") as documents,
        patch("app.routers.documents.extracted_fields_repository") as extracted,
        patch("app.routers.documents._launch_detection"),
    ):
        lookup.get_document.return_value = document
        documents.get_document.return_value = document
        documents.approve_document.return_value = document.model_copy(
            update={"status": DocumentStatus.APPROVED}
        )
        extracted.get_extracted_fields.return_value = fields
        response = client.post(f"/documents/{document.id}/approve")
        return response, documents.approve_document


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-20", "DI-026")
def test_a_document_with_a_missing_required_field_cannot_be_approved():
    document, fields = _real("Statement_128262")  # corporate bank statement
    fields = [f for f in fields if f.field_name != "account_number"]

    response, approve = _approve(document, fields)

    assert response.status_code == 400
    assert response.json()["detail"] == "Cannot approve document. Required fields are missing or empty: account_number"
    approve.assert_not_called()


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-20", "DI-025")
def test_a_document_with_every_required_field_can_be_approved():
    document, fields = _real("Statement_128262")

    response, approve = _approve(document, fields)

    assert response.status_code == 200
    approve.assert_called_once_with(document.id)


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-20", "DI-025")
def test_a_clean_credit_report_can_be_approved():
    document, fields = _real("Credit_Bureau_Report_8027")
    assert any(f.field_name == "defaults" and f.field_value == [] for f in fields)

    assert missing_required_fields(document.document_type, fields) == []


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-20", "DI-025")
@pytest.mark.parametrize("filename", ["SimS_SourceOfWealth", "MuhammadD_Wealth_Statement_scan"])
def test_a_personal_source_of_wealth_statement_can_be_approved(filename):
    document, fields = _real(filename)
    assert any(f.field_name == "account_holder_name" and f.field_value for f in fields)

    assert missing_required_fields(document.document_type, fields) == []
