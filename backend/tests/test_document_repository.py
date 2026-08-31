from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app import document_repository
from app.models import DocumentStatus


def _execute_result(data: list[dict]) -> MagicMock:
    result = MagicMock()
    result.data = data
    return result


def _row(**overrides) -> dict:
    row = {
        "id": "doc-1",
        "case_id": "case-1",
        "filename": "statement.pdf",
        "document_type": None,
        "storage_path": "case-1/doc-1_statement.pdf",
        "status": "uploaded",
        "retry_count": 0,
        "uploaded_at": "2026-08-26T00:00:00Z",
        "queued_at": None,
        "processing_started_at": None,
        "failure_reason": None,
    }
    row.update(overrides)
    return row


def test_create_document_inserts_expected_row_and_returns_record() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        mock_client.table.return_value.insert.return_value.execute.return_value = _execute_result(
            [_row()]
        )

        result = document_repository.create_document(
            "case-1", "statement.pdf", "case-1/doc-1_statement.pdf"
        )

        assert result.id == "doc-1"
        assert result.status == DocumentStatus.UPLOADED
        mock_client.table.assert_called_with("documents")
        inserted_row = mock_client.table.return_value.insert.call_args[0][0]
        assert inserted_row["case_id"] == "case-1"
        assert inserted_row["filename"] == "statement.pdf"
        assert inserted_row["storage_path"] == "case-1/doc-1_statement.pdf"
        assert inserted_row["status"] == "uploaded"
        assert inserted_row["retry_count"] == 0


def test_update_status_sends_only_status_when_no_optional_fields() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        document_repository.update_status("doc-1", DocumentStatus.PROCESSING)

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload == {"status": "processing"}
        mock_client.table.return_value.update.return_value.eq.assert_called_once_with(
            "id", "doc-1"
        )


def test_update_status_includes_every_optional_field_when_given() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        now = datetime.now(timezone.utc)

        document_repository.update_status(
            "doc-1",
            DocumentStatus.FAILED,
            queued_at=now,
            processing_started_at=now,
            retry_count=2,
            failure_reason="boom",
            document_type="payslip",
        )

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload == {
            "status": "failed",
            "queued_at": now.isoformat(),
            "processing_started_at": now.isoformat(),
            "retry_count": 2,
            "failure_reason": "boom",
            "document_type": "payslip",
        }


def test_get_document_returns_none_when_missing() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = (
            _execute_result([])
        )

        assert document_repository.get_document("missing") is None


def test_get_document_returns_record_when_found() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = (
            _execute_result([_row(status="analyzed")])
        )

        result = document_repository.get_document("doc-1")

        assert result is not None
        assert result.id == "doc-1"
        assert result.status == DocumentStatus.ANALYZED
        mock_client.table.return_value.select.return_value.eq.assert_called_once_with(
            "id", "doc-1"
        )


def test_get_documents_in_status_queries_with_enum_values() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        mock_in = mock_client.table.return_value.select.return_value.in_
        mock_in.return_value.execute.return_value = _execute_result([_row(status="queued")])

        results = document_repository.get_documents_in_status(
            [DocumentStatus.QUEUED, DocumentStatus.PROCESSING]
        )

        assert len(results) == 1
        assert results[0].status == DocumentStatus.QUEUED
        mock_in.assert_called_once_with("status", ["queued", "processing"])


def test_list_documents_with_no_filters_does_not_call_eq() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        mock_client.table.return_value.select.return_value.execute.return_value = (
            _execute_result([_row()])
        )

        results = document_repository.list_documents()

        assert len(results) == 1
        mock_client.table.return_value.select.return_value.eq.assert_not_called()


def test_list_documents_applies_case_id_and_status_filters() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        query = mock_client.table.return_value.select.return_value
        query.eq.return_value = query
        query.execute.return_value = _execute_result([])

        document_repository.list_documents(case_id="case-1", status=DocumentStatus.ANALYZED)

        query.eq.assert_any_call("case_id", "case-1")
        query.eq.assert_any_call("status", "analyzed")
        assert query.eq.call_count == 2
