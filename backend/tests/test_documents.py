from __future__ import annotations

import io
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app.config import settings
from app.fraud_detection.font_consistency_v2 import ExtractedField
from app.main import app
from app.models import CaseRecord, DocumentRecord, DocumentStatus
from app.routers.upload import _ingest_one


client = TestClient(app)

# A fixed timestamp built as a real datetime (not a string literal) so it
# satisfies DocumentRecord/CaseRecord's `datetime`-typed fields for static
# type checkers, while still being what Pydantic would parse from the
# equivalent ISO string at runtime.
_TIMESTAMP = datetime.fromisoformat("2026-08-26T00:00:00+00:00")


def _make_document(document_id: str = "doc-1") -> DocumentRecord:
    return DocumentRecord(
        id=document_id,
        case_id="case-1",
        filename="statement.pdf",
        storage_path=f"case-1/{document_id}_statement.pdf",
        status=DocumentStatus.UPLOADED,
        uploaded_at=_TIMESTAMP,
    )


def _make_case(case_id: str = "case-1", greenlink_case_id: str = "GLDB-1") -> CaseRecord:
    return CaseRecord(
        id=case_id,
        greenlink_case_id=greenlink_case_id,
        status="open",
        submission_finalized=False,
        recommended_additional_documents=[],
        created_at=_TIMESTAMP,
        updated_at=_TIMESTAMP,
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
            uploaded_at=_TIMESTAMP,
        )
        updated = original.model_copy(
            update={
                "extracted_fields": {"individual_name": "Jane Tan (corrected)"},
                "extraction_confidence": 1.0,
            }
        )
        mock_repo.get_document.side_effect = [original, updated]
        # First call is the handler's own existing-fields lookup (used to
        # decide which fields were "editable"/human-verified); the field is
        # new here (empty existing set), so it's treated as verified with
        # confidence 1.0. Second call is the final get_document() refetch,
        # which is what actually populates the response body.
        mock_fields_repo.get_extracted_fields.side_effect = [
            [],
            [
                ExtractedField(
                    field_name="individual_name",
                    field_type="scalar",
                    field_value="Jane Tan (corrected)",
                    confidence=1.0,
                )
            ],
        ]

        response = client.patch(
            "/documents/doc-1/extracted-fields",
            json={"extracted_fields": {"individual_name": "Jane Tan (corrected)"}},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["extracted_fields"] == {
        "individual_name": {"value": "Jane Tan (corrected)", "confidence": 1.0}
    }
    assert body["extraction_confidence"] == 1.0

    # fulfilled_requirements recomputed from the corrected fields (see
    # models.compute_fulfilled_requirements) rather than left untouched —
    # here just self-fulfilment since no individual_address was corrected in.
    mock_repo.update_extracted_fields.assert_called_once_with(
        "doc-1", {"individual_name": "Jane Tan (corrected)"}, 1.0, ["payslip"]
    )
    # required_field_names drawn from the document's own document_type
    # ("payslip") so field_category reflects the same required/additional
    # split as the automated pipeline's own save. field_confidences is 1.0
    # for "individual_name" because it has no prior extracted value (new
    # field added during manual review), per update_extracted_fields's
    # was_editable logic.
    mock_fields_repo.replace_extracted_fields.assert_called_once_with(
        "doc-1",
        {"individual_name": "Jane Tan (corrected)"},
        field_confidences={"individual_name": 1.0},
        default_confidence=None,
        required_field_names={"individual_name", "company_name", "net_pay", "company_address"},
    )
    # A manual correction can change requirement coverage (e.g. adding an
    # address that was missing) just as much as the automated pipeline can.
    mock_case_status.sync_submission_finalized.assert_called_once_with("case-1")


def test_update_extracted_fields_preserves_confidence_for_already_reviewed_field() -> None:
    # A field that already had a real (non-blank) value and high confidence
    # wasn't part of this manual review — its saved value happens to equal
    # what's already there, so its original per-field confidence should be
    # kept rather than bumped to 1.0.
    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
        patch("app.routers.documents.case_status"),
    ):
        original = DocumentRecord(
            id="doc-1",
            case_id="case-1",
            filename="payslip.pdf",
            document_type="payslip",
            storage_path="case-1/doc-1_payslip.pdf",
            status=DocumentStatus.ANALYZED,
            uploaded_at=_TIMESTAMP,
        )
        mock_repo.get_document.side_effect = [original, original]
        mock_fields_repo.get_extracted_fields.side_effect = [
            [
                ExtractedField(
                    field_name="individual_name",
                    field_type="scalar",
                    field_value="Jane Tan",
                    confidence=0.98,
                )
            ],
            [],
        ]

        response = client.patch(
            "/documents/doc-1/extracted-fields",
            json={"extracted_fields": {"individual_name": "Jane Tan"}},
        )

    assert response.status_code == 200
    mock_fields_repo.replace_extracted_fields.assert_called_once_with(
        "doc-1",
        {"individual_name": "Jane Tan"},
        field_confidences={"individual_name": 0.98},
        default_confidence=None,
        required_field_names={"individual_name", "company_name", "net_pay", "company_address"},
    )


def test_update_extracted_fields_bumps_confidence_for_low_confidence_existing_field() -> None:
    # A field extraction was unsure about (below EXTRACTION_CONFIDENCE_THRESHOLD)
    # is treated as human-reviewed once it comes through this endpoint, even
    # if the officer just re-saved the same value.
    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
        patch("app.routers.documents.case_status"),
    ):
        original = DocumentRecord(
            id="doc-1",
            case_id="case-1",
            filename="payslip.pdf",
            document_type="payslip",
            storage_path="case-1/doc-1_payslip.pdf",
            status=DocumentStatus.ANALYZED,
            uploaded_at=_TIMESTAMP,
        )
        mock_repo.get_document.side_effect = [original, original]
        mock_fields_repo.get_extracted_fields.side_effect = [
            [
                ExtractedField(
                    field_name="net_pay",
                    field_type="scalar",
                    field_value=5000,
                    confidence=0.4,
                )
            ],
            [],
        ]

        response = client.patch(
            "/documents/doc-1/extracted-fields",
            json={"extracted_fields": {"net_pay": "5000"}},
        )

    assert response.status_code == 200
    mock_fields_repo.replace_extracted_fields.assert_called_once_with(
        "doc-1",
        {"net_pay": "5000"},
        field_confidences={"net_pay": 1.0},
        default_confidence=None,
        required_field_names={"individual_name", "company_name", "net_pay", "company_address"},
    )


def test_update_extracted_fields_treats_blank_existing_value_as_editable() -> None:
    # old_value normalises to "" (the field was previously extracted as
    # None) — that alone should make it editable/reviewable regardless of
    # its recorded confidence.
    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
        patch("app.routers.documents.case_status"),
    ):
        original = DocumentRecord(
            id="doc-1",
            case_id="case-1",
            filename="payslip.pdf",
            document_type="payslip",
            storage_path="case-1/doc-1_payslip.pdf",
            status=DocumentStatus.ANALYZED,
            uploaded_at=_TIMESTAMP,
        )
        mock_repo.get_document.side_effect = [original, original]
        mock_fields_repo.get_extracted_fields.side_effect = [
            [
                ExtractedField(
                    field_name="company_address",
                    field_type="scalar",
                    field_value=None,
                    confidence=0.99,
                )
            ],
            [],
        ]

        response = client.patch(
            "/documents/doc-1/extracted-fields",
            json={"extracted_fields": {"company_address": "1 Raffles Place"}},
        )

    assert response.status_code == 200
    mock_fields_repo.replace_extracted_fields.assert_called_once_with(
        "doc-1",
        {"company_address": "1 Raffles Place"},
        field_confidences={"company_address": 1.0},
        default_confidence=None,
        required_field_names={"individual_name", "company_name", "net_pay", "company_address"},
    )


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


def test_get_document_attaches_extracted_fields() -> None:
    document = _make_document("doc-1")
    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields,
    ):
        mock_repo.get_document.return_value = document
        mock_fields.get_extracted_fields.return_value = [
            ExtractedField(field_name="individual_name", field_type="string", field_value="Jane", confidence=0.88)
        ]

        response = client.get("/documents/doc-1")

    assert response.status_code == 200
    assert response.json()["extracted_fields"]["individual_name"]["value"] == "Jane"


def test_get_document_extracted_fields_failure_returns_502() -> None:
    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields,
    ):
        mock_repo.get_document.return_value = _make_document("doc-1")
        mock_fields.get_extracted_fields.side_effect = RuntimeError("fields down")

        response = client.get("/documents/doc-1")

    assert response.status_code == 502


def test_update_extracted_fields_fetch_and_save_failures() -> None:
    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.get_document.side_effect = RuntimeError("db down")
        assert (
            client.patch(
                "/documents/doc-1/extracted-fields",
                json={"extracted_fields": {"a": "b"}},
            ).status_code
            == 502
        )

    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields,
    ):
        mock_repo.get_document.return_value = _make_document("doc-1")
        mock_fields.replace_extracted_fields.side_effect = RuntimeError("write failed")
        assert (
            client.patch(
                "/documents/doc-1/extracted-fields",
                json={"extracted_fields": {"a": "b"}},
            ).status_code
            == 502
        )


def test_retry_document_fetch_and_enqueue_failures() -> None:
    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.get_document.side_effect = RuntimeError("db down")
        assert client.post("/documents/doc-1/retry").status_code == 502

    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.document_queue") as mock_queue,
    ):
        mock_repo.get_document.return_value = _make_document("doc-1")
        mock_queue.enqueue = AsyncMock(side_effect=RuntimeError("queue down"))
        assert client.post("/documents/doc-1/retry").status_code == 502


def test_download_url_missing_document_and_storage_path() -> None:
    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.get_document.return_value = None
        assert client.get("/documents/doc-1/download").status_code == 404

    missing_path = _make_document("doc-1")
    missing_path.storage_path = None
    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.get_document.return_value = missing_path
        assert client.get("/documents/doc-1/download").status_code == 404


def test_download_url_signed_url_failure() -> None:
    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.storage_service") as mock_storage,
    ):
        mock_repo.get_document.return_value = _make_document("doc-1")
        mock_storage.create_signed_url.side_effect = RuntimeError("sign failed")
        assert client.get("/documents/doc-1/download").status_code == 502


async def test_upload_rejects_blank_filename_direct() -> None:
    upload = UploadFile(
        file=io.BytesIO(b"%PDF-1.4"),
        filename="",
        headers=Headers({"content-type": "application/pdf"}),
    )
    with pytest.raises(HTTPException) as exc:
        await _ingest_one(None, upload)
    assert exc.value.status_code == 400


def test_upload_batch_rejects_too_many_files() -> None:
    with patch("app.routers.upload.settings.MAX_BULK_FILES", 1):
        response = client.post(
            "/documents/upload-batch",
            files=[
                ("files", ("a.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")),
                ("files", ("b.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")),
            ],
        )
    assert response.status_code == 413


def _typed_document(**overrides) -> DocumentRecord:
    fields = {
        "status": DocumentStatus.ANALYZED,
        "document_type": "payslip",
        "confidence": 0.5,
        **overrides,
    }
    return _make_document().model_copy(update=fields)


def test_update_document_type_records_type_and_reruns_extraction() -> None:
    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
        patch("app.routers.documents.case_status") as mock_case_status,
        patch("app.routers.documents.langgraph_pipeline") as mock_pipeline,
    ):
        mock_repo.get_document.return_value = _typed_document()
        mock_fields_repo.get_extracted_fields.return_value = []
        mock_pipeline.rerun_extraction = AsyncMock()

        response = client.patch("/documents/doc-1/document-type", json={"document_type": " NRIC "})

    assert response.status_code == 200
    mock_repo.record_classification.assert_called_once_with(
        "doc-1",
        document_type="nric",
        confidence=1.0,
        classification_outcome="accepted",
        evidence=[],
        fulfilled_requirements=["id_document"],
    )
    args, kwargs = mock_repo.update_status.call_args
    assert args == ("doc-1", DocumentStatus.EXTRACTING)
    assert "processing_started_at" in kwargs
    mock_case_status.sync_submission_finalized.assert_called_once_with("case-1")
    mock_pipeline.rerun_extraction.assert_awaited_once_with("doc-1")


def test_update_document_type_allows_unclassified_failed_document_without_case() -> None:
    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
        patch("app.routers.documents.case_status") as mock_case_status,
        patch("app.routers.documents.langgraph_pipeline") as mock_pipeline,
    ):
        mock_repo.get_document.return_value = _typed_document(
            status=DocumentStatus.FAILED, confidence=None, document_type=None, case_id=None
        )
        mock_fields_repo.get_extracted_fields.return_value = []
        mock_pipeline.rerun_extraction = AsyncMock()

        response = client.patch(
            "/documents/doc-1/document-type", json={"document_type": "unrecognised"}
        )

    assert response.status_code == 200
    mock_case_status.sync_submission_finalized.assert_not_called()
    mock_pipeline.rerun_extraction.assert_awaited_once_with("doc-1")


def test_update_document_type_rejects_unknown_type() -> None:
    with patch("app.routers.documents.document_repository") as mock_repo:
        response = client.patch("/documents/doc-1/document-type", json={"document_type": "id_document"})

    assert response.status_code == 422
    mock_repo.get_document.assert_not_called()


def test_update_document_type_not_found() -> None:
    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.get_document.return_value = None

        response = client.patch("/documents/missing/document-type", json={"document_type": "nric"})

    assert response.status_code == 404


def test_update_document_type_fetch_failure_returns_502() -> None:
    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.get_document.side_effect = RuntimeError("db down")

        response = client.patch("/documents/doc-1/document-type", json={"document_type": "nric"})

    assert response.status_code == 502


def test_update_document_type_conflicts_while_pipeline_is_running() -> None:
    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.get_document.return_value = _typed_document(status=DocumentStatus.PROCESSING)

        response = client.patch("/documents/doc-1/document-type", json={"document_type": "nric"})

    assert response.status_code == 409
    mock_repo.record_classification.assert_not_called()


def test_update_document_type_rejects_confident_classification() -> None:
    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.get_document.return_value = _typed_document(
            confidence=settings.CLASSIFICATION_CONFIDENCE_THRESHOLD
        )

        response = client.patch("/documents/doc-1/document-type", json={"document_type": "nric"})

    assert response.status_code == 400
    mock_repo.record_classification.assert_not_called()


def test_update_document_type_write_failure_returns_502_without_rerun() -> None:
    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.langgraph_pipeline") as mock_pipeline,
    ):
        mock_repo.get_document.return_value = _typed_document()
        mock_repo.record_classification.side_effect = RuntimeError("db down")
        mock_pipeline.rerun_extraction = AsyncMock()

        response = client.patch("/documents/doc-1/document-type", json={"document_type": "nric"})

    assert response.status_code == 502
    mock_pipeline.rerun_extraction.assert_not_called()


# --- POST /documents/{id}/extracted-fields (manual field add) -----------


def _payslip_document(document_id: str = "doc-1") -> DocumentRecord:
    return DocumentRecord(
        id=document_id,
        case_id="case-1",
        filename="payslip.pdf",
        document_type="payslip",
        storage_path=f"case-1/{document_id}_payslip.pdf",
        status=DocumentStatus.ANALYZED,
        uploaded_at=_TIMESTAMP,
    )


def test_add_extracted_field_inserts_and_returns_refetched_document() -> None:
    added = ExtractedField(
        id="field-1",
        field_name="net_pay",
        field_type="number",
        field_value=5000,
        confidence=1.0,
    )

    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
        patch("app.routers.documents.case_status") as mock_case_status,
    ):
        mock_repo.get_document.return_value = _payslip_document()
        # 1st call: the duplicate-name check (no fields yet). 2nd: the
        # re-read that rebuilds documents.extracted_fields. 3rd: the
        # get_document() refetch that builds the response body.
        mock_fields_repo.get_extracted_fields.side_effect = [[], [added], [added]]

        response = client.post(
            "/documents/doc-1/extracted-fields",
            json={"field_name": "net_pay", "field_value": 5000},
        )

    assert response.status_code == 200
    # id is echoed back alongside value/confidence — it's what the frontend
    # targets when deleting the field again.
    assert response.json()["extracted_fields"] == {
        "net_pay": {"id": "field-1", "value": 5000, "confidence": 1.0}
    }

    # net_pay is a required field for a payslip, so it's inserted under that
    # category rather than as a freeform addition.
    mock_fields_repo.add_extracted_field.assert_called_once_with(
        "doc-1",
        "net_pay",
        5000,
        required_field_names={"individual_name", "company_name", "net_pay", "company_address"},
    )
    # Manually entered fields are human-verified, hence confidence 1.0.
    mock_repo.update_extracted_fields.assert_called_once_with(
        "doc-1", {"net_pay": 5000}, 1.0, ["payslip"]
    )
    mock_case_status.sync_submission_finalized.assert_called_once_with("case-1")


def test_add_extracted_field_trims_whitespace_from_name() -> None:
    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
        patch("app.routers.documents.case_status"),
    ):
        mock_repo.get_document.return_value = _payslip_document()
        mock_fields_repo.get_extracted_fields.side_effect = [[], [], []]

        response = client.post(
            "/documents/doc-1/extracted-fields",
            json={"field_name": "  net_pay  ", "field_value": 5000},
        )

    assert response.status_code == 200
    assert mock_fields_repo.add_extracted_field.call_args[0][1] == "net_pay"


def test_add_extracted_field_rejects_blank_name_before_touching_the_database() -> None:
    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
    ):
        response = client.post(
            "/documents/doc-1/extracted-fields",
            json={"field_name": "   ", "field_value": "x"},
        )

    assert response.status_code == 400
    mock_repo.get_document.assert_not_called()
    mock_fields_repo.add_extracted_field.assert_not_called()


def test_add_extracted_field_document_not_found() -> None:
    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_repo.get_document.return_value = None

        response = client.post(
            "/documents/missing-id/extracted-fields",
            json={"field_name": "net_pay", "field_value": 5000},
        )

    assert response.status_code == 404
    mock_fields_repo.add_extracted_field.assert_not_called()


def test_add_extracted_field_rejects_duplicate_name() -> None:
    existing = ExtractedField(
        id="field-1", field_name="net_pay", field_type="number",
        field_value=5000, confidence=1.0,
    )

    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_repo.get_document.return_value = _payslip_document()
        mock_fields_repo.get_extracted_fields.return_value = [existing]

        response = client.post(
            "/documents/doc-1/extracted-fields",
            json={"field_name": "net_pay", "field_value": 9999},
        )

    assert response.status_code == 409
    mock_fields_repo.add_extracted_field.assert_not_called()


def test_add_extracted_field_skips_case_sync_for_caseless_document() -> None:
    document = _payslip_document()
    document.case_id = None

    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
        patch("app.routers.documents.case_status") as mock_case_status,
    ):
        mock_repo.get_document.return_value = document
        mock_fields_repo.get_extracted_fields.side_effect = [[], [], []]

        response = client.post(
            "/documents/doc-1/extracted-fields",
            json={"field_name": "net_pay", "field_value": 5000},
        )

    assert response.status_code == 200
    mock_case_status.sync_submission_finalized.assert_not_called()


def test_add_extracted_field_write_failure_returns_502() -> None:
    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_repo.get_document.return_value = _payslip_document()
        mock_fields_repo.get_extracted_fields.return_value = []
        mock_fields_repo.add_extracted_field.side_effect = RuntimeError("db down")

        response = client.post(
            "/documents/doc-1/extracted-fields",
            json={"field_name": "net_pay", "field_value": 5000},
        )

    assert response.status_code == 502


# --- DELETE /documents/{id}/extracted-fields/{field_id} -----------------


def test_delete_extracted_field_removes_and_recomputes_requirements() -> None:
    doomed = ExtractedField(
        id="field-1", field_name="net_pay", field_type="number",
        field_value=5000, confidence=1.0,
    )
    kept = ExtractedField(
        id="field-2", field_name="individual_name", field_type="string",
        field_value="Jane Tan", confidence=0.9,
    )

    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
        patch("app.routers.documents.case_status") as mock_case_status,
    ):
        mock_repo.get_document.return_value = _payslip_document()
        mock_fields_repo.get_extracted_fields.side_effect = [
            [doomed, kept],  # lookup that resolves field_id
            [kept],          # re-read after the delete
            [kept],          # get_document() refetch
        ]

        response = client.delete("/documents/doc-1/extracted-fields/field-1")

    assert response.status_code == 200
    mock_fields_repo.delete_extracted_field.assert_called_once_with("doc-1", "field-1")
    # Unlike the add path, a deletion doesn't make the document
    # human-verified, so its existing extraction_confidence is carried
    # through rather than bumped to 1.0.
    mock_repo.update_extracted_fields.assert_called_once_with(
        "doc-1", {"individual_name": "Jane Tan"}, None, ["payslip"]
    )
    mock_case_status.sync_submission_finalized.assert_called_once_with("case-1")


def test_delete_extracted_field_document_not_found() -> None:
    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_repo.get_document.return_value = None

        response = client.delete("/documents/missing-id/extracted-fields/field-1")

    assert response.status_code == 404
    mock_fields_repo.delete_extracted_field.assert_not_called()


def test_delete_extracted_field_rejects_field_belonging_to_another_document() -> None:
    other = ExtractedField(
        id="field-9", field_name="net_pay", field_type="number",
        field_value=5000, confidence=1.0,
    )

    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_repo.get_document.return_value = _payslip_document()
        mock_fields_repo.get_extracted_fields.return_value = [other]

        response = client.delete("/documents/doc-1/extracted-fields/field-1")

    assert response.status_code == 404
    mock_fields_repo.delete_extracted_field.assert_not_called()


def test_delete_extracted_field_skips_case_sync_for_caseless_document() -> None:
    document = _payslip_document()
    document.case_id = None
    doomed = ExtractedField(
        id="field-1", field_name="net_pay", field_type="number",
        field_value=5000, confidence=1.0,
    )

    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
        patch("app.routers.documents.case_status") as mock_case_status,
    ):
        mock_repo.get_document.return_value = document
        mock_fields_repo.get_extracted_fields.side_effect = [[doomed], [], []]

        response = client.delete("/documents/doc-1/extracted-fields/field-1")

    assert response.status_code == 200
    mock_case_status.sync_submission_finalized.assert_not_called()


def test_delete_extracted_field_write_failure_returns_502() -> None:
    doomed = ExtractedField(
        id="field-1", field_name="net_pay", field_type="number",
        field_value=5000, confidence=1.0,
    )

    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_repo.get_document.return_value = _payslip_document()
        mock_fields_repo.get_extracted_fields.return_value = [doomed]
        mock_fields_repo.delete_extracted_field.side_effect = RuntimeError("db down")

        response = client.delete("/documents/doc-1/extracted-fields/field-1")

    assert response.status_code == 502


def test_delete_document_removes_it_and_resyncs_the_case() -> None:
    document = _make_document()

    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.case_status") as mock_case_status,
    ):
        mock_repo.get_document.return_value = document

        response = client.delete("/documents/doc-1")

    assert response.status_code == 204
    assert response.content == b""
    mock_repo.remove_document.assert_called_once_with(document)
    mock_case_status.sync_submission_finalized.assert_called_once_with("case-1")


def test_delete_document_rejects_an_approved_document() -> None:
    document = _make_document()
    document.status = DocumentStatus.APPROVED

    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.get_document.return_value = document

        response = client.delete("/documents/doc-1")

    assert response.status_code == 409
    mock_repo.remove_document.assert_not_called()


def test_delete_document_not_found() -> None:
    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.get_document.return_value = None

        response = client.delete("/documents/missing-id")

    assert response.status_code == 404
    mock_repo.remove_document.assert_not_called()


def test_delete_document_without_a_case_skips_checklist_sync() -> None:
    document = _make_document()
    document.case_id = None

    with (
        patch("app.routers.documents.document_repository") as mock_repo,
        patch("app.routers.documents.case_status") as mock_case_status,
    ):
        mock_repo.get_document.return_value = document

        response = client.delete("/documents/doc-1")

    assert response.status_code == 204
    mock_case_status.sync_submission_finalized.assert_not_called()


def test_delete_document_db_failure_returns_502() -> None:
    with patch("app.routers.documents.document_repository") as mock_repo:
        mock_repo.get_document.return_value = _make_document()
        mock_repo.remove_document.side_effect = RuntimeError("db down")

        response = client.delete("/documents/doc-1")

    assert response.status_code == 502
