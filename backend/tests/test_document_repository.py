"""
The documents table (document_repository.py), on an in-memory table (tests/support/fake_supabase.py): each test seeds
rows, calls the repository, and asserts what is stored and what comes back, not which query was built.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import call, patch

import pytest

from app import document_repository
from app.models import DocumentRecord, DocumentStatus, RoutingStatus
from tests.support.fake_supabase import FakeSupabase


@pytest.fixture
def db(monkeypatch) -> FakeSupabase:
    fake = FakeSupabase()
    monkeypatch.setattr(document_repository, "supabase_client", fake)
    monkeypatch.setattr(document_repository, "service_supabase_client", lambda: fake)
    return fake


@pytest.fixture
def synced(monkeypatch) -> list[str]:
    """The cases whose stage the repository asked to refresh, in order."""
    cases: list[str] = []
    monkeypatch.setattr(document_repository, "_sync_case", cases.append)
    return cases


@pytest.fixture
def extracted(monkeypatch) -> dict[str, dict]:
    """What the extracted_fields table holds, by document, for the reads that attach it."""
    held: dict[str, dict] = {}
    monkeypatch.setattr(
        document_repository.extracted_fields_repository, "get_extracted_fields_dicts_for_documents",
        lambda ids: {i: held[i] for i in ids if i in held},
    )
    return held


def _row(**overrides) -> dict:
    row = {
        "id": "doc-1",
        "case_id": "case-1",
        "filename": "statement.pdf",
        "document_type": None,
        "classification_confidence": None,
        "routing_status": None,
        "storage_path": "case-1/doc-1_statement.pdf",
        "status": "uploaded",
        "retry_count": 0,
        "uploaded_at": "2026-08-26T00:00:00Z",
        "queued_at": None,
        "processing_started_at": None,
        "failure_reason": None,
        "evidence": [],
        "fulfilled_requirements": [],
        "extraction_confidence": None,
        "fields_reviewed_at": None,
        "escalation_reason": None,
    }
    row.update(overrides)
    return row


def _stored(db: FakeSupabase, document_id: str = "doc-1") -> dict:
    [row] = db.rows("documents", id=document_id)
    return row


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


# --- Writes -----------------------------------------------------------------------------------------------------


def test_create_document_stores_an_uploaded_row_and_returns_it(db, synced) -> None:
    result = document_repository.create_document("case-1", "statement.pdf", "case-1/doc-1_statement.pdf")

    stored = _stored(db, result.id)
    assert (stored["case_id"], stored["filename"], stored["storage_path"]) == (
        "case-1", "statement.pdf", "case-1/doc-1_statement.pdf",
    )
    assert (stored["status"], stored["retry_count"]) == ("uploaded", 0)
    assert result.status == DocumentStatus.UPLOADED
    assert synced == ["case-1"]


def test_update_status_changes_only_the_status_and_clears_the_failure_reason(db, synced) -> None:
    db.add("documents", _row(status="queued", failure_reason="old reason", retry_count=3))

    assert document_repository.update_status("doc-1", DocumentStatus.PROCESSING) is True

    stored = _stored(db)
    assert (stored["status"], stored["failure_reason"], stored["retry_count"]) == ("processing", None, 3)
    assert synced == ["case-1"]  # the stage follows the document statuses


def test_update_status_stores_every_optional_field_when_given(db) -> None:
    db.add("documents", _row())
    now = datetime.now(timezone.utc)

    document_repository.update_status(
        "doc-1", DocumentStatus.FAILED, queued_at=now, processing_started_at=now, retry_count=2,
        failure_reason="boom", document_type="payslip",
    )

    stored = _stored(db)
    assert (stored["status"], stored["failure_reason"], stored["retry_count"], stored["document_type"]) == (
        "failed", "boom", 2, "payslip",
    )
    assert stored["queued_at"] == now.isoformat() and stored["processing_started_at"] == now.isoformat()


@pytest.mark.parametrize(
    "status", [s for s in DocumentStatus if s != DocumentStatus.FAILED], ids=lambda s: s.value
)
def test_update_status_clears_the_failure_reason_for_any_status_but_failed(db, status: DocumentStatus) -> None:
    db.add("documents", _row(status="failed", failure_reason="old reason"))

    document_repository.update_status("doc-1", status)

    assert _stored(db)["failure_reason"] is None


def test_update_status_to_failed_without_a_reason_leaves_the_stored_reason_alone(db) -> None:
    db.add("documents", _row(status="processing", failure_reason="first reason"))

    document_repository.update_status("doc-1", DocumentStatus.FAILED)

    assert _stored(db)["failure_reason"] == "first reason"


def test_update_status_only_moves_a_document_that_is_still_in_an_expected_status(db, synced) -> None:
    db.add("documents", _row(status="analyzed"))

    moved = document_repository.update_status(
        "doc-1", DocumentStatus.PROCESSING, expected_current_statuses=[DocumentStatus.QUEUED],
    )

    assert moved is False
    assert _stored(db)["status"] == "analyzed"
    assert synced == []


def test_update_status_of_a_missing_document_changes_nothing(db, synced) -> None:
    assert document_repository.update_status("missing", DocumentStatus.ANALYZED) is False
    assert synced == []


def test_approve_document_accepts_it_and_resyncs_the_case(db, synced) -> None:
    # Approving resolves manual review (a content guard flag, say), which can tick a checklist item.
    db.add("documents", _row(status="analyzed", routing_status="manual_review"))

    approved = document_repository.approve_document("doc-1")

    stored = _stored(db)
    assert (stored["status"], stored["routing_status"]) == ("approved", "accepted")
    assert approved is not None and approved.status == DocumentStatus.APPROVED
    assert synced == ["case-1"]


def test_approve_document_returns_none_when_nothing_was_updated(db, synced) -> None:
    assert document_repository.approve_document("missing") is None
    assert synced == []


def test_a_document_that_is_not_analyzed_is_not_approved(db, synced) -> None:
    db.add("documents", _row(status="rejected"))

    assert document_repository.approve_document("doc-1") is None
    assert _stored(db)["status"] == "rejected"
    assert synced == []


def test_undo_approval_restores_the_status_and_routing_only_while_still_approved(db, synced) -> None:
    db.add("documents", _row(status="approved", routing_status="accepted"))

    document_repository.undo_approval("doc-1", RoutingStatus.MANUAL_REVIEW)

    stored = _stored(db)
    assert (stored["status"], stored["routing_status"]) == ("analyzed", "manual_review")
    assert synced == ["case-1"]


def test_undo_approval_leaves_a_document_that_moved_on(db, synced) -> None:
    db.add("documents", _row(status="detecting", routing_status="accepted"))

    document_repository.undo_approval("doc-1", RoutingStatus.MANUAL_REVIEW)

    stored = _stored(db)
    assert (stored["status"], stored["routing_status"]) == ("detecting", "accepted")
    assert synced == []


def test_reject_document_updates_status_resyncs_the_case_and_returns_the_row(db, synced) -> None:
    # A rejected document no longer counts towards the checklist, so this is the one status change that resyncs.
    db.add("documents", _row(status="analyzed"))

    rejected = document_repository.reject_document("doc-1")

    assert _stored(db)["status"] == "rejected"
    assert rejected is not None and rejected.status == DocumentStatus.REJECTED
    assert synced == ["case-1"]


def test_update_extracted_fields_stores_the_rounded_confidence_only(db, synced) -> None:
    db.add("documents", _row())

    updated = document_repository.update_extracted_fields("doc-1", 0.91234)

    stored = _stored(db)
    assert stored["extraction_confidence"] == 0.912
    assert stored["fulfilled_requirements"] == []  # not written unless given
    assert updated is not None and synced == ["case-1"]


def test_update_extracted_fields_includes_fulfilled_requirements_when_given(db) -> None:
    db.add("documents", _row())

    document_repository.update_extracted_fields("doc-1", 0.9, ["id_document", "proof_of_address"])

    assert _stored(db)["fulfilled_requirements"] == ["id_document", "proof_of_address"]


def test_update_extracted_fields_of_a_missing_document_returns_none(db, synced) -> None:
    assert document_repository.update_extracted_fields("missing", 0.9) is None
    assert synced == []


def test_record_classification_stores_the_decision_with_empty_defaults(db) -> None:
    db.add("documents", _row())

    document_repository.record_classification(
        "doc-1", document_type="payslip", classification_confidence=0.9, routing_status="accepted",
    )

    stored = _stored(db)
    assert (stored["document_type"], stored["classification_confidence"], stored["routing_status"]) == (
        "payslip", 0.9, "accepted",
    )
    assert (stored["evidence"], stored["fulfilled_requirements"]) == ([], [])


def test_record_classification_includes_evidence_and_fulfilled_requirements(db) -> None:
    db.add("documents", _row())

    document_repository.record_classification(
        "doc-1", document_type="unrecognised", classification_confidence=0.2, routing_status="manual_review",
        evidence=["reads like a shipping manifest"], fulfilled_requirements=["id_document"],
    )

    stored = _stored(db)
    assert stored["routing_status"] == "manual_review"
    assert stored["evidence"] == ["reads like a shipping manifest"]
    assert stored["fulfilled_requirements"] == ["id_document"]


def test_record_escalation_stores_the_reason(db) -> None:
    db.add("documents", _row())

    document_repository.record_escalation("doc-1", "reads like a shipping manifest")

    assert _stored(db)["escalation_reason"] == "reads like a shipping manifest"


def test_ingest_results_are_one_write_of_the_guard_result_and_metadata(db) -> None:
    db.add("documents", _row())

    document_repository.update_ingest_results("doc-1", guard_result={"risk": "none"}, metadata={"Author": "Jane Tan"})

    stored = _stored(db)
    assert stored["guard_result"] == {"risk": "none"} and stored["metadata"] == {"Author": "Jane Tan"}


def test_ingest_results_without_metadata_keep_the_stored_metadata(db) -> None:
    db.add("documents", _row(metadata={"Author": "Jane Tan"}))

    document_repository.update_ingest_results("doc-1", guard_result={"risk": "low"}, metadata=None)

    stored = _stored(db)
    assert stored["guard_result"] == {"risk": "low"} and stored["metadata"] == {"Author": "Jane Tan"}


def test_moved_document_takes_its_notes_and_findings_to_the_new_case(db, synced) -> None:
    for number in (1, 2, 3):
        db.add("documents", _row(id=f"doc-{number}"))
    with (
        patch("app.document_repository.notes_repository") as mock_notes,
        patch("app.document_repository.findings_repository") as mock_findings,
    ):
        document_repository.move_document("doc-1", "case-2")
        document_repository.move_documents(["doc-2", "doc-3"], "case-2")

    assert [_stored(db, f"doc-{n}")["case_id"] for n in (1, 2, 3)] == ["case-2"] * 3
    assert mock_notes.move_with_documents.call_args_list == [
        call(["doc-1"], "case-2"), call(["doc-2", "doc-3"], "case-2"),
    ]
    assert mock_findings.move_with_documents.call_args_list == mock_notes.move_with_documents.call_args_list
    assert synced == ["case-2"]  # one document is resynced; a batch leaves that to its caller


# --- Reads ------------------------------------------------------------------------------------------------------


def test_get_document_returns_none_when_missing(db, extracted) -> None:
    assert document_repository.get_document("missing") is None


def test_get_document_returns_record_when_found(db, extracted) -> None:
    db.add("documents", _row(status="analyzed"))

    result = document_repository.get_document("doc-1")

    assert result is not None
    assert (result.id, result.status) == ("doc-1", DocumentStatus.ANALYZED)


def test_get_document_populates_extracted_fields_from_the_fields_table(db, extracted) -> None:
    db.add("documents", _row(status="analyzed", extracted_fields={"stale": "legacy"}))
    extracted["doc-1"] = {"individual_name": "Jane Tan"}

    result = document_repository.get_document("doc-1")

    # Sourced from the extracted_fields table, not the raw column above.
    assert result is not None and result.extracted_fields == {"individual_name": "Jane Tan"}


def test_get_documents_in_status_reads_only_those_statuses(db, extracted) -> None:
    db.add("documents", _row(id="doc-1", status="queued"))
    db.add("documents", _row(id="doc-2", status="processing"))
    db.add("documents", _row(id="doc-3", status="analyzed"))

    results = document_repository.get_documents_in_status([DocumentStatus.QUEUED, DocumentStatus.PROCESSING])

    assert sorted(r.id for r in results) == ["doc-1", "doc-2"]


def test_list_documents_with_no_filters_returns_every_document(db, extracted) -> None:
    db.add("documents", _row(id="doc-1"))
    db.add("documents", _row(id="doc-2", case_id="case-2"))

    assert sorted(d.id for d in document_repository.list_documents()) == ["doc-1", "doc-2"]


def test_list_documents_applies_case_id_and_status_filters(db, extracted) -> None:
    db.add("documents", _row(id="doc-1", status="analyzed"))
    db.add("documents", _row(id="doc-2", status="queued"))
    db.add("documents", _row(id="doc-3", case_id="case-2", status="analyzed"))

    results = document_repository.list_documents(case_id="case-1", status=DocumentStatus.ANALYZED)

    assert [d.id for d in results] == ["doc-1"]


def test_document_statuses_are_read_for_100_cases_at_a_time() -> None:
    row = {"id": "d", "case_id": "c", "filename": "a.pdf", "status": "analyzed", "retry_count": 0,
           "uploaded_at": "2026-08-26T00:00:00Z", "queued_at": None, "processing_started_at": None}
    with patch("app.document_repository.fetch_all", return_value=[row]) as fetch:
        documents = document_repository.list_document_statuses([f"case-{n}" for n in range(250)])

    assert fetch.call_count == 3  # 100 + 100 + 50
    assert len(documents) == 3
    assert document_repository.list_document_statuses([]) == []


# --- Removal ----------------------------------------------------------------------------------------------------


def test_remove_document_deletes_the_file_and_the_row_which_cascades(db) -> None:
    db.add("documents", _row(id="doc-1"))
    db.add("documents", _row(id="doc-2"))

    with (
        patch("app.document_repository.findings_repository") as mock_findings,
        patch("app.document_repository.storage_service") as mock_storage,
    ):
        document_repository.remove_document(_document())

    # The cross-document findings resting on it go; every row keyed by it cascades from the documents delete.
    mock_findings.delete_case_findings_citing.assert_called_once_with(["doc-1"])
    mock_storage.delete_file.assert_called_once_with("case-1/doc-1_statement.pdf")
    assert [row["id"] for row in db.rows("documents")] == ["doc-2"]


def test_remove_document_skips_storage_when_the_file_was_never_stored(db) -> None:
    db.add("documents", _row(storage_path=None))

    with (
        patch("app.document_repository.findings_repository"),
        patch("app.document_repository.storage_service") as mock_storage,
    ):
        document_repository.remove_document(_document(storage_path=None))

    mock_storage.delete_file.assert_not_called()
    assert db.rows("documents") == []


def test_remove_document_still_deletes_the_row_when_storage_delete_fails(db) -> None:
    db.add("documents", _row())

    with (
        patch("app.document_repository.findings_repository"),
        patch("app.document_repository.storage_service") as mock_storage,
    ):
        mock_storage.delete_file.side_effect = RuntimeError("object missing")
        document_repository.remove_document(_document())

    assert db.rows("documents") == []


def test_remove_document_raises_when_the_row_was_not_actually_deleted(db) -> None:
    with (
        patch("app.document_repository.findings_repository"),
        patch("app.document_repository.storage_service"),
    ):
        with pytest.raises(RuntimeError, match="doc-1"):
            document_repository.remove_document(_document())  # no such row to delete


def test_remove_documents_is_a_noop_for_an_empty_list(db) -> None:
    db.add("documents", _row())

    with (
        patch("app.document_repository.findings_repository") as mock_findings,
        patch("app.document_repository.storage_service") as mock_storage,
    ):
        document_repository.remove_documents([])

    mock_findings.delete_case_findings_citing.assert_not_called()
    mock_storage.delete_files.assert_not_called()
    assert len(db.rows("documents")) == 1


def test_remove_documents_batches_the_file_and_row_deletes(db, synced) -> None:
    for number in (1, 2, 3):
        db.add("documents", _row(id=f"doc-{number}"))
    documents = [
        _document(id="doc-1"),
        _document(id="doc-2", storage_path="case-1/doc-2_statement.pdf"),
    ]

    with (
        patch("app.document_repository.findings_repository") as mock_findings,
        patch("app.document_repository.storage_service") as mock_storage,
    ):
        document_repository.remove_documents(documents)

    mock_findings.delete_case_findings_citing.assert_called_once_with(["doc-1", "doc-2"])
    mock_storage.delete_files.assert_called_once_with(
        ["case-1/doc-1_statement.pdf", "case-1/doc-2_statement.pdf"]
    )
    assert [row["id"] for row in db.rows("documents")] == ["doc-3"]
    # Batch removal skips the per-document case resync: callers doing this in bulk delete the case right after.
    assert synced == []


def test_remove_documents_skips_storage_call_when_no_document_has_a_storage_path(db) -> None:
    db.add("documents", _row())

    with (
        patch("app.document_repository.findings_repository"),
        patch("app.document_repository.storage_service") as mock_storage,
    ):
        document_repository.remove_documents([_document(id="doc-1", storage_path=None)])

    mock_storage.delete_files.assert_not_called()
    assert db.rows("documents") == []


def test_remove_documents_swallows_a_storage_batch_delete_failure(db) -> None:
    db.add("documents", _row())

    with (
        patch("app.document_repository.findings_repository"),
        patch("app.document_repository.storage_service") as mock_storage,
    ):
        mock_storage.delete_files.side_effect = RuntimeError("bucket unreachable")
        document_repository.remove_documents([_document(id="doc-1")])

    assert db.rows("documents") == []


# --- The case stage refresh a request leaves behind -------------------------------------------------------------


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


def test_flushing_runs_the_requests_scheduled_refreshes_now_and_leaves_other_requests_alone(monkeypatch) -> None:
    synced: list[str] = []
    monkeypatch.setattr(document_repository, "SYNC_DEBOUNCE_SECONDS", 60)  # far enough off that only a flush runs it
    monkeypatch.setattr(document_repository, "_sync_now", synced.append)
    mine = document_repository.track_request_syncs()
    document_repository._sync_case("case-1")
    document_repository._sync_case("case-1")  # a second write shares the pending refresh
    document_repository._sync_case("case-other")
    try:
        document_repository.flush_syncs({"case-1"})

        assert synced == ["case-1"]
        assert mine == {"case-1", "case-other"}
        assert "case-1" not in document_repository._sync_timers
        assert "case-other" in document_repository._sync_timers  # another request's, not flushed here
    finally:
        document_repository.flush_syncs({"case-other"})
