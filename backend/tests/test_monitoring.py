from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models import DocumentRecord, DocumentStatus
from app.workers import documents as doc_worker


client = TestClient(app)


def _old_timestamp(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def test_stuck_documents_includes_uploaded_status() -> None:
    stuck_upload = DocumentRecord(
        id="doc-stuck-upload",
        filename="statement.pdf",
        storage_path="case-1/doc-stuck-upload_statement.pdf",
        status=DocumentStatus.UPLOADED,
        uploaded_at=_old_timestamp(settings.STUCK_THRESHOLD_SECONDS + 60),
    )

    with patch("app.monitoring.document_repository") as mock_repo:
        mock_repo.get_documents_in_status.return_value = [stuck_upload]

        response = client.get("/documents/monitoring/stuck")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == "doc-stuck-upload"
    assert body[0]["status"] == "uploaded"

    # confirms UPLOADED is actually included in the query, not just tolerated
    called_statuses = mock_repo.get_documents_in_status.call_args[0][0]
    assert DocumentStatus.UPLOADED in called_statuses


def test_stuck_documents_includes_extracting_status() -> None:
    stuck_extraction = DocumentRecord(
        id="doc-stuck-extraction",
        filename="statement.pdf",
        storage_path="case-1/doc-stuck-extraction_statement.pdf",
        status=DocumentStatus.EXTRACTING,
        uploaded_at=_old_timestamp(settings.STUCK_THRESHOLD_SECONDS + 60),
    )

    with patch("app.monitoring.document_repository") as mock_repo:
        mock_repo.get_documents_in_status.return_value = [stuck_extraction]

        response = client.get("/documents/monitoring/stuck")

    assert response.status_code == 200
    assert [doc["id"] for doc in response.json()] == ["doc-stuck-extraction"]
    queried = mock_repo.get_documents_in_status.call_args[0][0]
    assert DocumentStatus.EXTRACTING in queried


def test_stuck_documents_skips_candidates_not_yet_over_threshold() -> None:
    # get_documents_in_status returns every UPLOADED/QUEUED/PROCESSING
    # document regardless of age — get_stuck_documents itself is what
    # filters out ones that just haven't crossed STUCK_THRESHOLD_SECONDS yet.
    stuck = DocumentRecord(
        id="doc-stuck",
        filename="statement.pdf",
        storage_path="case-1/doc-stuck_statement.pdf",
        status=DocumentStatus.PROCESSING,
        uploaded_at=_old_timestamp(3600),
    )
    fresh = DocumentRecord(
        id="doc-fresh",
        filename="statement.pdf",
        storage_path="case-1/doc-fresh_statement.pdf",
        status=DocumentStatus.PROCESSING,
        uploaded_at=_old_timestamp(1),
    )

    with patch("app.monitoring.document_repository") as mock_repo:
        mock_repo.get_documents_in_status.return_value = [fresh, stuck]

        response = client.get("/documents/monitoring/stuck")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == "doc-stuck"


def test_stuck_documents_db_failure_returns_502() -> None:
    with patch("app.monitoring.document_repository") as mock_repo:
        mock_repo.get_documents_in_status.side_effect = RuntimeError("connection refused")

        response = client.get("/documents/monitoring/stuck")

    assert response.status_code == 502


def test_monitoring_issues_db_failure_returns_502() -> None:
    with (
        patch("app.routers.monitoring.get_stuck_documents", return_value=[]),
        patch("app.routers.monitoring.document_repository") as mock_repo,
    ):
        mock_repo.get_documents_in_status.side_effect = RuntimeError("connection refused")

        response = client.get("/documents/monitoring/issues")

    assert response.status_code == 502


def test_monitoring_issues_surfaces_failed_and_recent_errors() -> None:
    failed_doc = DocumentRecord(
        id="doc-failed",
        filename="statement.pdf",
        storage_path="case-1/doc-failed_statement.pdf",
        status=DocumentStatus.FAILED,
        uploaded_at=_old_timestamp(120),
        failure_reason="Classifier invocation failed after 3 attempts",
    )

    doc_worker._record_error("doc-missing", "Document doc-missing not found when dequeued")

    with patch("app.routers.monitoring.document_repository") as mock_repo, \
         patch("app.routers.monitoring.get_stuck_documents", return_value=[]):
        mock_repo.get_documents_in_status.return_value = [failed_doc]

        response = client.get("/documents/monitoring/issues")

    assert response.status_code == 200
    body = response.json()
    assert body["stuck"] == []
    assert len(body["failed"]) == 1
    assert body["failed"][0]["id"] == "doc-failed"
    assert any(entry["document_id"] == "doc-missing" for entry in body["recent_errors"])
