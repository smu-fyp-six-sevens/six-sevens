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


class CaseRecord(BaseModel):
    id: str
    status: str = "open"
    submission_finalized: bool = False
    finalized_at: Optional[datetime] = None
    recommended_additional_documents: list = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class DocumentRecord(BaseModel):
    id: str
    case_id: Optional[str] = None
    filename: str
    document_type: Optional[str] = None
    storage_path: Optional[str] = None
    status: DocumentStatus
    retry_count: int = 0
    uploaded_at: datetime
    queued_at: Optional[datetime] = None
    processing_started_at: Optional[datetime] = None
    failure_reason: Optional[str] = None


class UploadResponse(BaseModel):
    document_id: str
    status: DocumentStatus


class BulkUploadItemResult(BaseModel):
    filename: str
    success: bool
    document_id: Optional[str] = None
    status: Optional[DocumentStatus] = None
    error: Optional[str] = None


class BulkUploadResponse(BaseModel):
    results: list[BulkUploadItemResult]


class DocumentDownloadResponse(BaseModel):
    document_id: str
    signed_url: str
    expires_in: int


class StuckDocument(BaseModel):
    id: str
    case_id: Optional[str] = None
    status: DocumentStatus
    seconds_in_current_status: float = Field(
        ..., description="How long the document has sat in its current status"
    )