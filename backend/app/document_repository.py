from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, cast

from . import case_service, extracted_fields_repository, storage_service
from .models import DocumentRecord, DocumentStatus
from .supabase_client import service_supabase_client, supabase_client

logger = logging.getLogger(__name__)


def _row(mapping: Any) -> dict:
    """postgrest types a row as a loose JSON union rather than dict[str, Any]
    — every row here is one we (or our own migrations) wrote, so this cast
    just tells the type checker what's already true."""
    return cast(dict, mapping)


def _sync_case(case_id: Optional[str]) -> None:
    """
    Best-effort refresh of the case-level submission_finalized cache after a
    write that can change a document's classification_outcome or
    fulfilled_requirements. Never raises: the document write above already
    succeeded and is the source of truth, and get_case_summary/build_summary
    always recompute requirement coverage live from the documents themselves
    rather than trusting this cached flag — so a failed resync here just
    leaves it stale until the next successful write to this case's documents
    self-heals it, not something worth failing the caller's request over.
    """
    if case_id is None:
        return
    try:
        case_service.sync_submission_finalized(case_id)
    except Exception:
        logger.exception("Document write succeeded but failed to resync case %s", case_id)


def create_document(
    case_id: Optional[str], filename: str, storage_path: str
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
    queued_at: Optional[datetime] = None,
    processing_started_at: Optional[datetime] = None,
    retry_count: Optional[int] = None,
    failure_reason: Optional[str] = None,
    document_type: Optional[str] = None,
) -> None:
    update: dict = {
        "status": status.value,
    }
    if queued_at is not None:
        update["queued_at"] = queued_at.isoformat()
    if processing_started_at is not None:
        update["processing_started_at"] = processing_started_at.isoformat()
    if retry_count is not None:
        update["retry_count"] = retry_count
    if failure_reason is not None:
        update["failure_reason"] = failure_reason
    if document_type is not None:
        update["document_type"] = document_type

    result = supabase_client.table("documents").update(update).eq("id", document_id).execute()
    # Only the ANALYZED transition can change requirement coverage (a
    # document's classification_outcome/fulfilled_requirements are set
    # earlier, by record_classification/update_extracted_fields) — every
    # other status this function sets (queued/processing/extracting/failed)
    # never did resync before either, so this deliberately doesn't sync on
    # those to avoid extra writes with no coverage-affecting reason.
    if status == DocumentStatus.ANALYZED and result.data:
        _sync_case(_row(result.data[0]).get("case_id"))

def approve_document(document_id: str) -> None:
    result = (
        supabase_client.table("documents")
        .update({"status": DocumentStatus.APPROVED.value})
        .eq("id", document_id)
        .execute()
    )
    if result.data:
        _sync_case(_row(result.data[0]).get("case_id"))

def reject_document(document_id: str) -> None:
    supabase_client.table("documents").update(
        {"status": DocumentStatus.REJECTED.value}
    ).eq("id", document_id).execute()
    
def record_classification(
    document_id: str,
    *,
    document_type: Optional[str],
    confidence: Optional[float],
    classification_outcome: str,
    evidence: Optional[list[str]] = None,
    fulfilled_requirements: Optional[list[str]] = None,
    reasoning: Optional[str] = None,
) -> None:
    """
    Persists the classification-stage decision (type, confidence, and
    whether it was accepted or sent to manual review) independently of
    document status — this can happen well before the document reaches a
    terminal ANALYZED/FAILED status, since Extraction is a separate stage
    that runs afterward and can fail on its own.

    `reasoning` is the classifier's own plain-language justification for the
    decision (the Dify chatflow's Agent 3 always produced one; it was parsed
    into the classification dict but never actually persisted anywhere —
    kept here now so it isn't silently dropped). Optional and update-only
    (never clears a prior reasoning with "") since not every caller has one.
    """
    update = {
        "document_type": document_type,
        "confidence": confidence,
        "classification_outcome": classification_outcome,
        "evidence": evidence or [],
        "fulfilled_requirements": fulfilled_requirements or [],
    }
    if reasoning is not None:
        update["classification_reasoning"] = reasoning
    result = supabase_client.table("documents").update(update).eq("id", document_id).execute()
    if result.data:
        _sync_case(_row(result.data[0]).get("case_id"))


def update_extracted_fields(
    document_id: str,
    extracted_fields: dict,
    extraction_confidence: Optional[float],
    fulfilled_requirements: Optional[list[str]] = None,
) -> None:
    """
    Persists the Extraction-stage output into its own extraction_confidence
    column — kept separate from record_classification's confidence (the
    Classifier's confidence in document_type) so neither stage's number
    overwrites the other's; both remain readable after the fact. Also used
    for a loan officer's manual correction (see routers/documents.py's PATCH
    endpoint), which passes extraction_confidence=1.0 since a human has now
    verified the values.

    fulfilled_requirements is optional (rather than always written here)
    since callers compute it from the extracted_fields they're passing in
    (see models.compute_fulfilled_requirements) — kept out of this
    function's own responsibility so it stays a pure "persist what I was
    given" write.
    """
    update: dict = {
        "extracted_fields": extracted_fields,
        "extraction_confidence": extraction_confidence,
    }
    if fulfilled_requirements is not None:
        update["fulfilled_requirements"] = fulfilled_requirements
    result = supabase_client.table("documents").update(update).eq("id", document_id).execute()
    if result.data:
        _sync_case(_row(result.data[0]).get("case_id"))


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


def remove_document(document: DocumentRecord) -> None:
    """
    Permanently removes a document: its extracted-field rows, the stored
    file, and the documents row. Field rows go first so a foreign key on
    extracted_fields.document_id cannot block the documents delete.

    Storage deletion is best-effort. A missing object should not leave the
    row in place; a later retry still deletes the row.
    """
    extracted_fields_repository.delete_fields_for_document(document.id)
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
    extracted_fields_repository.delete_fields_for_documents(document_ids)
    storage_paths = [document.storage_path for document in documents if document.storage_path]
    if storage_paths:
        try:
            storage_service.delete_files(storage_paths)
        except Exception:
            logger.exception("Failed to delete stored files for documents %s", document_ids)
    service_supabase_client().table("documents").delete().in_("id", document_ids).execute()


def get_document(document_id: str) -> Optional[DocumentRecord]:
    result = supabase_client.table("documents").select("*").eq("id", document_id).execute()
    if not result.data:
        return None
    return DocumentRecord(**_row(result.data[0]))


def get_documents_in_status(statuses: list[DocumentStatus]) -> list[DocumentRecord]:
    result = (
        supabase_client.table("documents")
        .select("*")
        .in_("status", [s.value for s in statuses])
        .execute()
    )
    return [DocumentRecord(**_row(row)) for row in result.data]


def list_documents(
    case_id: Optional[str] = None,
    status: Optional[DocumentStatus] = None,
) -> list[DocumentRecord]:
    query = supabase_client.table("documents").select("*")
    if case_id is not None:
        query = query.eq("case_id", case_id)
    if status is not None:
        query = query.eq("status", status.value)
    result = query.execute()
    return [DocumentRecord(**_row(row)) for row in result.data]
