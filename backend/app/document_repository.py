from __future__ import annotations

import logging
import threading
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

from . import extracted_fields_repository, findings_repository, notes_repository, storage_service
from .models import DocumentRecord, DocumentStatus, RoutingStatus
from .supabase_client import service_supabase_client, supabase_client
from .supabase_rows import fetch_all, row as _row

logger = logging.getLogger(__name__)

# Writes in a burst (a batch upload classifying several documents) share one recompute of the case's stage.
# 0 recomputes after every write (the tests do this).
SYNC_DEBOUNCE_SECONDS = 1.5
_sync_timers: dict[str, threading.Timer] = {}
_sync_lock = threading.Lock()
# The cases the current request scheduled a sync for (see track_request_syncs).
_request_cases: ContextVar[set[str] | None] = ContextVar("_request_cases", default=None)


# Reads skip the large exiftool `metadata` blob; only the metadata checks ask for it.
_LIST_COLUMNS = (
    "id, case_id, filename, document_type, classification_confidence, routing_status, storage_path, status, "
    "retry_count, uploaded_at, queued_at, processing_started_at, failure_reason, evidence, "
    "fulfilled_requirements, extraction_confidence, fields_reviewed_at, escalation_reason"
)


def _sync_case(case_id: str | None) -> None:
    """Schedules a stage refresh for the case; one already pending is shared, since it reads the latest state."""
    if case_id is None:
        return
    if SYNC_DEBOUNCE_SECONDS <= 0:
        _sync_now(case_id)
        return
    requested = _request_cases.get()
    if requested is not None:
        requested.add(case_id)
    with _sync_lock:
        if case_id in _sync_timers:
            return
        timer = _sync_timers[case_id] = threading.Timer(SYNC_DEBOUNCE_SECONDS, _run_scheduled_sync, args=(case_id,))
        timer.daemon = True
    timer.start()


def _run_scheduled_sync(case_id: str) -> None:
    with _sync_lock:
        _sync_timers.pop(case_id, None)  # a write from here on schedules the next run
    _sync_now(case_id)


def _sync_now(case_id: str) -> None:
    """
    Best-effort: the document write already succeeded. Imported locally: case_stage imports this module.
    """
    try:
        from . import case_stage

        case_stage.sync_case_stage(case_id)
    except Exception:
        logger.exception("Document write succeeded but failed to update the stage of case %s", case_id)


def track_request_syncs() -> set[str]:
    """Starts collecting the cases this request (and the threads it starts) schedules a sync for."""
    cases: set[str] = set()
    _request_cases.set(cases)
    return cases


def flush_syncs(case_ids: set[str]) -> None:
    """
    Runs these cases' scheduled refreshes now. Called before a request or push returns: Cloud Run throttles CPU
    once nothing is in flight, so a timer left behind could stall until the instance's next request. Other
    requests' timers are theirs to flush.
    """
    with _sync_lock:
        pending = [(case_id, _sync_timers.pop(case_id)) for case_id in case_ids if case_id in _sync_timers]
    for case_id, timer in pending:
        timer.cancel()
        _sync_now(case_id)


def create_document(
    case_id: str | None, filename: str, storage_path: str
) -> DocumentRecord:
    now = datetime.now(timezone.utc)
    row = {
        "id": str(uuid.uuid4()),
        "case_id": case_id,
        "filename": filename,
        "storage_path": storage_path,
        "status": DocumentStatus.UPLOADED.value,
        "retry_count": 0,
        "uploaded_at": now.isoformat(),
    }
    result = supabase_client.table("documents").insert(row).execute()
    _sync_case(case_id)
    return DocumentRecord(**_row(result.data[0]))


def update_status(
    document_id: str,
    status: DocumentStatus,
    *,
    queued_at: datetime | None = None,
    processing_started_at: datetime | None = None,
    retry_count: int | None = None,
    failure_reason: str | None = None,
    document_type: str | None = None,
    expected_current_statuses: list[DocumentStatus] | None = None,
) -> bool:
    """
    A failure_reason belongs to a failed run: any other status clears it, FAILED sets it (or keeps the old one if none is given).

    expected_current_statuses turns this into an atomic compare-and-swap:
    the WHERE clause requires the row's current status to still be one of
    these, so a caller that read a document's status and now wants to act
    on it (cancel, retry) can't race a concurrent transition â€” e.g. two
    simultaneous cancel requests, or a cancel landing after the pipeline
    already reached ANALYZED. Returns whether the update actually matched a
    row; callers that pass this should check it and treat False as
    "the document's state changed underneath you, refetch and reconsider"
    rather than assuming the transition happened.
    """
    update: dict = {
        "status": status.value,
    }
    if queued_at is not None:
        update["queued_at"] = queued_at.isoformat()
    if processing_started_at is not None:
        update["processing_started_at"] = processing_started_at.isoformat()
    if retry_count is not None:
        update["retry_count"] = retry_count
    if status != DocumentStatus.FAILED:
        update["failure_reason"] = None
    elif failure_reason is not None:
        update["failure_reason"] = failure_reason
    if document_type is not None:
        update["document_type"] = document_type

    query = supabase_client.table("documents").update(update).eq("id", document_id)
    if expected_current_statuses is not None:
        query = query.in_("status", [s.value for s in expected_current_statuses])
    result = query.execute()
    # Any status change can move the case's stage.
    if result.data:
        _sync_case(_row(result.data[0]).get("case_id"))
    return bool(result.data)

def approve_document(document_id: str) -> DocumentRecord | None:
    """
    Approving resolves any manual review, so the document is ACCEPTED. Without it, a document routed to
    manual review that needed nothing saved (a content guard flag, say) stayed there after approval, never
    counting towards the checklist (models.counts_towards_checklist). That can move coverage and the stage,
    so resync the case.
    """
    result = (
        supabase_client.table("documents")
        .update({"status": DocumentStatus.APPROVED.value, "routing_status": RoutingStatus.ACCEPTED.value})
        .eq("id", document_id)
        .execute()
    )
    if not result.data:
        return None
    row = _row(result.data[0])
    _sync_case(row.get("case_id"))
    return DocumentRecord(**row)


def undo_approval(document_id: str, routing_status: RoutingStatus | None) -> None:
    """Puts an approval back (status and the routing_status it replaced), if nothing has moved it on since."""
    result = (
        supabase_client.table("documents")
        .update({
            "status": DocumentStatus.ANALYZED.value,
            "routing_status": routing_status.value if routing_status else None,
        })
        .eq("id", document_id)
        .eq("status", DocumentStatus.APPROVED.value)
        .execute()
    )
    if result.data:
        _sync_case(_row(result.data[0]).get("case_id"))


def _set_detection_status(document_id: str, status: DocumentStatus) -> None:
    result = supabase_client.table("documents").update(
        {"status": status.value}
    ).eq("id", document_id).execute()
    if result.data:
        _sync_case(_row(result.data[0]).get("case_id"))


def mark_detecting(document_id: str) -> None:
    """The Detection Agent has started â€” shown live as "Scanning" (see langgraph/detection/graph.py)."""
    _set_detection_status(document_id, DocumentStatus.DETECTING)


def mark_detected(document_id: str) -> None:
    """The Detection Agent finished and produced a report â€” terminal."""
    _set_detection_status(document_id, DocumentStatus.DETECTED)


def end_unfinished_detection(document_id: str, *, has_report: bool) -> None:
    """
    A detection run crashed or was cancelled. With a report from an earlier run (a rerun that didn't finish),
    back to DETECTED: that report still stands. Otherwise back to APPROVED: still locked, and it can be run again.
    """
    _set_detection_status(document_id, DocumentStatus.DETECTED if has_report else DocumentStatus.APPROVED)


def reject_document(document_id: str) -> DocumentRecord | None:
    result = (
        supabase_client.table("documents")
        .update({"status": DocumentStatus.REJECTED.value})
        .eq("id", document_id)
        .execute()
    )
    if not result.data:
        return None
    _sync_case(_row(result.data[0]).get("case_id"))
    return DocumentRecord(**_row(result.data[0]))


def get_documents_by_ids(
    document_ids: list[str], *, include_fields: bool = True
) -> list[DocumentRecord]:
    if not document_ids:
        return []
    result = supabase_client.table("documents").select(_LIST_COLUMNS).in_("id", document_ids).execute()
    documents = [DocumentRecord(**_row(row)) for row in result.data]
    return attach_extracted_fields(documents) if include_fields else documents


def move_documents(document_ids: list[str], case_id: str) -> None:
    """
    Bulk version of move_document â€” one UPDATE for every document, and no
    per-document case resync: a caller moving N documents resyncs each
    affected case once after the whole batch lands, not once per document
    (see routers/documents.py's bulk_move_documents).
    """
    if not document_ids:
        return
    supabase_client.table("documents").update({"case_id": case_id}).in_(
        "id", document_ids
    ).execute()
    notes_repository.move_with_documents(document_ids, case_id)
    findings_repository.move_with_documents(document_ids, case_id)


def move_document(document_id: str, case_id: str) -> None:
    """
    Reassigns a document to a different case â€” e.g. corrects a document
    uploaded under the wrong case. Syncs both ends: the old case no longer
    counts it (see routers/documents.py's move_document, which also pushes a
    fresh snapshot for the old case over the websocket â€” Realtime's own
    change event only carries the document's new case_id, so the old case
    would otherwise never hear about losing it), the new case now does.
    """
    result = (
        supabase_client.table("documents")
        .update({"case_id": case_id})
        .eq("id", document_id)
        .execute()
    )
    if result.data:
        notes_repository.move_with_documents([document_id], case_id)
        findings_repository.move_with_documents([document_id], case_id)
        _sync_case(case_id)


def record_classification(
    document_id: str,
    *,
    document_type: str | None,
    classification_confidence: float | None,
    routing_status: str,
    evidence: list[str] | None = None,
    fulfilled_requirements: list[str] | None = None,
) -> None:
    """
    Persists the classification-stage decision (type, confidence, and
    whether it was accepted or sent to manual review) independently of
    document status â€” this can happen well before the document reaches a
    terminal ANALYZED/FAILED status, since Extraction is a separate stage
    that runs afterward and can fail on its own.
    """
    update = {
        "document_type": document_type,
        "classification_confidence": classification_confidence,
        "routing_status": routing_status,
        "evidence": evidence or [],
        "fulfilled_requirements": fulfilled_requirements or [],
    }
    result = supabase_client.table("documents").update(update).eq("id", document_id).execute()
    if result.data:
        _sync_case(_row(result.data[0]).get("case_id"))


def update_extracted_fields(
    document_id: str,
    extraction_confidence: float | None,
    fulfilled_requirements: list[str] | None = None,
    *,
    routing_status: str | None = None,
    fields_reviewed: bool = False,
) -> DocumentRecord | None:
    """
    Returns the updated document row, so a caller that must answer with it needn't read it back.

    Persists the Extraction-stage confidence into its own extraction_confidence
    column â€” kept separate from record_classification's confidence (the
    Classifier's confidence in document_type) so neither stage's number
    overwrites the other's; both remain readable after the fact. Also used
    for a loan officer's manual correction (see routers/documents.py's PATCH
    endpoint), which passes extraction_confidence=1.0 since a human has now
    verified the values.

    Does NOT write the field values themselves â€” the extracted_fields table
    (see extracted_fields_repository.replace_extracted_fields, called
    alongside this by every one of this function's callers) is the only
    place those are persisted; get_document/list_documents read them back
    from there, not from a documents column.

    fulfilled_requirements is optional (rather than always written here)
    since callers compute it from the extracted_fields they're passing in
    (see models.compute_fulfilled_requirements) â€” kept out of this
    function's own responsibility so it stays a pure "persist what I was
    given" write.
    """
    update: dict = {
        "extraction_confidence": (
            round(extraction_confidence, 3) if extraction_confidence is not None else None
        ),
    }
    if fulfilled_requirements is not None:
        update["fulfilled_requirements"] = fulfilled_requirements
    # Only a loan officer's "Save fields" passes this â€” their review
    # resolves the document's manual review, so it's now ACCEPTED.
    if routing_status is not None:
        update["routing_status"] = routing_status
    # An officer saved an Other document's fields, which approving it requires.
    if fields_reviewed:
        update["fields_reviewed_at"] = datetime.now(timezone.utc).isoformat()
    result = supabase_client.table("documents").update(update).eq("id", document_id).execute()
    if not result.data:
        return None
    _sync_case(_row(result.data[0]).get("case_id"))
    return DocumentRecord(**_row(result.data[0]))


def clear_fields_review(document_id: str) -> None:
    """The fields are being extracted again, so the officer's earlier review no longer covers them."""
    supabase_client.table("documents").update({"fields_reviewed_at": None}).eq("id", document_id).execute()


def record_escalation(document_id: str, reason: str) -> None:
    """
    Marks that Extraction judged the classified document_type to be wrong
    at least once during processing, triggering a re-run of Intake
    Coordinator (see the langgraph reclassify loop (nodes.record_escalation_node)).
    Persisted independently of the final outcome so the frontend can show
    "classification was re-run" even once the document reaches a terminal
    status under a different (or the same) document_type.
    """
    supabase_client.table("documents").update({"escalation_reason": reason}).eq(
        "id", document_id
    ).execute()


def update_metadata(document_id: str, metadata: dict) -> None:
    """
    Persists the file metadata pulled by ingestion.metadata_extractor
    (exiftool â€” author/creation-date/software/etc.) alongside the raw-text
    extraction step, independent of classification/Extraction succeeding â€”
    it's captured as soon as the file is read, not gated on downstream
    stages.
    """
    supabase_client.table("documents").update({"metadata": metadata}).eq(
        "id", document_id
    ).execute()


def update_guard_result(document_id: str, guard_result: dict) -> None:
    """
    What the content guard found at ingest (guardrails/content_guard.py), for detection's prompt injection
    check. Needs the documents.guard_result jsonb column. Written on every ingest, so a rerun replaces it.
    """
    supabase_client.table("documents").update({"guard_result": guard_result}).eq("id", document_id).execute()


def get_guard_result(document_id: str) -> dict | None:
    """The saved guard_result, or None: not ingested yet, or saving it at ingest failed (ingest doesn't fail for it)."""
    try:
        rows = supabase_client.table("documents").select("guard_result").eq("id", document_id).execute().data
    except Exception as exc:
        logger.warning("Document %s: guard_result could not be read: %s", document_id, exc)
        return None
    row = rows[0] if rows else None
    value = row.get("guard_result") if isinstance(row, dict) else None
    return value if isinstance(value, dict) else None


def _delete_document_children(document_ids: list[str]) -> None:
    """
    What the database's cascades don't cover: the cross-document findings resting on these documents, and the traces
    held in memory. Every table keyed by document (extracted_fields, findings, detection_results, langgraph_trace,
    case_notes) cascades from the documents delete (backend/sql/schema.sql). audit_events stays: it is the trail and
    the cost record.
    """
    from .langgraph import trace

    findings_repository.delete_case_findings_citing(document_ids)
    trace.forget(document_ids)


def remove_document(document: DocumentRecord) -> None:
    """
    Permanently removes a document: the stored file, then the documents row, which takes every row keyed by it
    (_delete_document_children).

    Storage deletion is best-effort. A missing object should not leave the
    row in place; a later retry still deletes the row.
    """
    _delete_document_children([document.id])
    if document.storage_path:
        try:
            storage_service.delete_file(document.storage_path)
        except Exception:
            logger.exception(
                "Failed to delete stored file for document %s at %s",
                document.id,
                document.storage_path,
            )
    result = service_supabase_client().table("documents").delete().eq("id", document.id).execute()
    if not result.data:
        raise RuntimeError(f"Document {document.id} was not deleted")
    _sync_case(document.case_id)


def remove_documents(documents: list[DocumentRecord]) -> None:
    """
    Batch version of remove_document, for deleting every document under a
    case in one pass instead of one round-trip per document per step. Skips
    the per-document _sync_case call â€” callers doing this in bulk (case
    deletion) are removing the case itself right after, so resyncing its
    stage in between would be wasted work.
    """
    if not documents:
        return
    document_ids = [document.id for document in documents]
    _delete_document_children(document_ids)
    storage_paths = [document.storage_path for document in documents if document.storage_path]
    if storage_paths:
        try:
            storage_service.delete_files(storage_paths)
        except Exception:
            logger.exception("Failed to delete stored files for documents %s", document_ids)
    service_supabase_client().table("documents").delete().in_("id", document_ids).execute()


def attach_extracted_fields(documents: list[DocumentRecord]) -> list[DocumentRecord]:
    """
    Populates extracted_fields from the extracted_fields table â€” the only
    place field values are persisted (see update_extracted_fields). None when
    a document has no rows there. For documents read with include_fields=False
    whose fields turn out to be needed after all.
    """
    if not documents:
        return documents
    fields_by_document = extracted_fields_repository.get_extracted_fields_dicts_for_documents(
        [document.id for document in documents]
    )
    for document in documents:
        document.extracted_fields = fields_by_document.get(document.id) or None
    return documents


def get_document(
    document_id: str, *, include_fields: bool = True, include_metadata: bool = False
) -> DocumentRecord | None:
    """
    include_fields=False skips the extracted_fields table round-trip for
    callers that only need status/document_type/etc. (the langgraph pipeline
    nodes, the queue worker, detection's context loader) â€” cheap to opt out
    of since it's a separate query, not part of the documents-table read
    itself. Defaults to True since most callers (API responses, case-match,
    mapper) do need it and forgetting to ask for it would silently produce
    wrong data, whereas forgetting to opt out just costs an extra query.
    include_metadata=True adds the exiftool blob, which only the metadata checks read.
    """
    columns = f"{_LIST_COLUMNS}, metadata" if include_metadata else _LIST_COLUMNS
    result = supabase_client.table("documents").select(columns).eq("id", document_id).execute()
    if not result.data:
        return None
    document = DocumentRecord(**_row(result.data[0]))
    if include_fields:
        attach_extracted_fields([document])
    return document


def get_documents_in_status(
    statuses: list[DocumentStatus], *, include_fields: bool = True
) -> list[DocumentRecord]:
    result = (
        supabase_client.table("documents")
        .select(_LIST_COLUMNS)
        .in_("status", [s.value for s in statuses])
        .execute()
    )
    documents = [DocumentRecord(**_row(row)) for row in result.data]
    return attach_extracted_fields(documents) if include_fields else documents


def list_documents(
    case_id: str | None = None,
    status: DocumentStatus | None = None,
    *,
    include_fields: bool = True,
) -> list[DocumentRecord]:
    query = supabase_client.table("documents").select(_LIST_COLUMNS)
    if case_id is not None:
        query = query.eq("case_id", case_id)
    if status is not None:
        query = query.eq("status", status.value)
    result = query.execute()
    documents = [DocumentRecord(**_row(row)) for row in result.data]
    return attach_extracted_fields(documents) if include_fields else documents


# What the case queue's status rollup reads (the status and monitoring.is_stuck's timestamps), and the type the dashboard counts.
_STATUS_COLUMNS = "id, case_id, filename, document_type, status, retry_count, uploaded_at, queued_at, processing_started_at"
_CASE_CHUNK = 100


def list_document_statuses(case_ids: list[str]) -> list[DocumentRecord]:
    """These cases' documents with only their status columns, a query per 100 cases (and per 1,000 documents)."""
    documents: list[DocumentRecord] = []
    for start in range(0, len(case_ids), _CASE_CHUNK):
        chunk = case_ids[start : start + _CASE_CHUNK]
        rows = fetch_all(lambda: supabase_client.table("documents").select(_STATUS_COLUMNS).in_("case_id", chunk), "id")
        documents.extend(DocumentRecord(**item) for item in rows)
    return documents


def list_all_document_statuses() -> list[DocumentRecord]:
    """Every document's status columns, a page of 1,000 per query: what the queue's status counts roll up."""
    rows = fetch_all(lambda: supabase_client.table("documents").select(_STATUS_COLUMNS), "id")
    return [DocumentRecord(**item) for item in rows]
