from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from .config import settings
from .holder import holder_kind


class DocumentStatus(str, Enum):
    UPLOADED = "uploaded"       # stored, not yet queued
    QUEUED = "queued"           # AC1: sitting in the background task queue
    PROCESSING = "processing"   # AC2: Classifier workflow invoked (shown as "Classifying")
    EXTRACTING = "extracting"   # classification recorded; Extraction workflow invoked
    ANALYZED = "analyzed"       # Classifier (and downstream agents) finished
    APPROVED = "approved"       # When loan officer manually approves a document
    DETECTING = "detecting"     # Detection Agent running (shown as "Scanning")
    DETECTED = "detected"       # Detection Agent finished with a report — terminal
    REJECTED = "rejected"       # When loan officer manually rejects a document
    FAILED = "failed"           # AC3: retries exhausted, needs manual review
    CANCELLED = "cancelled"     # Manually stopped by a loan officer while queued/processing


# The pipeline is still actively running on a document in any of these
# statuses — a mutation that assumes a settled document (manual type
# override, monitoring's "stuck" check, running detection) would race it.
# Single source of truth: was independently redefined 3 times (monitoring.py,
# routers/documents.py, routers/detection.py) before being centralised here.
IN_FLIGHT_STATUSES = frozenset({
    DocumentStatus.UPLOADED,
    DocumentStatus.QUEUED,
    DocumentStatus.PROCESSING,
    DocumentStatus.EXTRACTING,
})

# An officer has approved the document: from approval through detection (locked against edits).
APPROVED_STATUSES = frozenset({DocumentStatus.APPROVED, DocumentStatus.DETECTING, DocumentStatus.DETECTED})


class RoutingStatus(str, Enum):
    """
    Routing decision made at the classification stage, independent of
    whether the later Extraction stage subsequently succeeds or fails.
    ACCEPTED means classification met the confidence threshold and was
    routed to Analysis — it does not mean the document ultimately reached
    ANALYZED status. Backed by the documents.routing_status column.
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


def is_personal_bank_statement(document_type: str | None, fields: dict | None) -> bool:
    """
    A bank_statement held by an individual rather than a company. The signal
    is the stored entity_type, or for statements stored before it existed an
    individual_name with no company_name (see holder.holder_kind). Before extraction has run —
    no fields yet — it stays the corporate default, matching how a
    proof_of_address/source_of_wealth document is treated until its fields
    are known.
    """
    return document_type == DocumentType.BANK_STATEMENT.value and holder_kind(document_type, fields) == "individual"


def counts_as_address_proof(document_type: str | None, fields: dict | None) -> bool:
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
    document_type: str | None, extracted_fields: dict | None
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
    company_name: str | None = None
    status: str = "open"
    submission_finalized: bool = False
    finalized_at: datetime | None = None
    # The list of documents to request, read from the recommended_additional_documents column (see the validator); only the case summary returns it.
    additional_documents: AdditionalDocuments | None = Field(default=None, exclude=True)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _split_stored_additional_documents(cls, data):
        """
        The recommended_additional_documents column holds [] until a case is analysed, then the analysis's
        whole additional-documents result (an object). It is not a field: callers see that object as additional_documents.
        """
        raw = data.get("recommended_additional_documents") if isinstance(data, dict) else None
        if isinstance(raw, dict):
            data = {**data, "additional_documents": raw or None}
        return data


class CaseDocumentsStatus(str, Enum):
    """
    Rollup of every document under a case, worst-first: a single stuck
    document outranks nine analyzed ones. Computed live from the documents
    table on every read — never stored — since "stuck" is itself a
    time-elapsed condition with no event that would keep a stored column
    honest (see case_service.py).
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
    missing_side: str | None = None


RiskState = Literal["scored", "pending", "failed", "no_documents"]
RiskLevel = Literal["Low", "Medium", "High", "Critical"]


class ContributingFinding(BaseModel):
    document_id: str
    filename: str
    document_type: str | None = None
    finding_id: str
    tool: str | None = None
    severity: str
    summary: str
    rationale: str
    # Points this finding added after the document's noise cap.
    points: float
    # The specialist kept the tool's finding without ever assessing it.
    unreviewed: bool = False
    # The finding's row in the flags table, set only when a specialist approved it as genuine.
    flag_id: str | None = None
    # See case_pipeline.models.FindingCategory: decides whether more evidence can reduce it.
    category: str | None = None
    # "cross_document" for a finding from the case analysis rather than one document's scan.
    source: Literal["document", "cross_document"] = "document"
    # The same finding on other documents is scored once. On the copy that scores it: how many other documents
    # carry it. On the copies that score 0: the finding_id of the one that scored.
    also_on: int = 0
    repeat_of: str | None = None


class UnassessedCheck(BaseModel):
    document_id: str
    filename: str
    tool: str | None = None
    reason: str | None = None


class CaseRisk(BaseModel):
    """
    The case's risk level, rolled up from its documents' detection
    findings — see case_risk.compute_case_risk for how it's scored. Only
    "scored" carries a level; the other states say why there isn't one yet.
    """

    state: RiskState
    level: RiskLevel | None = None
    # 0-100, anchored to level (see case_risk.SCORE_BANDS) — what officers see.
    score: int | None = None
    # The raw points total the level is banded from.
    points: float | None = None
    contributing_findings: list[ContributingFinding] = Field(default_factory=list)
    # Checks that couldn't run — listed, never scored: not being able to
    # check something isn't evidence of fraud.
    unassessed_checks: list[UnassessedCheck] = Field(default_factory=list)
    # Why the case got its level, e.g. which Critical rule fired.
    reasons: list[str] = Field(default_factory=list)
    documents_scanned: int = 0
    documents_total: int = 0


class RecommendationItem(BaseModel):
    """
    One thing a supporting document is requested for: a bank statement
    transaction, or a line of a financial statement.
    """

    date: str | None = None
    # The transaction description or line label, as printed.
    description: str
    # Signed as extracted: negative for a payment out, positive for a receipt.
    amount: float
    # A financial statement line's prior-year figure, where it has one.
    prior_amount: float | None = None
    # The supplier paid or customer paying, when the statement names one.
    counterparty: str | None = None
    reason: str
    # The lines this one totals, e.g. a total's portions — shown as a table
    # of these lines plus this one as its total row.
    breakdown: list["RecommendationItem"] = Field(default_factory=list)


class SupportingDocumentRecommendation(BaseModel):
    """
    A document the officer should request from the client, and why — see
    supporting_documents.recommend_supporting_documents.
    """

    document_type: Literal[
        "purchase_order", "sales_invoice", "related_party_repayment_evidence", "bank_loan_evidence"
    ]
    # What to ask for, e.g. "Purchase Order".
    title: str
    # Why: what the source document shows.
    reason: str
    # The ask, introducing items, e.g. "Request purchase orders for these transactions:".
    request: str
    source_document_id: str
    source_filename: str
    items: list[RecommendationItem] = Field(default_factory=list)


class AdditionalDocument(BaseModel):
    """One document to ask the client for, from the checklist rules or from the findings."""

    title: str
    why: str
    source: Literal["checklist", "finding"]
    # Finding ids the request bears on (after matching; may be empty).
    addresses: list[str] = Field(default_factory=list)
    # False when more evidence cannot change the score (tampering, registry results, checklist items).
    affects_score: bool = False
    score_if_supports: int | None = None
    level_if_supports: str | None = None
    # The checklist rule's detail (items, breakdown tables), for checklist items.
    recommendation: SupportingDocumentRecommendation | None = None


class AdditionalDocuments(BaseModel):
    """What to ask the client for, in one list, with why the list is empty or cannot move the score."""

    items: list[AdditionalDocument] = Field(default_factory=list)
    # no_reducible_findings: no finding can be answered by more evidence. model_failed / budget: the
    # model's requests are missing. none_needed: the case has nothing that evidence would address.
    reason: Literal["none_needed", "no_reducible_findings", "model_failed", "budget"] | None = None
    current_score: int | None = None
    current_level: str | None = None
    best_case_score: int | None = None
    best_case_level: str | None = None


CaseRecord.model_rebuild()  # now that AdditionalDocuments exists


class CaseAnalysisOverview(BaseModel):
    """The latest per-case analysis in brief; the full result is GET /cases/{id}/analysis."""

    run_id: str
    status: Literal["running", "complete", "failed", "superseded"]
    # The case's documents changed since this run read them.
    stale: bool = False
    finished_at: datetime | None = None
    finding_count: int = 0


class CaseSummary(CaseRecord):
    """
    documents_status, fulfilled_requirements and missing_requirements are all
    computed live from the documents table on every read, never stored — see
    case_service.py.
    """

    documents_status: CaseDocumentsStatus
    fulfilled_requirements: list[str] = Field(default_factory=list)
    # The checklist requirements no accepted document has fulfilled yet.
    missing_requirements: list[str] = Field(default_factory=list)
    proof_of_address_coverage: ProofOfAddressCoverage = Field(
        default_factory=lambda: ProofOfAddressCoverage(covered=0, total=0)
    )
    id_document_coverage: IdDocumentCoverage = Field(
        default_factory=lambda: IdDocumentCoverage(covered=0, total=0)
    )
    # None when the documents' detection reports couldn't be loaded.
    risk: CaseRisk | None = None
    # Further evidence to request from the client; empty when nothing in the
    # case needs any. Unlike missing_requirements (missing checklist items),
    # these come from what the documents themselves show.
    supporting_document_recommendations: list[SupportingDocumentRecommendation] = Field(
        default_factory=list
    )
    # The checklist requests merged with the agent's, in one list; None before the case is analysed.
    additional_documents: AdditionalDocuments | None = None
    # Every approved document has been scanned, so the case analysis can be run.
    analysis_ready: bool = False
    # None when the case has never been analysed.
    analysis: CaseAnalysisOverview | None = None


class CaseListItem(CaseRecord):
    """One row of the case queue: only what the list shows, so it needs no extracted fields."""

    documents_status: CaseDocumentsStatus
    # None when the documents' detection reports couldn't be loaded.
    risk: CaseRisk | None = None


# The same limit as the case forms in the frontend (utils/caseValidation.ts).
MAX_COMPANY_NAME_LENGTH = 100


class UpdateCaseRequest(BaseModel):
    """
    A loan officer updating the case's own identifying details. Only
    company_name is officer-editable today — status, submission_finalized
    and greenlink_case_id are system-managed (see case_service.py /
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
        if len(trimmed) > MAX_COMPANY_NAME_LENGTH:
            raise ValueError(f"company_name must be at most {MAX_COMPANY_NAME_LENGTH} characters")
        return trimmed


MAX_NOTE_LENGTH = 4000


class CaseNote(BaseModel):
    """
    A loan officer's note on a case, or on one of its documents (document_id). parent_id marks a
    reply to an earlier note. Only its author can edit or delete it; edited_at shows it was edited.
    A deleted note that has replies stays as a placeholder (deleted_at set, its text erased) so the
    replies keep their place.
    """

    id: str
    case_id: str
    document_id: str | None = None
    parent_id: str | None = None
    author: str
    body: str
    created_at: datetime
    edited_at: datetime | None = None
    deleted_at: datetime | None = None


def _note_body(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("A note can't be empty")
    if len(trimmed) > MAX_NOTE_LENGTH:
        raise ValueError(f"A note must be at most {MAX_NOTE_LENGTH} characters")
    return trimmed


class CreateNoteRequest(BaseModel):
    """A new note: on the whole case, on one of its documents, or a reply to an earlier note (parent_id)."""

    body: str
    document_id: UUID | None = None
    parent_id: UUID | None = None

    @field_validator("body")
    @classmethod
    def body_must_not_be_blank(cls, value: str) -> str:
        return _note_body(value)


class UpdateNoteRequest(BaseModel):
    """A note's new text. Where the note sits (case, document, parent) never changes."""

    body: str

    @field_validator("body")
    @classmethod
    def body_must_not_be_blank(cls, value: str) -> str:
        return _note_body(value)


class DocumentRecord(BaseModel):
    id: str
    case_id: str | None = None
    filename: str
    document_type: str | None = None
    # The Classifier's confidence in document_type — set once at classification
    # and never overwritten by Extraction. See extraction_confidence below for
    # the separate, later stage's own confidence in the field values it found.
    classification_confidence: float | None = None
    routing_status: RoutingStatus | None = None
    storage_path: str | None = None
    status: DocumentStatus
    retry_count: int = 0
    uploaded_at: datetime
    queued_at: datetime | None = None
    processing_started_at: datetime | None = None
    failure_reason: str | None = None
    evidence: list[str] = Field(default_factory=list)
    fulfilled_requirements: list[str] = Field(default_factory=list)
    # Not a documents-table column — document_repository.get_document/
    # list_documents populate this from the extracted_fields table on every
    # read (see document_repository._attach_extracted_fields). Plain
    # {field_name: value}; routers/documents.py's GET and case_service's
    # case-list enrichment separately rewrap it as {value, confidence} for
    # the API response.
    extracted_fields: dict | None = None
    missing_required_fields: list[str] = Field(default_factory=list)
    # Extraction's own confidence in the values it pulled into extracted_fields
    # — distinct from (and no longer overwrites) the classifier's
    # `classification_confidence` above. Set to 1.0 when a loan officer
    # manually saves corrected fields.
    extraction_confidence: float | None = None
    # When a loan officer saved the fields of an Other ("unrecognised")
    # document. Its fields come from no schema, so approving one needs it —
    # unlike extraction_confidence, which Extraction can also report as 1.0.
    fields_reviewed_at: datetime | None = None
    escalation_reason: str | None = None
    metadata: dict | None = None


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


class MoveDocumentRequest(BaseModel):
    """Reassigns a document to a different case — see routers/documents.py's move_document."""

    case_id: str


class BulkActionFailure(BaseModel):
    document_id: str
    reason: str


class BulkMoveRequest(BaseModel):
    document_ids: list[str]
    case_id: str


class BulkMoveResponse(BaseModel):
    moved: list[str]
    failed: list[BulkActionFailure]


class BulkDeleteRequest(BaseModel):
    document_ids: list[str]


class BulkDeleteResponse(BaseModel):
    deleted: list[str]
    failed: list[BulkActionFailure]


class StuckDocument(BaseModel):
    id: str
    case_id: str | None = None
    status: DocumentStatus
    seconds_in_current_status: float = Field(
        ..., description="How long the document has sat in its current status"
    )


class ProcessingErrorEntry(BaseModel):
    document_id: str
    error: str
    occurred_at: datetime


def is_classification_confirmed(document: DocumentRecord) -> bool:
    """
    Whether the document's type can be trusted enough to approve it. A real
    type needs the classifier at or above the threshold, or an officer's
    confirmation. "unrecognised" (Other) always needs the officer: the
    classifier's confidence there only says it couldn't find a match. An
    officer's pick in the type dropdown writes exactly 1.0 (see
    routers/documents.update_document_type), which the classifier never
    reaches (CERTAIN_CAP is 0.97), so 1.0 marks an officer-confirmed type.
    """
    confidence = document.classification_confidence
    if confidence is None:
        return False
    if document.document_type in (None, "", "unrecognised"):
        return confidence >= 1.0
    return confidence >= settings.CLASSIFICATION_CONFIDENCE_THRESHOLD


def skips_detection(document: DocumentRecord) -> bool:
    """Detection doesn't run on a document with no type at all. An approved
    Other ("unrecognised") document is scanned: only the checks that need no
    type run on it, the rest report nothing to check."""
    return document.document_type in (None, "")


def counts_towards_checklist(document: DocumentRecord) -> bool:
    """
    Whether a document can tick checklist items and establish identities or
    addresses (case_service.compute_requirement_coverage, mapper.py). Same
    bar as approval for the type: routing_status alone isn't enough, since
    an officer's "Save fields" flips it to ACCEPTED without confirming the
    type. A rejected document never counts.
    """
    return (
        document.routing_status == RoutingStatus.ACCEPTED
        and document.status != DocumentStatus.REJECTED
        and is_classification_confirmed(document)
    )
