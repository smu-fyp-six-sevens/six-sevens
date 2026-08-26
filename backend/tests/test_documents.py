from __future__ import annotations

import io
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from app.main import app
from app.models import DocumentRecord, DocumentStatus


client = TestClient(app)


def _make_document(document_id: str = "doc-1") -> DocumentRecord:
    return DocumentRecord(
        id=document_id,
        case_id="case-1",
        filename="statement.pdf",
        storage_path=f"case-1/{document_id}_statement.pdf",
        status=DocumentStatus.UPLOADED,
        uploaded_at="2026-08-26T00:00:00Z",
    )


def test_upload_batch_partial_failure() -> None:
    with (
        patch("app.routers.upload.storage_service") as mock_storage,
        patch("app.routers.upload.document_repository") as mock_repo,
        patch("app.routers.upload.document_queue") as mock_queue,
    ):
        mock_storage.upload_file.return_value = "case-1/uuid_good.pdf"
        mock_repo.create_document.return_value = _make_document("doc-good")
        mock_queue.enqueue = AsyncMock()

        response = client.post(
            "/documents/upload-batch",
            data={"case_id": "case-1"},
            files=[
                ("files", ("good.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")),
                ("files", ("bad.exe", io.BytesIO(b"MZ"), "application/octet-stream")),
            ],
        )

    assert response.status_code == 202
    body = response.json()
    results = {item["filename"]: item for item in body["results"]}

    assert results["good.pdf"]["success"] is True
    assert results["good.pdf"]["document_id"] == "doc-good"

    assert results["bad.exe"]["success"] is False
    assert results["bad.exe"]["error"] is not None

    mock_queue.enqueue.assert_awaited_once_with("doc-good")


def test_upload_rejects_unknown_case_id() -> None:
    with (
        patch("app.routers.upload.storage_service") as mock_storage,
        patch("app.routers.upload.document_repository") as mock_repo,
    ):
        mock_storage.upload_file.return_value = "does-not-exist/uuid_good.pdf"
        mock_repo.create_document.side_effect = APIError(
            {
                "message": 'insert or update on table "documents" violates foreign key '
                'constraint "documents_case_id_fkey"',
                "code": "23503",
                "hint": None,
                "details": 'Key is not present in table "cases".',
            }
        )

        response = client.post(
            "/documents/upload",
            data={"case_id": "does-not-exist"},
            files={"file": ("good.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )

    assert response.status_code == 400
    assert "does-not-exist" in response.json()["detail"]


def test_get_document_not_found() -> None:
    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.get_document.return_value = None

        response = client.get("/documents/missing-id")

    assert response.status_code == 404


def test_get_document_download_url() -> None:
    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.storage_service") as mock_storage,
    ):
        mock_repo.get_document.return_value = _make_document("doc-1")
        mock_storage.create_signed_url.return_value = "https://example.supabase.co/signed/doc-1"

        response = client.get("/documents/doc-1/download")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == "doc-1"
    assert body["signed_url"] == "https://example.supabase.co/signed/doc-1"
    assert body["expires_in"] == 300
