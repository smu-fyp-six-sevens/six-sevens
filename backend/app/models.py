from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DocumentStatus(str, Enum):
    UPLOADED = "uploaded"       # stored, not yet queued
    QUEUED = "queued"           # AC1: sitting in the background task queue
    PROCESSING = "processing"   # AC2: Classifier workflow invoked
    ANALYZED = "analyzed"       # Classifier (and downstream agents) finished
    FAILED = "failed"           # AC3: retries exhausted, needs manual review


class DocumentRecord(BaseModel):
    id: str
    case_id: Optional[str] = None
    filename: str
    document_type: Optional[str] = None
    storage_path: str
    status: DocumentStatus
    retry_count: int = 0
    uploaded_at: datetime
    queued_at: Optional[datetime] = None
    processing_started_at: Optional[datetime] = None
    failure_reason: Optional[str] = None


class UploadResponse(BaseModel):
    document_id: str
    status: DocumentStatus


class StuckDocument(BaseModel):
    id: str
    case_id: Optional[str] = None
    status: DocumentStatus
    seconds_in_current_status: float = Field(
        ..., description="How long the document has sat in its current status"
    )