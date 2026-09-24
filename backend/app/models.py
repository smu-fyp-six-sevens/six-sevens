from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class DocumentStatus(str, Enum):
    UPLOADED = "uploaded"       # stored, not yet queued
    QUEUED = "queued"           # AC1: sitting in the background task queue
    PROCESSING = "processing"   # AC2: Classifier workflow invoked (shown as "Classifying")
    EXTRACTING = "extracting"   # classification recorded; Extraction workflow invoked
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
    # Split from a single former ID_DOCUMENT type — passport and NRIC have
    # different extraction schemas (a passport carries no address field;
    # see extraction_prompts.EXTRACTION_SCHEMAS) so classification needs to
    # tell them apart. They still satisfy the same case-checklist slot
    # though — see IDENTITY_DOCUMENT_TYPES/IDENTITY_DOCUMENT_REQUIREMENT
    # below — a case needs one identity document per individual, whichever
    # type they actually have, not both.
    PASSPORT = "passport"
    NRIC = "nric"
    FATCA_CRS_DECLARATION = "fatca_crs_declaration"
    CERTIFICATE_OF_INCUMBENCY = "certificate_of_incumbency"
    COMPANY_CONSTITUTION = "company_constitution"
    PROOF_OF_ADDRESS = "proof_of_address"
    SOURCE_OF_WEALTH = "source_of_wealth"
    CREDIT_REPORT = "credit_report"
    LIST_OF_LOANS = "list_of_loans"
    NOTICE_OF_ASSESSMENT = "notice_of_assessment"
    # Not a real category — an explicit, queryable outcome for "this document
    # doesn't match any of the categories above", distinct from a low-
    # confidence guess among them. Always paired with needs_manual_review.
    UNRECOGNISED = "unrecognised"


# Passport and NRIC are real, independently-classifiable document types
# (different extraction schemas each), but both fulfil the same checklist
# requirement below — never appears as a document's own document_type,
# only in REQUIRED_DOCUMENT_TYPES/fulfilled_requirements.
IDENTITY_DOCUMENT_REQUIREMENT = "id_document"
IDENTITY_DOCUMENT_TYPES: set[str] = {DocumentType.PASSPORT.value, DocumentType.NRIC.value}

# The only document types whose individual_address counts as personal proof
# of address: the NRIC (the primary one), supplemented by a personal utility
# bill (proof_of_address) or personal bank statement. Any other document that
# happens to print an address (a company's incumbency/constitution listing
# officers or subscribers, ...) is ignored for proof_of_address, both here
# and in mapper.py's name matching.
ADDRESS_PROOF_DOCUMENT_TYPES: set[str] = {
    DocumentType.NRIC.value,
    DocumentType.PROOF_OF_ADDRESS.value,
    DocumentType.BANK_STATEMENT.value,
}


def is_personal_bank_statement(document_type: str | None, fields: Optional[dict]) -> bool:
    """
    A bank_statement held by an individual rather than a company. The signal
    is who the extracted account holder is: an individual_name with no
    company_name (see extraction_prompts.EXTRACTION_SCHEMAS's
    "bank_statement" entry and its guidance). Before extraction has run —
    no fields yet — it stays the corporate default, matching how a
    proof_of_address/source_of_wealth document is treated until its fields
    are known.
    """
    fields = fields or {}
    return (
        document_type == DocumentType.BANK_STATEMENT.value
        and bool(fields.get("individual_name"))
        and not fields.get("company_name")
    )


def counts_as_address_proof(document_type: str | None, fields: Optional[dict]) -> bool:
    """
    Whether an individual_address on this document counts as personal proof
    of address — see ADDRESS_PROOF_DOCUMENT_TYPES. A bank statement only
    counts when it's a personal one: a corporate statement's address is the
    company's, not any person's residence.
    """
    if document_type == DocumentType.BANK_STATEMENT.value:
        return is_personal_bank_statement(document_type, fields)
    return document_type in ADDRESS_PROOF_DOCUMENT_TYPES


# The fixed checklist every case is required to satisfy. Every DocumentType
# is its own requirement EXCEPT passport/nric, which collapse into the
# single shared IDENTITY_DOCUMENT_REQUIREMENT above — so this isn't quite
# "every real DocumentType" any more, it's every DocumentType that isn't
# itself part of an either/or group, plus one slot per such group. A single
# document can fulfil more than one requirement (e.g. an NRIC also covers
# Proof of Address), which is why fulfilment is tracked as its own list
# rather than assumed 1:1 with a document's own document_type — see
# DocumentRecord.fulfilled_requirements.
#
# ADDITIONAL_DOCUMENT_TYPES are the checklist's "Additional" tier: documents
# that may be required depending on the case. They show up on the checklist
# once uploaded but never count toward completion — they're left out of
# REQUIRED_DOCUMENT_TYPES so a case can be finalised without them.
#
# company_proof_of_address is, like id_document above, a requirement and not
# a document_type: it's a proof_of_address document whose extracted address
# is the company's rather than an individual's (see
# compute_fulfilled_requirements). Personal proof of address stays the
# required "proof_of_address" item.
COMPANY_PROOF_OF_ADDRESS_REQUIREMENT = "company_proof_of_address"
# Same idea for source of wealth: personal source of wealth (e.g. a brokerage
# statement in an individual's own name) is a source_of_wealth document whose
# subject is a person rather than a company. Corporate source of wealth stays
# the required "source_of_wealth" item.
PERSONAL_SOURCE_OF_WEALTH_REQUIREMENT = "source_of_personal_wealth"
ADDITIONAL_DOCUMENT_TYPES: list[str] = [
    DocumentType.LIST_OF_LOANS.value,
    COMPANY_PROOF_OF_ADDRESS_REQUIREMENT,
    PERSONAL_SOURCE_OF_WEALTH_REQUIREMENT,
]

REQUIRED_DOCUMENT_TYPES: list[str] = [
    doc_type.value
    for doc_type in DocumentType
    if doc_type != DocumentType.UNRECOGNISED
    and doc_type.value not in IDENTITY_DOCUMENT_TYPES
    and doc_type.value not in ADDITIONAL_DOCUMENT_TYPES
] + [IDENTITY_DOCUMENT_REQUIREMENT]


def compute_fulfilled_requirements(
    document_type: str | None, extracted_fields: Optional[dict]
) -> list[str]:
    """
    Deterministic replacement for what the Classifier used to self-report
    via free-text ("Fulfils requirements" section, cross-checked against an
    "Address check: visible" evidence line) — decided here instead from the
    actually-extracted individual_address field, once Extraction has run.
    An NRIC, utility bill or PERSONAL bank statement showing an
    individual_address fulfils proof_of_address for whoever it names. No
    other document type does (see ADDRESS_PROOF_DOCUMENT_TYPES) — an address
    on an incumbency or constitution, for example, is not proof of address.
    A personal bank statement fulfils only proof_of_address, never the
    corporate "bank_statement" requirement; a corporate one fulfils only
    "bank_statement".

    Note: this only decides what a single document fulfils on its own —
    with multiple identities in a case (multiple ID documents), whether
    proof_of_address counts case-wide requires matching each identity to
    an address by name; see mapper.compute_identity_proof_of_address_coverage.
    """
    if document_type in IDENTITY_DOCUMENT_TYPES:
        fulfilled = [IDENTITY_DOCUMENT_REQUIREMENT]
    elif document_type in REQUIRED_DOCUMENT_TYPES or document_type in ADDITIONAL_DOCUMENT_TYPES:
        fulfilled = [document_type]
    else:
        return []
    fields = extracted_fields or {}
    has_address = bool(fields.get("individual_address"))
    if (
        has_address
        and document_type != DocumentType.PROOF_OF_ADDRESS.value
        and counts_as_address_proof(document_type, fields)
    ):
        fulfilled.append(DocumentType.PROOF_OF_ADDRESS.value)
    # A personal bank statement is proof of address only — it isn't the
    # company's bank statement the corporate checklist item asks for.
    if is_personal_bank_statement(document_type, fields):
        fulfilled.remove(DocumentType.BANK_STATEMENT.value)
    # A proof_of_address document showing the company's address fulfils the
    # additional company_proof_of_address item. With no individual address on
    # it too, it says nothing about any person's residence, so it doesn't
    # fulfil personal proof_of_address.
    if document_type == DocumentType.PROOF_OF_ADDRESS.value and fields.get("company_address"):
        fulfilled.append(COMPANY_PROOF_OF_ADDRESS_REQUIREMENT)
        if not has_address:
            fulfilled.remove(DocumentType.PROOF_OF_ADDRESS.value)
    # A source_of_wealth document in a person's name with no UEN on it is
    # personal source of wealth. The UEN (not company_name) is the corporate
    # signal: a personal brokerage statement still prints the broker's own
    # "Pte. Ltd." name, which must not make it read as a company document.
    if (
        document_type == DocumentType.SOURCE_OF_WEALTH.value
        and (fields.get("account_holder_name") or fields.get("individual_name"))
        and not fields.get("uen")
    ):
        fulfilled.remove(DocumentType.SOURCE_OF_WEALTH.value)
        fulfilled.append(PERSONAL_SOURCE_OF_WEALTH_REQUIREMENT)
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
    # Set automatically at creation from settings.IS_CLOUD_DEPLOYMENT (never
    # by the caller) — True for anything created outside the cloud
    # deployment, hidden from the cloud case queue during UAT. See
    # case_repository.create_case / list_cases.
    dev: bool = False


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


class ProofOfAddressCoverage(BaseModel):
    """
    How many of the case's identified individuals (one per accepted
    id_document) currently have an address on file, out of how many there
    are — see mapper.compute_identity_proof_of_address_progress.
    total=0 means no ID document has been accepted yet, so there's nothing
    to report coverage against (distinct from 0 covered out of >0 total).
    """

    covered: int
    total: int
    # Names carrying an address (proof_of_address, bank_statement, ...) that
    # don't match any identified id_document in the case — e.g. an address
    # uploaded for someone whose ID hasn't been uploaded yet. Not counted in
    # covered/total (which is id_document-driven), so surfaced separately —
    # see mapper.compute_unmatched_proof_of_address_names.
    unmatched_names: list[str] = Field(default_factory=list)


class IdDocumentCoverage(BaseModel):
    """
    How many of the case's identified individuals (grouped the same way as
    proof_of_address_coverage — by id_number, falling back to name) have a
    complete id_document (front+back matched by id_number, or a
    self-contained single capture) on file, out of how many identities have
    been identified at all. total=0 means no id_document has been accepted
    yet — see mapper.compute_id_document_identity_progress.
    """

    covered: int
    total: int
    # "front" or "back" — whichever side is still missing — only populated
    # when there's exactly one identity in the case; with more than one, a
    # single hint can't describe each person's own gap. See
    # mapper.compute_id_document_missing_side.
    missing_side: Optional[str] = None


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
    proof_of_address_coverage: ProofOfAddressCoverage = Field(
        default_factory=lambda: ProofOfAddressCoverage(covered=0, total=0)
    )
    id_document_coverage: IdDocumentCoverage = Field(
        default_factory=lambda: IdDocumentCoverage(covered=0, total=0)
    )


class UpdateCaseRequest(BaseModel):
    """
    A loan officer updating the case's own identifying details. Only
    company_name is officer-editable today — status, submission_finalized
    and greenlink_case_id are system-managed (see case_status.py /
    case_repository.set_submission_finalized) and deliberately excluded
    here so they can't be overwritten through this endpoint.
    """

    company_name: str

    @field_validator("company_name")
    @classmethod
    def company_name_must_not_be_blank(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("company_name is required")
        return trimmed


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
    # The Digital Authenticity Specialist's stored result (see
    # fraud_detection.metadata_analysis.MetadataAnalysisResult) — set once
    # GET .../detection/metadata-anomaly has run for this document.
    metadata_anomalies: Optional[dict] = None


class UpdateExtractedFieldsRequest(BaseModel):
    """
    A loan officer's manual correction of low-confidence extracted fields.
    Replaces the full extracted_fields dict (not a per-field patch) since
    the frontend always edits the complete set it was shown.
    """

    extracted_fields: dict
    evidence: list[str] = Field(default_factory=list)

class AddExtractedFieldRequest(BaseModel):
    field_name: str
    field_value: object

class UpdateDocumentTypeRequest(BaseModel):
    """
    A loan officer's manual correction of a low-confidence document_type.
    "unrecognised" is the "Other" catch-all — see extraction_prompts.
    """

    document_type: str


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