from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app import document_repository
from app.models import DocumentRecord, DocumentStatus


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


def test_update_extracted_fields_sends_fields_and_extraction_confidence() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        document_repository.update_extracted_fields(
            "doc-1", {"individual_name": "Jane Tan"}, 0.9
        )

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload == {
            "extracted_fields": {"individual_name": "Jane Tan"},
            "extraction_confidence": 0.9,
        }
        mock_client.table.return_value.update.return_value.eq.assert_called_once_with(
            "id", "doc-1"
        )


def test_update_extracted_fields_includes_fulfilled_requirements_when_given() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        document_repository.update_extracted_fields(
            "doc-1", {"individual_name": "Jane Tan"}, 0.9, ["id_document", "proof_of_address"]
        )

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload == {
            "extracted_fields": {"individual_name": "Jane Tan"},
            "extraction_confidence": 0.9,
            "fulfilled_requirements": ["id_document", "proof_of_address"],
        }


def test_record_classification_sends_fields_with_defaults() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        document_repository.record_classification(
            "doc-1",
            document_type="payslip",
            confidence=0.9,
            classification_outcome="accepted",
        )

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload == {
            "document_type": "payslip",
            "confidence": 0.9,
            "classification_outcome": "accepted",
            "evidence": [],
            "fulfilled_requirements": [],
        }
        mock_client.table.return_value.update.return_value.eq.assert_called_once_with(
            "id", "doc-1"
        )


def test_record_classification_includes_evidence_and_fulfilled_requirements() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        document_repository.record_classification(
            "doc-1",
            document_type="unrecognised",
            confidence=0.2,
            classification_outcome="manual_review",
            evidence=["reads like a shipping manifest"],
            fulfilled_requirements=["id_document"],
        )

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload == {
            "document_type": "unrecognised",
            "confidence": 0.2,
            "classification_outcome": "manual_review",
            "evidence": ["reads like a shipping manifest"],
            "fulfilled_requirements": ["id_document"],
        }


def test_record_escalation_sends_reason() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        document_repository.record_escalation("doc-1", "reads like a shipping manifest")

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload == {"escalation_reason": "reads like a shipping manifest"}
        mock_client.table.return_value.update.return_value.eq.assert_called_once_with(
            "id", "doc-1"
        )


def test_update_metadata_sends_metadata() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        document_repository.update_metadata("doc-1", {"Author": "Jane Tan"})

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload == {"metadata": {"Author": "Jane Tan"}}
        mock_client.table.return_value.update.return_value.eq.assert_called_once_with(
            "id", "doc-1"
        )




def test_list_documents_applies_case_id_and_status_filters() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        query = mock_client.table.return_value.select.return_value
        query.eq.return_value = query
        query.execute.return_value = _execute_result([])

        document_repository.list_documents(case_id="case-1", status=DocumentStatus.ANALYZED)

        query.eq.assert_any_call("case_id", "case-1")
        query.eq.assert_any_call("status", "analyzed")
        assert query.eq.call_count == 2


def _document(**overrides) -> DocumentRecord:
    values = {
        "id": "doc-1",
        "case_id": "case-1",
        "filename": "statement.pdf",
        "storage_path": "case-1/doc-1_statement.pdf",
        "status": DocumentStatus.UPLOADED,
        "uploaded_at": datetime(2026, 8, 26, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return DocumentRecord(**values)


def test_remove_document_deletes_fields_storage_and_row() -> None:
    document = _document()

    with (
        patch("app.document_repository.extracted_fields_repository") as mock_fields,
        patch("app.document_repository.storage_service") as mock_storage,
        patch("app.document_repository.service_supabase_client") as mock_service,
    ):
        mock_client = mock_service.return_value
        mock_client.table.return_value.delete.return_value.eq.return_value.execute.return_value = (
            _execute_result([{"id": "doc-1"}])
        )
        document_repository.remove_document(document)

    mock_fields.delete_fields_for_document.assert_called_once_with("doc-1")
    mock_storage.delete_file.assert_called_once_with("case-1/doc-1_statement.pdf")
    mock_client.table.assert_called_with("documents")
    mock_client.table.return_value.delete.return_value.eq.assert_called_once_with("id", "doc-1")
    mock_client.table.return_value.delete.return_value.eq.return_value.execute.assert_called_once()


def test_remove_document_skips_storage_when_the_file_was_never_stored() -> None:
    with (
        patch("app.document_repository.extracted_fields_repository"),
        patch("app.document_repository.storage_service") as mock_storage,
        patch("app.document_repository.service_supabase_client") as mock_service,
    ):
        mock_service.return_value.table.return_value.delete.return_value.eq.return_value.execute.return_value = (
            _execute_result([{"id": "doc-1"}])
        )
        document_repository.remove_document(_document(storage_path=None))

    mock_storage.delete_file.assert_not_called()


def test_remove_document_still_deletes_the_row_when_storage_delete_fails() -> None:
    with (
        patch("app.document_repository.extracted_fields_repository"),
        patch("app.document_repository.storage_service") as mock_storage,
        patch("app.document_repository.service_supabase_client") as mock_service,
    ):
        mock_client = mock_service.return_value
        mock_client.table.return_value.delete.return_value.eq.return_value.execute.return_value = (
            _execute_result([{"id": "doc-1"}])
        )
        mock_storage.delete_file.side_effect = RuntimeError("object missing")

        document_repository.remove_document(_document())

    mock_client.table.return_value.delete.return_value.eq.return_value.execute.assert_called_once()
