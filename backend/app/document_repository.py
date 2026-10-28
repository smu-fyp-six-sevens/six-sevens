from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone

from . import extracted_fields_repository, finding_reviews_repository, notes_repository, storage_service
from .models import DocumentRecord, DocumentStatus
from .supabase_client import service_supabase_client, supabase_client
from .supabase_rows import fetch_all, row as _row

logger = logging.getLogger(__name__)

# Writes in a burst (a batch upload classifying several documents) share one recompute of the case's
# submission_finalized flag. 0 recomputes after every write (the tests do this).
SYNC_DEBOUNCE_SECONDS = 1.5
_sync_timers: dict[str, threading.Timer] = {}
_sync_lock = threading.Lock()


# Reads skip the large exiftool `metadata` blob and the retired extracted_fields column; only the metadata checks ask for it.
_LIST_COLUMNS = (
    "id, case_id, filename, document_type, classification_confidence, routing_status, storage_path, status, "
    "retry_count, uploaded_at, queued_at, processing_started_at, failure_reason, evidence, "
    "fulfilled_requirements, extraction_confidence, fields_reviewed_at, escalation_reason"
)


def _sync_case(case_id: str | None) -> None:
    """Schedules the refresh below; a case with one already pending shares it, since it reads the latest state."""
    if case_id is None:
        return
    if SYNC_DEBOUNCE_SECONDS <= 0:
        _sync_case_now(case_id)
        return
    with _sync_lock:
        if case_id in _sync_timers:
            return
        timer = _sync_timers[case_id] = threading.Timer(SYNC_DEBOUNCE_SECONDS, _run_scheduled_sync, args=(case_id,))
        timer.daemon = True
    timer.start()


def _run_scheduled_sync(case_id: str) -> None:
    with _sync_lock:
        _sync_timers.pop(case_id, None)  # a write from here on schedules the next run
    _sync_case_now(case_id)


def has_pending_syncs() -> bool:
    return bool(_sync_timers)


def flush_pending_syncs() -> None:
    """
    Runs every scheduled refresh now. Called before a request or push returns: Cloud Run throttles CPU
    once nothing is in flight, so a timer left behind could stall until the instance's next request.
    """
    with _sync_lock:
        pending = list(_sync_timers.items())
        _sync_timers.clear()
    for case_id, timer in pending:
        timer.cancel()
        _sync_case_now(case_id)


def _sync_case_now(case_id: str | None) -> None:
    """
    Best-effort refresh of the case-level submission_finalized cache after a
    write that can change a document's routing_status or
    fulfilled_requirements. Never raises: the document write above already
    succeeded and is the source of truth, and get_case_summary/build_summary
    always recompute requirement coverage live from the documents themselves
    rather than trusting this cached flag — so a failed resync here just
    leaves it stale until the next successful write to this case's documents
    self-heals it, not something worth failing the caller's request over.

    Imports case_service locally rather than at module level — case_service
    itself imports this module (it needs document_repository's own CRUD to
    assemble a case's documents), and this is the only thing here that needs
    case_service, so deferring it avoids a module-level circular import
    between the two.
    """
    if case_id is None:
        return
    try:
        from . import case_service

        case_service.sync_submission_finalized(case_id)
    except Exception:
        logger.exception("Document write succeeded but failed to resync case %s", case_id)


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
    on it (cancel, retry) can't race a concurrent transition — e.g. two
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
    # No case resync here. Coverage depends on routing_status, the confirmed type,
    # rejection and the fields (see models.counts_towards_checklist), and
    # record_classification / update_extracted_fields already resync when they
    # write those. A status change alone, ANALYZED included, can't move it.
    return bool(result.data)

def approve_document(document_id: str) -> DocumentRecord | None:
    """Approving doesn't change checklist coverage (only rejection does), so no case resync."""
    result = (
        supabase_client.table("documents")
        .update({"status": DocumentStatus.APPROVED.value})
        .eq("id", document_id)
        .execute()
    )
    return DocumentRecord(**_row(result.data[0])) if result.data else None

def mark_detecting(document_id: str) -> None:
    """The Detection Agent has started — shown live as "Scanning" (see langgraph/detection/graph.py)."""
    supabase_client.table("documents").update(
        {"status": DocumentStatus.DETECTING.value}
    ).eq("id", document_id).execute()


def mark_detected(document_id: str) -> None:
    """The Detection Agent finished and produced a report — terminal."""
    supabase_client.table("documents").update(
        {"status": DocumentStatus.DETECTED.value}
    ).eq("id", document_id).execute()


def revert_to_approved(document_id: str) -> None:
    """A detection run crashed or was cancelled: back to APPROVED — still locked, eligible for Retry."""
    supabase_client.table("documents").update(
        {"status": DocumentStatus.APPROVED.value}
    ).eq("id", document_id).execute()


def reject_document(document_id: str) -> DocumentRecord | None:
    result = (
        supabase_client.table("documents")
        .update({"status": DocumentStatus.REJECTED.value})
        .eq("id", document_id)
        .execute()
    )
    # A rejected document no longer fulfils the checklist (see
    # case_service.compute_requirement_coverage), so the case's
    # submission_finalized flag can change here too.
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
    return _attach_extracted_fields(documents) if include_fields else documents


def move_documents(document_ids: list[str], case_id: str) -> None:
    """
    Bulk version of move_document — one UPDATE for every document, and no
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
    finding_reviews_repository.move_with_documents(document_ids, case_id)


def move_document(document_id: str, case_id: str) -> None:
    """
    Reassigns a document to a different case — e.g. corrects a document
    uploaded under the wrong case. Syncs both ends: the old case no longer
    counts it (see routers/documents.py's move_document, which also pushes a
    fresh snapshot for the old case over the websocket — Realtime's own
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
        finding_reviews_repository.move_with_documents([document_id], case_id)
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
    document status — this can happen well before the document reaches a
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
    column — kept separate from record_classification's confidence (the
    Classifier's confidence in document_type) so neither stage's number
    overwrites the other's; both remain readable after the fact. Also used
    for a loan officer's manual correction (see routers/documents.py's PATCH
    endpoint), which passes extraction_confidence=1.0 since a human has now
    verified the values.

    Does NOT write the field values themselves — the extracted_fields table
    (see extracted_fields_repository.replace_extracted_fields, called
    alongside this by every one of this function's callers) is the only
    place those are persisted; get_document/list_documents read them back
    from there, not from a documents column.

    fulfilled_requirements is optional (rather than always written here)
    since callers compute it from the extracted_fields they're passing in
    (see models.compute_fulfilled_requirements) — kept out of this
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
    # Only a loan officer's "Save fields" passes this — their review
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
    (exiftool — author/creation-date/software/etc.) alongside the raw-text
    extraction step, independent of classification/Extraction succeeding —
    it's captured as soon as the file is read, not gated on downstream
    stages.
    """
    supabase_client.table("documents").update({"metadata": metadata}).eq(
        "id", document_id
    ).execute()


# Tables whose rows belong to one document, besides extracted_fields (which has its own helper).
_DOCUMENT_CHILD_TABLES = ("flags", "langgraph_trace", notes_repository.TABLE, finding_reviews_repository.TABLE)


def _delete_document_children(document_ids: list[str]) -> None:
    """Removes what hangs off the documents: field rows, flags, notes, finding reviews and the detection trace. Runs before the
    documents delete, so a foreign key on any of them cannot block it, and works whether or not they cascade."""
    from .langgraph import trace

    extracted_fields_repository.delete_fields_for_documents(document_ids)
    client = service_supabase_client()
    for table in _DOCUMENT_CHILD_TABLES:
        client.table(table).delete().in_("document_id", document_ids).execute()
    trace.forget(document_ids)


def remove_document(document: DocumentRecord) -> None:
    """
    Permanently removes a document: its extracted-field rows, flags, notes
    and trace, the stored file, and the documents row. The dependent rows go
    first so a foreign key cannot block the documents delete.

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
    the per-document _sync_case call — callers doing this in bulk (case
    deletion) are removing the case itself right after, so resyncing its
    submission_finalized flag in between would be wasted work.
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


def _attach_extracted_fields(documents: list[DocumentRecord]) -> list[DocumentRecord]:
    """
    Populates extracted_fields from the extracted_fields table — the only
    place field values are persisted (see update_extracted_fields). Always
    overwrites, including with None when a document has no rows there, so a
    document's extracted_fields never reflects stale/legacy data that may
    still sit in the documents.extracted_fields column.
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
    nodes, the queue worker, detection's context loader) — cheap to opt out
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
        _attach_extracted_fields([document])
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
    return _attach_extracted_fields(documents) if include_fields else documents


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
    return _attach_extracted_fields(documents) if include_fields else documents


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
