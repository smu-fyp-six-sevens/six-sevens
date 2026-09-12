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


# The fixed checklist every case is required to satisfy — every real
# DocumentType is also a document requirement. A single document can fulfil
# more than one of these (e.g. an NRIC also covers Proof of Address), which
# is why fulfilment is tracked as its own list rather than assumed 1:1 with
# a document's own document_type — see DocumentRecord.fulfilled_requirements.
REQUIRED_DOCUMENT_TYPES: list[str] = [
    doc_type.value for doc_type in DocumentType if doc_type != DocumentType.UNRECOGNISED
]


def compute_fulfilled_requirements(
    document_type: str | None, extracted_fields: Optional[dict]
) -> list[str]:
    """
    Deterministic replacement for what the Classifier used to self-report
    via free-text ("Fulfils requirements" section, cross-checked against an
    "Address check: visible" evidence line) — decided here instead from the
    actually-extracted individual_address field, once Extraction has run.
    An NRIC (or any other document type whose schema happens to capture an
    address) fulfils proof_of_address for whoever individual_address names,
    on top of fulfilling its own document_type.

    Note: this only decides what a single document fulfils on its own —
    with multiple identities in a case (multiple ID documents), whether
    proof_of_address counts case-wide requires matching each identity to
    an address by name; see case_status.compute_identity_proof_of_address_coverage.
    """
    if document_type not in REQUIRED_DOCUMENT_TYPES:
        return []
    fulfilled = [document_type]
    has_address = bool((extracted_fields or {}).get("individual_address"))
    if has_address and document_type != DocumentType.PROOF_OF_ADDRESS.value:
        fulfilled.append(DocumentType.PROOF_OF_ADDRESS.value)
    return fulfilled


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
    """
    documents_status and fulfilled_requirements are both computed live from
    the documents table on every read, never stored — see case_status.py.
    recommended_additional_documents is repurposed here as the computed
    "still missing" list (the checklist requirements no accepted document
    has fulfilled yet), overriding whatever's in the underlying (always
    empty — nothing ever wrote to it) cases.recommended_additional_documents
    column.
    """

    documents_status: CaseDocumentsStatus
    fulfilled_requirements: list[str] = Field(default_factory=list)


class DocumentRecord(BaseModel):
    id: str
    case_id: Optional[str] = None
    filename: str
    document_type: Optional[str] = None
    # The Classifier's confidence in document_type — set once at classification
    # and never overwritten by Extraction. See extraction_confidence below for
    # the separate, later stage's own confidence in the field values it found.
    confidence: Optional[float] = None
    classification_outcome: Optional[ClassificationOutcome] = None
    storage_path: Optional[str] = None
    status: DocumentStatus
    retry_count: int = 0
    uploaded_at: datetime
    queued_at: Optional[datetime] = None
    processing_started_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    evidence: list[str] = Field(default_factory=list)
    fulfilled_requirements: list[str] = Field(default_factory=list)
    extracted_fields: Optional[dict] = None
    # Extraction's own confidence in the values it pulled into extracted_fields
    # — distinct from (and no longer overwrites) the classifier's `confidence`
    # above. Set to 1.0 when a loan officer manually saves corrected fields.
    extraction_confidence: Optional[float] = None
    escalation_reason: Optional[str] = None
    metadata: Optional[dict] = None


class UpdateExtractedFieldsRequest(BaseModel):
    """
    A loan officer's manual correction of low-confidence extracted fields.
    Replaces the full extracted_fields dict (not a per-field patch) since
    the frontend always edits the complete set it was shown.
    """

    extracted_fields: dict
    evidence: list[str] = Field(default_factory=list)


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


class ExtractionTestPage(BaseModel):
    """
    One page's worth of extraction output. `elements` shape depends on
    extraction_method: native-PDF elements carry font/size/flags, OCR
    elements carry a per-line confidence score — kept as free-form dicts
    here rather than a union model since this endpoint is a debugging
    window onto whatever ingest_document actually returned.
    """

    page_number: int
    text: str
    elements: list[dict] = Field(default_factory=list)


class ExtractionTestResponse(BaseModel):
    """Mirrors the dict returned by ingestion.ingestion_service.ingest_document."""

    file_name: str
    file_type: str
    metadata: dict = Field(default_factory=dict)
    extraction_method: str
    raw_text: str
    pages: list[ExtractionTestPage]
    needs_review: bool
    status: str


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