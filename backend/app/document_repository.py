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
