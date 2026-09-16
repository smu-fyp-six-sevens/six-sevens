from __future__ import annotations

import io
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from app.config import settings
from app.main import app
from app.models import CaseRecord, DocumentRecord, DocumentStatus


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


def _make_case(case_id: str = "case-1", greenlink_case_id: str = "GLDB-1") -> CaseRecord:
    return CaseRecord(
        id=case_id,
        greenlink_case_id=greenlink_case_id,
        status="open",
        submission_finalized=False,
        recommended_additional_documents=[],
        created_at="2026-08-26T00:00:00Z",
        updated_at="2026-08-26T00:00:00Z",
    )


def test_upload_batch_partial_failure() -> None:
    with (
        patch("app.routers.upload.case_repository") as mock_case_repo,
        patch("app.routers.upload.storage_service") as mock_storage,
        patch("app.routers.upload.document_repository") as mock_repo,
        patch("app.routers.upload.document_queue") as mock_queue,
    ):
        mock_case_repo.get_case.return_value = _make_case("case-1")
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


def test_upload_batch_rejects_no_files() -> None:
    """
    files uses File(default_factory=list) rather than File(...) specifically
    so this reaches our own `if not files` check (a clean 400) instead of
    FastAPI's generic 422 required-field validation error, which fires
    before the function body ever runs when the field is required.
    """
    response = client.post("/documents/upload-batch", data={"case_id": "case-1"})

    assert response.status_code == 400
    assert response.json()["detail"] == "At least one file is required"


def test_upload_rejects_unknown_case_id_before_touching_storage() -> None:
    """
    The case-existence check must run before any storage write, so an
    invalid case_id never gets a Storage object/folder created under it.
    """
    with (
        patch("app.routers.upload.case_repository") as mock_case_repo,
        patch("app.routers.upload.storage_service") as mock_storage,
        patch("app.routers.upload.document_repository") as mock_repo,
    ):
        mock_case_repo.get_case.return_value = None

        response = client.post(
            "/documents/upload",
            data={"case_id": "does-not-exist"},
            files={"file": ("good.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )

    assert response.status_code == 400
    assert "does-not-exist" in response.json()["detail"]
    mock_storage.upload_file.assert_not_called()
    mock_repo.create_document.assert_not_called()


def test_upload_allows_missing_case_id_unassigned_folder() -> None:
    """No case_id at all is still allowed (goes to the 'unassigned' folder) —
    the existence check only applies when a case_id is actually provided."""
    with (
        patch("app.routers.upload.case_repository") as mock_case_repo,
        patch("app.routers.upload.storage_service") as mock_storage,
        patch("app.routers.upload.document_repository") as mock_repo,
        patch("app.routers.upload.document_queue") as mock_queue,
    ):
        mock_storage.upload_file.return_value = "unassigned/uuid_good.pdf"
        mock_repo.create_document.return_value = _make_document("doc-good")
        mock_queue.enqueue = AsyncMock()

        response = client.post(
            "/documents/upload",
            files={"file": ("good.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )

    assert response.status_code == 202
    mock_case_repo.get_case.assert_not_called()


def test_upload_cleans_up_storage_on_fk_violation_race() -> None:
    """
    Defense-in-depth: even if the case existed at check time but is gone by
    the time the document row insert runs (a race), cleanup still fires.
    """
    with (
        patch("app.routers.upload.case_repository") as mock_case_repo,
        patch("app.routers.upload.storage_service") as mock_storage,
        patch("app.routers.upload.document_repository") as mock_repo,
    ):
        mock_case_repo.get_case.return_value = _make_case("case-1")
        mock_storage.upload_file.return_value = "case-1/uuid_good.pdf"
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
            data={"case_id": "case-1"},
            files={"file": ("good.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )

    assert response.status_code == 400
    assert "case-1" in response.json()["detail"]
    mock_storage.delete_file.assert_called_once_with("case-1/uuid_good.pdf")


def test_upload_cleans_up_storage_on_generic_db_failure() -> None:
    with (
        patch("app.routers.upload.case_repository") as mock_case_repo,
        patch("app.routers.upload.storage_service") as mock_storage,
        patch("app.routers.upload.document_repository") as mock_repo,
    ):
        mock_case_repo.get_case.return_value = _make_case("case-1")
        mock_storage.upload_file.return_value = "case-1/uuid_good.pdf"
        mock_repo.create_document.side_effect = RuntimeError("transient DB error")

        response = client.post(
            "/documents/upload",
            data={"case_id": "case-1"},
            files={"file": ("good.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )

    assert response.status_code == 502
    mock_storage.delete_file.assert_called_once_with("case-1/uuid_good.pdf")


def test_upload_does_not_clean_up_storage_when_enqueue_fails() -> None:
    with (
        patch("app.routers.upload.case_repository") as mock_case_repo,
        patch("app.routers.upload.storage_service") as mock_storage,
        patch("app.routers.upload.document_repository") as mock_repo,
        patch("app.routers.upload.document_queue") as mock_queue,
    ):
        mock_case_repo.get_case.return_value = _make_case("case-1")
        mock_storage.upload_file.return_value = "case-1/uuid_good.pdf"
        mock_repo.create_document.return_value = _make_document("doc-good")
        mock_queue.enqueue = AsyncMock(side_effect=RuntimeError("queue full"))

        response = client.post(
            "/documents/upload",
            data={"case_id": "case-1"},
            files={"file": ("good.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )

    assert response.status_code == 502
    mock_storage.delete_file.assert_not_called()


def test_get_document_not_found() -> None:
    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.get_document.return_value = None

        response = client.get("/documents/missing-id")

    assert response.status_code == 404


def test_list_documents_db_failure_returns_502() -> None:
    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.list_documents.side_effect = RuntimeError("connection refused")

        response = client.get("/documents")

    assert response.status_code == 502


def test_get_document_db_failure_returns_502() -> None:
    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.get_document.side_effect = RuntimeError("connection refused")

        response = client.get("/documents/doc-1")

    assert response.status_code == 502


def test_get_document_download_url_db_failure_returns_502() -> None:
    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.get_document.side_effect = RuntimeError("connection refused")

        response = client.get("/documents/doc-1/download")

    assert response.status_code == 502


def test_retry_document_not_found() -> None:
    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.get_document.return_value = None

        response = client.post("/documents/missing-id/retry")

    assert response.status_code == 404


def test_retry_document_rejects_already_analyzed() -> None:
    document = _make_document("doc-1")
    document.status = DocumentStatus.ANALYZED

    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.get_document.return_value = document

        response = client.post("/documents/doc-1/retry")

    assert response.status_code == 400


def test_retry_document_requeues_stuck_processing_document() -> None:
    stuck = _make_document("doc-1")
    stuck.status = DocumentStatus.PROCESSING
    stuck.retry_count = 0
    requeued = _make_document("doc-1")
    requeued.status = DocumentStatus.QUEUED
    requeued.retry_count = 1

    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.document_queue") as mock_queue,
    ):
        mock_repo.get_document.side_effect = [stuck, requeued]
        mock_queue.enqueue = AsyncMock()

        response = client.post("/documents/doc-1/retry")

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    mock_repo.update_status.assert_called_once_with(
        "doc-1", DocumentStatus.QUEUED, retry_count=1
    )
    mock_queue.enqueue.assert_awaited_once_with("doc-1")


def test_retry_document_allows_retrying_failed_document() -> None:
    failed = _make_document("doc-1")
    failed.status = DocumentStatus.FAILED
    failed.failure_reason = "Classifier flagged for manual review"
    failed.retry_count = 2

    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.document_queue") as mock_queue,
    ):
        mock_repo.get_document.side_effect = [failed, failed]
        mock_queue.enqueue = AsyncMock()

        response = client.post("/documents/doc-1/retry")

    assert response.status_code == 200
    mock_repo.update_status.assert_called_once_with(
        "doc-1", DocumentStatus.QUEUED, retry_count=3
    )


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
    assert body["expires_in"] == settings.SIGNED_URL_EXPIRY_SECONDS


def test_update_extracted_fields_success() -> None:
    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
        patch("app.routers.documents.case_status") as mock_case_status,
    ):
        original = DocumentRecord(
            id="doc-1",
            case_id="case-1",
            filename="payslip.pdf",
            document_type="payslip",
            storage_path="case-1/doc-1_payslip.pdf",
            status=DocumentStatus.ANALYZED,
            uploaded_at="2026-08-26T00:00:00Z",
        )
        updated = original.model_copy(
            update={
                "extracted_fields": {"individual_name": "Jane Tan (corrected)"},
                "extraction_confidence": 1.0,
            }
        )
        mock_repo.get_document.side_effect = [original, updated]

        response = client.patch(
            "/documents/doc-1/extracted-fields",
            json={"extracted_fields": {"individual_name": "Jane Tan (corrected)"}},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["extracted_fields"] == {"individual_name": "Jane Tan (corrected)"}
    assert body["extraction_confidence"] == 1.0

    # fulfilled_requirements recomputed from the corrected fields (see
    # models.compute_fulfilled_requirements) rather than left untouched —
    # here just self-fulfilment since no individual_address was corrected in.
    mock_repo.update_extracted_fields.assert_called_once_with(
        "doc-1", {"individual_name": "Jane Tan (corrected)"}, 1.0, ["payslip"]
    )
    # required_field_names drawn from the document's own document_type
    # ("payslip") so field_category reflects the same required/additional
    # split as the automated pipeline's own save.
    mock_fields_repo.replace_extracted_fields.assert_called_once_with(
        "doc-1",
        {"individual_name": "Jane Tan (corrected)"},
        default_confidence=1.0,
        required_field_names={"individual_name", "company_name", "net_pay", "company_address"},
    )
    # A manual correction can change requirement coverage (e.g. adding an
    # address that was missing) just as much as the automated pipeline can.
    mock_case_status.sync_submission_finalized.assert_called_once_with("case-1")


def test_update_extracted_fields_not_found() -> None:
    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_repo.get_document.return_value = None

        response = client.patch(
            "/documents/missing-id/extracted-fields",
            json={"extracted_fields": {"a": "b"}},
        )

    assert response.status_code == 404
    mock_fields_repo.replace_extracted_fields.assert_not_called()
