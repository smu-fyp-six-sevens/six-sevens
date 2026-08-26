from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from supabase import create_client, Client

from .config import settings
from .models import DocumentRecord, DocumentStatus


class SupabaseClient:
    """
    Thin wrapper around the Supabase Python client. Keeps all document-row
    and storage operations in one place so routers/services never touch
    the raw client directly.
    """

    def __init__(self) -> None:
        self._client: Client | None = None

    @property
    def client(self) -> Client:
        if self._client is None:
            if not settings.SUPABASE_URL:
                raise RuntimeError("SUPABASE_URL is not set")
            if not settings.SUPABASE_PUBLISHABLE_KEY:
                raise RuntimeError("SUPABASE_PUBLISHABLE_KEY is not set")
            self._client = create_client(
                settings.SUPABASE_URL, settings.SUPABASE_PUBLISHABLE_KEY
            )
        return self._client

    # ---- Storage -----------------------------------------------------

    def upload_file(
        self, case_id: Optional[str], filename: str, file_bytes: bytes, content_type: str
    ) -> str:
        """Uploads the file and returns its storage path."""
        storage_folder = case_id or "unassigned"
        storage_path = f"{storage_folder}/{uuid.uuid4()}_{filename}"
        self.client.storage.from_(settings.SUPABASE_STORAGE_BUCKET).upload(
            storage_path, file_bytes, {"content-type": content_type}
        )
        return storage_path

    # ---- Document rows -------------------------------------------------

    def create_document(
        self, case_id: Optional[str], filename: str, storage_path: str
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
        result = self.client.table("documents").insert(row).execute()
        return DocumentRecord(**result.data[0])

    def update_status(
        self,
        document_id: str,
        status: DocumentStatus,
        *,
        queued_at: Optional[datetime] = None,
        processing_started_at: Optional[datetime] = None,
        retry_count: Optional[int] = None,
        failure_reason: Optional[str] = None,
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

        self.client.table("documents").update(update).eq("id", document_id).execute()

    def get_document(self, document_id: str) -> Optional[DocumentRecord]:
        result = self.client.table("documents").select("*").eq("id", document_id).execute()
        if not result.data:
            return None
        return DocumentRecord(**result.data[0])

    def get_documents_in_status(self, statuses: list[DocumentStatus]) -> list[DocumentRecord]:
        result = (
            self.client.table("documents")
            .select("*")
            .in_("status", [s.value for s in statuses])
            .execute()
        )
        return [DocumentRecord(**row) for row in result.data]


supabase_client = SupabaseClient()