from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from .models import DocumentRecord, DocumentStatus
from .supabase_client import supabase_client


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
    return DocumentRecord(**result.data[0])


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

    supabase_client.table("documents").update(update).eq("id", document_id).execute()


def record_classification(
    document_id: str,
    *,
    document_type: Optional[str],
    confidence: Optional[float],
    classification_outcome: str,
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
        "confidence": confidence,
        "classification_outcome": classification_outcome,
    }
    supabase_client.table("documents").update(update).eq("id", document_id).execute()


def update_extracted_fields(
    document_id: str, extracted_fields: dict, extraction_confidence: float
) -> None:
    """
    Persists the Extraction-stage output into its own extraction_confidence
    column — kept separate from record_classification's confidence (the
    Classifier's confidence in document_type) so neither stage's number
    overwrites the other's; both remain readable after the fact. Also used
    for a loan officer's manual correction (see routers/documents.py's PATCH
    endpoint), which passes extraction_confidence=1.0 since a human has now
    verified the values.
    """
    update = {
        "extracted_fields": extracted_fields,
        "extraction_confidence": extraction_confidence,
    }
    supabase_client.table("documents").update(update).eq("id", document_id).execute()


def record_escalation(document_id: str, reason: str) -> None:
    """
    Marks that Extraction judged the classified document_type to be wrong
    at least once during processing, triggering a re-run of Intake
    Coordinator (see orchestrator._process_document's escalation loop).
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


def get_document(document_id: str) -> Optional[DocumentRecord]:
    result = supabase_client.table("documents").select("*").eq("id", document_id).execute()
    if not result.data:
        return None
    return DocumentRecord(**result.data[0])


def get_documents_in_status(statuses: list[DocumentStatus]) -> list[DocumentRecord]:
    result = (
        supabase_client.table("documents")
        .select("*")
        .in_("status", [s.value for s in statuses])
        .execute()
    )
    return [DocumentRecord(**row) for row in result.data]


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
    return [DocumentRecord(**row) for row in result.data]
