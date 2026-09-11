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


class ClassificationOutcome(str, Enum):
    """
    Routing decision made at the classification stage, independent of
    whether the later Extraction stage subsequently succeeds or fails.
    ACCEPTED means classification met the confidence threshold and was
    routed to Analysis — it does not mean the document ultimately reached
    ANALYZED status.
    """

    ACCEPTED = "accepted"
    MANUAL_REVIEW = "manual_review"


class DocumentType(str, Enum):
    """The document categories the Classifier Agent recognises."""

    BANK_STATEMENT = "bank_statement"
    CERTIFICATE_OF_INCORPORATION = "certificate_of_incorporation"
    PAYSLIP = "payslip"
    ID_DOCUMENT = "id_document"  # NRIC / passport of directors, shareholders, UBO
    FATCA_CRS_DECLARATION = "fatca_crs_declaration"
    CERTIFICATE_OF_INCUMBENCY = "certificate_of_incumbency"
    COMPANY_CONSTITUTION = "company_constitution"
    PROOF_OF_ADDRESS = "proof_of_address"
    SOURCE_OF_WEALTH = "source_of_wealth"
    CREDIT_REPORT = "credit_report"
    LIST_OF_LOANS = "list_of_loans"
    # Not a real category — an explicit, queryable outcome for "this document
    # doesn't match any of the categories above", distinct from a low-
    # confidence guess among them. Always paired with needs_manual_review.
    UNRECOGNISED = "unrecognised"


class CaseRecord(BaseModel):
    id: str
    greenlink_case_id: str
    company_name: Optional[str] = None
    status: str = "open"
    submission_finalized: bool = False
    finalized_at: Optional[datetime] = None
    recommended_additional_documents: list = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CaseDocumentsStatus(str, Enum):
    """
    Rollup of every document under a case, worst-first: a single stuck
    document outranks nine analyzed ones. Computed live from the documents
    table on every read — never stored — since "stuck" is itself a
    time-elapsed condition with no event that would keep a stored column
    honest (see case_status.py).
    """

    NO_DOCUMENTS = "no_documents"
    STUCK = "stuck"
    FAILED = "failed"
    IN_PROGRESS = "in_progress"
    ANALYZED = "analyzed"


class CaseSummary(CaseRecord):
    documents_status: CaseDocumentsStatus


class DocumentRecord(BaseModel):
    id: str
    case_id: Optional[str] = None
    filename: str
    document_type: Optional[str] = None
    confidence: Optional[float] = None
    classification_outcome: Optional[ClassificationOutcome] = None
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


class ClassificationPreviewResponse(BaseModel):
    """
    Evidence held in-memory only (see classification_cache.py) — confidence
    now lives on DocumentRecord.confidence since it's a real, persisted
    column; this endpoint only covers the part that still isn't persisted.
    """

    evidence: list[str] = Field(default_factory=list)


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


class ProcessingErrorEntry(BaseModel):
    document_id: str
    error: str
    occurred_at: datetime


class MonitoringIssues(BaseModel):
    stuck: list[StuckDocument]
    failed: list[DocumentRecord]
    recent_errors: list[ProcessingErrorEntry]