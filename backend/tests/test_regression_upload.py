"""
Regression tests (SCRUM-206) for Upload Documents (SCRUM-6, Sprint 1) — the
backend side of DI-009 (file over 25MB) and DI-012 / DI-013 (a failed file
reports its own failure without affecting the others).

The refactor in 61d730b removed the tests that covered these from
test_documents.py without a replacement; the behaviour itself is unchanged
in routers/upload.py, so it's pinned here.
"""
from __future__ import annotations

import io
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models import CaseRecord, DocumentRecord, DocumentStatus

client = TestClient(app)
_TIMESTAMP = datetime.fromisoformat("2026-09-30T00:00:00+00:00")


def _case() -> CaseRecord:
    return CaseRecord(id="case-1", greenlink_case_id="GLDB-1", created_at=_TIMESTAMP, updated_at=_TIMESTAMP)


def _document(document_id: str, filename: str) -> DocumentRecord:
    return DocumentRecord(
        id=document_id,
        case_id="case-1",
        filename=filename,
        storage_path=f"case-1/{filename}",
        status=DocumentStatus.UPLOADED,
        uploaded_at=_TIMESTAMP,
    )


def _upload(files: list[tuple[str, bytes]]):
    return client.post(
        "/documents/upload-batch",
        data={"case_id": "case-1"},
        files=[("files", (name, io.BytesIO(content), "application/pdf")) for name, content in files],
    )


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-6", "DI-009")
def test_a_file_over_the_25mb_limit_is_rejected_and_never_stored():
    too_big = b"%PDF-1.4" + b" " * settings.MAX_FILE_SIZE_BYTES
    with (
        patch("app.routers.upload.case_service") as case_service,
        patch("app.routers.upload.storage_service") as storage,
        patch("app.routers.upload.document_repository") as repository,
        patch("app.routers.upload.document_queue") as queue,
    ):
        case_service.get_case.return_value = _case()
        repository.create_document.return_value = _document("doc-ok", "payslip.pdf")
        queue.enqueue = AsyncMock()

        response = _upload([("large-statement.pdf", too_big), ("payslip.pdf", b"%PDF-1.4 payslip")])

    assert response.status_code == 202
    results = {r["filename"]: r for r in response.json()["results"]}
    assert results["large-statement.pdf"]["success"] is False
    assert "limit" in results["large-statement.pdf"]["error"]
    assert results["payslip.pdf"]["success"] is True
    stored = [call.args[1] for call in storage.upload_file.call_args_list]
    assert stored == ["payslip.pdf"]


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-6", "DI-012 / DI-013")
def test_a_storage_failure_fails_only_that_file_with_an_error():
    def store(case_id, filename, content, content_type):
        if filename == "passport.pdf":
            raise RuntimeError("bucket unavailable")
        return f"case-1/{filename}"

    with (
        patch("app.routers.upload.case_service") as case_service,
        patch("app.routers.upload.storage_service") as storage,
        patch("app.routers.upload.document_repository") as repository,
        patch("app.routers.upload.document_queue") as queue,
    ):
        case_service.get_case.return_value = _case()
        storage.upload_file.side_effect = store
        repository.create_document.return_value = _document("doc-payslip", "payslip.pdf")
        queue.enqueue = AsyncMock()

        response = _upload([("payslip.pdf", b"%PDF-1.4 payslip"), ("passport.pdf", b"%PDF-1.4 passport")])

    assert response.status_code == 202
    results = {r["filename"]: r for r in response.json()["results"]}
    assert results["payslip.pdf"]["success"] is True
    assert results["payslip.pdf"]["document_id"] == "doc-payslip"
    assert results["passport.pdf"]["success"] is False
    assert "Failed to store" in results["passport.pdf"]["error"]
    queue.enqueue.assert_awaited_once_with("doc-payslip")
