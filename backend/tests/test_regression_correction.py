"""
Regression tests (SCRUM-206) for the officer-facing side of classification and
retry — "Test Cases - Sprint 2.xlsx" plus the Sprint 1 stories completed in
Sprint 2:

  SCRUM-8   Classify documents: below-threshold documents can't be approved   DI-others
  SCRUM-17  Auto-trigger classification: a failed or stuck document is retried by the officer   MA-002
  SCRUM-19  Auto-trigger extraction: confirming a type re-runs extraction   MA-003

Runs the real FastAPI routes with only the database and the background
pipeline faked. Pipeline behaviour itself lives in test_regression_ai_pipeline.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models import DocumentRecord, DocumentStatus, RoutingStatus

client = TestClient(app)


def _document(**overrides) -> DocumentRecord:
    fields = {
        "id": "doc-1",
        "case_id": "case-1",
        "filename": "C001-P2_Payslip.pdf",
        "document_type": "payslip",
        "status": DocumentStatus.ANALYZED,
        "uploaded_at": datetime.now(timezone.utc),
        "classification_confidence": 0.5,
        "routing_status": RoutingStatus.MANUAL_REVIEW,
        "extracted_fields": {},
    }
    fields.update(overrides)
    return DocumentRecord(**fields)


# --- SCRUM-8: below-threshold documents can't be approved ----------------------------


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-8", "DI-others")
def test_a_below_threshold_document_cannot_be_approved_until_its_type_is_confirmed() -> None:
    document = _document()
    assert document.classification_confidence < settings.CLASSIFICATION_CONFIDENCE_THRESHOLD

    with (
        patch("app.routers._dependencies.document_repository") as lookup,
        patch("app.routers.documents.document_repository") as documents,
        patch("app.routers.documents._launch_detection") as launch,
    ):
        lookup.get_document.return_value = document
        documents.get_document.return_value = document
        response = client.post(f"/documents/{document.id}/approve")

    assert response.status_code == 400
    assert response.json()["detail"] == "Cannot approve document. Confirm the document type first."
    documents.approve_document.assert_not_called()
    launch.assert_not_called()


# --- SCRUM-17: officer-initiated retry, not automatic ---------------------------------


def _retry(document: DocumentRecord):
    with (
        patch("app.routers._dependencies.document_repository") as lookup,
        patch("app.routers.documents.document_repository") as documents,
        patch("app.routers.documents.document_queue") as queue,
    ):
        lookup.get_document.return_value = document
        documents.get_document.return_value = document
        documents.update_status.return_value = True
        queue.enqueue = AsyncMock()
        response = client.post(f"/documents/{document.id}/retry")
        return response, documents, queue


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-17", "MA-002")
def test_an_officer_can_retry_a_failed_document() -> None:
    document = _document(status=DocumentStatus.FAILED, failure_reason="Classifier flagged for manual review")

    response, documents, queue = _retry(document)

    assert response.status_code == 200
    queue.enqueue.assert_awaited_once_with("doc-1")


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-17", "MA-002")
def test_a_processing_document_is_retryable_only_after_the_retry_threshold() -> None:
    long_running = _document(
        status=DocumentStatus.PROCESSING,
        processing_started_at=datetime.now(timezone.utc)
        - timedelta(seconds=settings.STUCK_PROCESSING_RETRY_THRESHOLD_SECONDS + 60),
    )
    response, _, queue = _retry(long_running)
    assert response.status_code == 200
    queue.enqueue.assert_awaited_once_with("doc-1")

    recent = _document(
        status=DocumentStatus.PROCESSING,
        processing_started_at=datetime.now(timezone.utc) - timedelta(seconds=30),
    )
    response, _, queue = _retry(recent)
    assert response.status_code == 409
    queue.enqueue.assert_not_awaited()


# --- SCRUM-19: confirming a type re-runs extraction ------------------------------------


def _confirm_type(document: DocumentRecord, new_type: str):
    with (
        patch("app.routers._dependencies.document_repository") as lookup,
        patch("app.routers.documents.document_repository") as documents,
        patch("app.routers.documents.extracted_fields_repository") as fields,
        patch("app.routers.documents.langgraph_pipeline") as pipeline,
    ):
        lookup.get_document.return_value = document
        documents.get_document.return_value = document
        fields.get_extracted_fields.return_value = []
        pipeline.rerun_extraction = AsyncMock()
        response = client.patch(f"/documents/{document.id}/document-type", json={"document_type": new_type})
        return response, pipeline


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-19", "MA-003")
def test_picking_a_different_type_reruns_extraction_once() -> None:
    response, pipeline = _confirm_type(_document(), "bank_statement")

    assert response.status_code == 200
    pipeline.rerun_extraction.assert_awaited_once()


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-19", "MA-003")
def test_confirming_the_same_type_after_a_generic_first_pass_reruns_extraction() -> None:
    response, pipeline = _confirm_type(_document(), "payslip")

    assert response.status_code == 200
    pipeline.rerun_extraction.assert_awaited_once()
