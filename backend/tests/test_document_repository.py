from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from app import document_repository
from app.models import DocumentRecord, DocumentStatus, RoutingStatus


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


def test_update_status_sends_only_the_status_and_a_cleared_reason_when_no_optional_fields() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        document_repository.update_status("doc-1", DocumentStatus.PROCESSING)

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload == {"status": "processing", "failure_reason": None}
        mock_client.table.return_value.update.return_value.eq.assert_called_once_with(
            "id", "doc-1"
        )


def test_sync_case_is_a_noop_for_a_caseless_document() -> None:
    with patch("app.case_stage.sync_case_stage") as mock_case_service:
        document_repository._sync_case(None)

    mock_case_service.assert_not_called()


class _FakeTimer:
    """Stands in for threading.Timer: records the call and runs it only when the test says so."""

    created: list["_FakeTimer"] = []

    def __init__(self, interval, function, args=()):
        self.interval, self.function, self.args, self.daemon = interval, function, args, False
        _FakeTimer.created.append(self)

    def start(self) -> None:
        pass

    def fire(self) -> None:
        self.function(*self.args)


def test_a_burst_of_writes_to_one_case_schedules_a_single_resync() -> None:
    _FakeTimer.created.clear()
    document_repository._sync_timers.clear()
    with (
        patch.object(document_repository, "SYNC_DEBOUNCE_SECONDS", 1.5),
        patch("app.document_repository.threading.Timer", _FakeTimer),
        patch("app.case_stage.sync_case_stage") as mock_case_service,
    ):
        for _ in range(5):
            document_repository._sync_case("case-1")
        document_repository._sync_case("case-2")
        assert [t.args[0] for t in _FakeTimer.created] == ["case-1", "case-2"]
        mock_case_service.assert_not_called()

        _FakeTimer.created[0].fire()
        mock_case_service.assert_called_once_with("case-1")

        # A write after the run has started schedules the next one.
        document_repository._sync_case("case-1")
        assert len(_FakeTimer.created) == 3
    document_repository._sync_timers.clear()


def test_update_status_resyncs_the_case_since_the_stage_follows_document_statuses() -> None:
    with (
        patch("app.document_repository.supabase_client") as mock_client,
        patch("app.case_stage.sync_case_stage") as mock_case_service,
    ):
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = (
            _execute_result([_row(status="analyzed")])
        )

        assert document_repository.update_status("doc-1", DocumentStatus.ANALYZED) is True

    mock_case_service.assert_called_once_with("case-1")


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


@pytest.mark.parametrize(
    "status", [s for s in DocumentStatus if s != DocumentStatus.FAILED], ids=lambda s: s.value
)
def test_update_status_clears_the_failure_reason_for_any_status_but_failed(status: DocumentStatus) -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        document_repository.update_status("doc-1", status)

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload == {"status": status.value, "failure_reason": None}


def test_update_status_to_failed_without_a_reason_leaves_the_stored_reason_alone() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        document_repository.update_status("doc-1", DocumentStatus.FAILED)

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload == {"status": "failed"}


def test_approve_document_accepts_it_and_resyncs_the_case() -> None:
    # Approving resolves manual review (a content guard flag, say), which can tick a checklist item.
    with (
        patch("app.document_repository.supabase_client") as mock_client,
        patch("app.case_stage.sync_case_stage") as mock_case_service,
    ):
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = (
            _execute_result([_row(status="approved", routing_status="accepted")])
        )

        approved = document_repository.approve_document("doc-1")

    mock_client.table.return_value.update.assert_called_once_with({"status": "approved", "routing_status": "accepted"})
    mock_client.table.return_value.update.return_value.eq.assert_called_once_with("id", "doc-1")
    assert approved is not None and approved.status == DocumentStatus.APPROVED
    mock_case_service.assert_called_once_with("case-1")


def test_undo_approval_restores_the_status_and_routing_only_while_still_approved() -> None:
    with (
        patch("app.document_repository.supabase_client") as mock_client,
        patch("app.case_stage.sync_case_stage") as mock_case_service,
    ):
        update = mock_client.table.return_value.update
        update.return_value.eq.return_value.eq.return_value.execute.return_value = _execute_result([_row()])

        document_repository.undo_approval("doc-1", RoutingStatus.MANUAL_REVIEW)

    update.assert_called_once_with({"status": "analyzed", "routing_status": "manual_review"})
    update.return_value.eq.assert_called_once_with("id", "doc-1")
    update.return_value.eq.return_value.eq.assert_called_once_with("status", "approved")
    mock_case_service.assert_called_once_with("case-1")


def test_approve_document_returns_none_when_nothing_was_updated() -> None:
    with (
        patch("app.document_repository.supabase_client") as mock_client,
        patch("app.case_stage.sync_case_stage") as mock_case_service,
    ):
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = (
            _execute_result([])
        )

        assert document_repository.approve_document("missing") is None

    mock_case_service.assert_not_called()


def test_reject_document_updates_status_resyncs_the_case_and_returns_the_row() -> None:
    # A rejected document no longer counts towards the checklist, so this is the one status
    # change that does resync.
    with (
        patch("app.document_repository.supabase_client") as mock_client,
        patch("app.case_stage.sync_case_stage") as mock_case_service,
    ):
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = (
            _execute_result([_row(status="rejected")])
        )

        rejected = document_repository.reject_document("doc-1")

    mock_client.table.return_value.update.assert_called_once_with({"status": "rejected"})
    mock_client.table.return_value.update.return_value.eq.assert_called_once_with("id", "doc-1")
    mock_case_service.assert_called_once_with("case-1")
    assert rejected is not None and rejected.status == DocumentStatus.REJECTED


def test_get_document_returns_none_when_missing() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = (
            _execute_result([])
        )

        assert document_repository.get_document("missing") is None


def test_get_document_returns_record_when_found() -> None:
    with (
        patch("app.document_repository.supabase_client") as mock_client,
        patch("app.document_repository.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = (
            _execute_result([_row(status="analyzed")])
        )
        mock_fields_repo.get_extracted_fields_dicts_for_documents.return_value = {}

        result = document_repository.get_document("doc-1")

        assert result is not None
        assert result.id == "doc-1"
        assert result.status == DocumentStatus.ANALYZED
        mock_client.table.return_value.select.return_value.eq.assert_called_once_with(
            "id", "doc-1"
        )


def test_get_document_populates_extracted_fields_from_the_fields_table() -> None:
    with (
        patch("app.document_repository.supabase_client") as mock_client,
        patch("app.document_repository.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = (
            _execute_result([_row(status="analyzed", extracted_fields={"stale": "legacy"})])
        )
        mock_fields_repo.get_extracted_fields_dicts_for_documents.return_value = {
            "doc-1": {"individual_name": "Jane Tan"}
        }

        result = document_repository.get_document("doc-1")

        assert result is not None
        # Sourced from the extracted_fields table, not the raw column above.
        assert result.extracted_fields == {"individual_name": "Jane Tan"}


def test_get_documents_in_status_queries_with_enum_values() -> None:
    with (
        patch("app.document_repository.supabase_client") as mock_client,
        patch("app.document_repository.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_in = mock_client.table.return_value.select.return_value.in_
        # Paged (fetch_all): ordered by id, a page at a time.
        mock_in.return_value.order.return_value.range.return_value.execute.return_value = _execute_result(
            [_row(status="queued")]
        )
        mock_fields_repo.get_extracted_fields_dicts_for_documents.return_value = {}

        results = document_repository.get_documents_in_status(
            [DocumentStatus.QUEUED, DocumentStatus.PROCESSING]
        )

        assert len(results) == 1
        assert results[0].status == DocumentStatus.QUEUED
        mock_in.assert_called_once_with("status", ["queued", "processing"])


def test_list_documents_with_no_filters_does_not_call_eq() -> None:
    with (
        patch("app.document_repository.supabase_client") as mock_client,
        patch("app.document_repository.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_client.table.return_value.select.return_value.execute.return_value = (
            _execute_result([_row()])
        )
        mock_fields_repo.get_extracted_fields_dicts_for_documents.return_value = {}

        results = document_repository.list_documents()

        assert len(results) == 1
        mock_client.table.return_value.select.return_value.eq.assert_not_called()


def test_update_extracted_fields_sends_extraction_confidence_only() -> None:
    with (
        patch("app.document_repository.supabase_client") as mock_client,
        patch("app.case_stage.sync_case_stage"),
    ):
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = (
            _execute_result([_row()])
        )
        document_repository.update_extracted_fields("doc-1", 0.9)

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload == {
            "extraction_confidence": 0.9,
        }
        mock_client.table.return_value.update.return_value.eq.assert_called_once_with(
            "id", "doc-1"
        )


def test_update_extracted_fields_includes_fulfilled_requirements_when_given() -> None:
    with (
        patch("app.document_repository.supabase_client") as mock_client,
        patch("app.case_stage.sync_case_stage"),
    ):
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = (
            _execute_result([_row()])
        )
        document_repository.update_extracted_fields(
            "doc-1", 0.9, ["id_document", "proof_of_address"]
        )

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload == {
            "extraction_confidence": 0.9,
            "fulfilled_requirements": ["id_document", "proof_of_address"],
        }


def test_record_classification_sends_fields_with_defaults() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        document_repository.record_classification(
            "doc-1",
            document_type="payslip",
            classification_confidence=0.9,
            routing_status="accepted",
        )

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload == {
            "document_type": "payslip",
            "classification_confidence": 0.9,
            "routing_status": "accepted",
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
            classification_confidence=0.2,
            routing_status="manual_review",
            evidence=["reads like a shipping manifest"],
            fulfilled_requirements=["id_document"],
        )

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload == {
            "document_type": "unrecognised",
            "classification_confidence": 0.2,
            "routing_status": "manual_review",
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


def test_ingest_results_are_one_write_of_the_guard_result_and_metadata() -> None:
    with patch("app.document_repository.supabase_client") as mock_client:
        document_repository.update_ingest_results("doc-1", guard_result={"risk": "none"}, metadata={"Author": "Jane Tan"})

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload == {"guard_result": {"risk": "none"}, "metadata": {"Author": "Jane Tan"}}
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


def test_remove_document_deletes_the_file_and_the_row_which_cascades() -> None:
    document = _document()

    with (
        patch("app.document_repository.findings_repository") as mock_findings,
        patch("app.document_repository.storage_service") as mock_storage,
        patch("app.document_repository.service_supabase_client") as mock_service,
    ):
        mock_client = mock_service.return_value
        mock_client.table.return_value.delete.return_value.eq.return_value.execute.return_value = (
            _execute_result([{"id": "doc-1"}])
        )
        document_repository.remove_document(document)

    # The cross-document findings resting on it go; every row keyed by it cascades from the documents delete.
    mock_findings.delete_case_findings_citing.assert_called_once_with(["doc-1"])
    mock_storage.delete_file.assert_called_once_with("case-1/doc-1_statement.pdf")
    assert [c.args for c in mock_client.table.call_args_list] == [("documents",)]
    mock_client.table.return_value.delete.return_value.eq.assert_called_once_with("id", "doc-1")
    mock_client.table.return_value.delete.return_value.eq.return_value.execute.assert_called_once()


def test_remove_document_skips_storage_when_the_file_was_never_stored() -> None:
    with (
        patch("app.document_repository.findings_repository"),
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
        patch("app.document_repository.findings_repository"),
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


def test_remove_document_raises_when_the_row_was_not_actually_deleted() -> None:
    with (
        patch("app.document_repository.findings_repository"),
        patch("app.document_repository.storage_service"),
        patch("app.document_repository.service_supabase_client") as mock_service,
    ):
        mock_service.return_value.table.return_value.delete.return_value.eq.return_value.execute.return_value = (
            _execute_result([])
        )

        with pytest.raises(RuntimeError, match="doc-1"):
            document_repository.remove_document(_document())


def test_remove_documents_is_a_noop_for_an_empty_list() -> None:
    with (
        patch("app.document_repository.findings_repository") as mock_findings,
        patch("app.document_repository.storage_service") as mock_storage,
        patch("app.document_repository.service_supabase_client") as mock_service,
    ):
        document_repository.remove_documents([])

    mock_findings.delete_case_findings_citing.assert_not_called()
    mock_storage.delete_files.assert_not_called()
    mock_service.assert_not_called()


def test_remove_documents_batches_the_file_and_row_deletes() -> None:
    documents = [
        _document(id="doc-1"),
        _document(id="doc-2", storage_path="case-1/doc-2_statement.pdf"),
    ]

    with (
        patch("app.document_repository.findings_repository") as mock_findings,
        patch("app.document_repository.storage_service") as mock_storage,
        patch("app.document_repository.service_supabase_client") as mock_service,
        patch("app.case_stage.sync_case_stage") as mock_case_service,
    ):
        document_repository.remove_documents(documents)

    mock_findings.delete_case_findings_citing.assert_called_once_with(["doc-1", "doc-2"])
    mock_storage.delete_files.assert_called_once_with(
        ["case-1/doc-1_statement.pdf", "case-1/doc-2_statement.pdf"]
    )
    mock_client = mock_service.return_value
    assert [c.args for c in mock_client.table.call_args_list] == [("documents",)]
    mock_client.table.return_value.delete.return_value.in_.assert_called_once_with("id", ["doc-1", "doc-2"])
    # Batch removal skips the per-document case resync (see remove_documents'
    # docstring) — callers doing this in bulk delete the case right after.
    mock_case_service.assert_not_called()


def test_remove_documents_skips_storage_call_when_no_document_has_a_storage_path() -> None:
    documents = [_document(id="doc-1", storage_path=None)]

    with (
        patch("app.document_repository.findings_repository"),
        patch("app.document_repository.storage_service") as mock_storage,
        patch("app.document_repository.service_supabase_client"),
    ):
        document_repository.remove_documents(documents)

    mock_storage.delete_files.assert_not_called()


def test_remove_documents_swallows_a_storage_batch_delete_failure() -> None:
    documents = [_document(id="doc-1")]

    with (
        patch("app.document_repository.findings_repository"),
        patch("app.document_repository.storage_service") as mock_storage,
        patch("app.document_repository.service_supabase_client") as mock_service,
    ):
        mock_storage.delete_files.side_effect = RuntimeError("bucket unreachable")

        document_repository.remove_documents(documents)

    mock_service.return_value.table.return_value.delete.return_value.in_.assert_called_with("id", ["doc-1"])


def test_moved_document_takes_its_notes_and_findings_to_the_new_case() -> None:
    with (
        patch("app.document_repository.supabase_client") as mock_client,
        patch("app.document_repository.notes_repository") as mock_notes,
        patch("app.document_repository.findings_repository") as mock_findings,
        patch("app.document_repository._sync_case"),
    ):
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = (
            _execute_result([{"id": "doc-1"}])
        )
        document_repository.move_document("doc-1", "case-2")
        document_repository.move_documents(["doc-2", "doc-3"], "case-2")

    assert mock_notes.move_with_documents.call_args_list == [
        call(["doc-1"], "case-2"), call(["doc-2", "doc-3"], "case-2"),
    ]
    assert mock_findings.move_with_documents.call_args_list == mock_notes.move_with_documents.call_args_list


def test_document_statuses_are_read_for_100_cases_at_a_time() -> None:
    row = {"id": "d", "case_id": "c", "filename": "a.pdf", "status": "analyzed", "retry_count": 0,
           "uploaded_at": "2026-08-26T00:00:00Z", "queued_at": None, "processing_started_at": None}
    with patch("app.document_repository.fetch_all", return_value=[row]) as fetch:
        documents = document_repository.list_document_statuses([f"case-{n}" for n in range(250)])

    assert fetch.call_count == 3  # 100 + 100 + 50
    assert len(documents) == 3
    assert document_repository.list_document_statuses([]) == []


def test_every_documents_status_is_read_a_page_at_a_time() -> None:
    row = {"id": "d", "case_id": "c", "filename": "a.pdf", "status": "analyzed", "retry_count": 0,
           "uploaded_at": "2026-08-26T00:00:00Z", "queued_at": None, "processing_started_at": None}
    with patch("app.document_repository.fetch_all", return_value=[row, row]):
        assert len(document_repository.list_all_document_statuses()) == 2
