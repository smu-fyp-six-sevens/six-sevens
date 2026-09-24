from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from .. import (
    case_status,
    document_repository,
    extracted_fields_repository,
    storage_service,
)
from ..config import settings
from ..extraction_prompts import KNOWN_DOCUMENT_TYPES, schema_field_names
from ..models import (
    ClassificationOutcome,
    DocumentDownloadResponse,
    DocumentRecord,
    DocumentStatus,
    UpdateDocumentTypeRequest,
    UpdateExtractedFieldsRequest,
    compute_fulfilled_requirements,
)
from ..langgraph import pipeline as langgraph_pipeline
from ..langgraph import trace as langgraph_trace
from ..queue_service import document_queue

# "unrecognised" is the "Other" catch-all: Extraction has a generic schema for it.
_SETTABLE_DOCUMENT_TYPES = {*KNOWN_DOCUMENT_TYPES, "unrecognised"}
# A pipeline run is already in flight for these — correcting the type now would race it.
_TYPE_OVERRIDE_BLOCKED_STATUSES = {
    DocumentStatus.UPLOADED,
    DocumentStatus.QUEUED,
    DocumentStatus.PROCESSING,
    DocumentStatus.EXTRACTING,
}

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)


@router.get("", response_model=list[DocumentRecord])
async def list_documents(
    case_id: str | None = None, doc_status: DocumentStatus | None = None
) -> list[DocumentRecord]:
    try:
        return await asyncio.to_thread(document_repository.list_documents, case_id, doc_status)
    except Exception as exc:
        logger.exception("Failed to list documents")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to list documents. Check Supabase table access.",
        ) from exc


@router.get("/{document_id}", response_model=DocumentRecord)
async def get_document(document_id: str) -> DocumentRecord:
    try:
        document = await asyncio.to_thread(
            document_repository.get_document,
            document_id,
        )
    except Exception as exc:
        logger.exception("Failed to fetch document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the document. Check Supabase table access.",
        ) from exc

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    try:
        extracted_fields = await asyncio.to_thread(
            extracted_fields_repository.get_extracted_fields,
            document_id,
        )
    except Exception as exc:
        logger.exception(
            "Failed to fetch extracted fields for document %s",
            document_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch extracted fields.",
        ) from exc

    if extracted_fields:
        document.extracted_fields = {
            field.field_name: {
                "value": field.field_value,
                "confidence": field.confidence,
            }
            for field in extracted_fields
        }

    return document


@router.patch("/{document_id}/extracted-fields", response_model=DocumentRecord)
async def update_extracted_fields(
    document_id: str, request: UpdateExtractedFieldsRequest
) -> DocumentRecord:
    """
    A loan officer manually correcting low-confidence extracted fields (see
    settings.EXTRACTION_CONFIDENCE_THRESHOLD — the frontend only shows an
    editable form below that threshold). Sets extraction_confidence to 1.0
    since a human has now verified the values, which also takes the document
    back above the threshold so the frontend stops showing it as editable.
    """
    try:
        document = await asyncio.to_thread(document_repository.get_document, document_id)
    except Exception as exc:
        logger.exception("Failed to fetch document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the document. Check Supabase table access.",
        ) from exc

    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    try:
        # Recomputed rather than left as whatever the automated pipeline last
        # wrote — a loan officer correcting individual_address (e.g. filling
        # one in the classifier/extractor missed) can change whether this
        # document now also fulfils proof_of_address.
        fulfilled_requirements = compute_fulfilled_requirements(
            document.document_type, request.extracted_fields
        )
        # Get the original per-field values and confidence scores
        # before replacing the extracted fields.
        existing_fields = await asyncio.to_thread(
            extracted_fields_repository.get_extracted_fields,
            document_id,
        )

        def normalise_value(value: object) -> str:
            """
            Convert values to a comparable representation because the
            frontend sends edited fields as strings, while the database
            may contain numbers, strings, lists, etc.
            """
            if value is None:
                return ""

            if isinstance(value, str):
                return value.strip()

            return str(value).strip()

        existing_by_name = {
            field.field_name: field
            for field in existing_fields
        }

        field_confidences = {}

        is_unrecognised_document = (
            document.document_type == "unrecognised"
        )

        for name, new_value in request.extracted_fields.items():
            existing_field = existing_by_name.get(name)

            if existing_field is None:
                # New field added during manual review.
                field_confidences[name] = 1.0
                continue

            old_value = normalise_value(existing_field.field_value)

            # These fields were editable/reviewable by the loan officer.
            was_editable = (
                is_unrecognised_document
                or old_value == ""
                or (
                    existing_field.confidence is not None
                    and existing_field.confidence < settings.EXTRACTION_CONFIDENCE_THRESHOLD
                )
            )

            if was_editable:
                # The officer reviewed this field by clicking Save,
                # so it is now considered human-verified.
                field_confidences[name] = 1.0
            else:
                # This field was not part of manual review.
                # Preserve the original extraction confidence.
                field_confidences[name] = existing_field.confidence

        await asyncio.to_thread(
            document_repository.update_extracted_fields,
            document_id,
            request.extracted_fields,
            1.0,
            fulfilled_requirements,
        )

        await asyncio.to_thread(
            extracted_fields_repository.replace_extracted_fields,
            document_id,
            request.extracted_fields,
            field_confidences=field_confidences,
            default_confidence=None,
            required_field_names=set(
                schema_field_names(document.document_type or "")
            ),
        )
    except Exception as exc:
        logger.exception("Failed to update extracted fields for document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to save extracted fields. Check Supabase table access.",
        ) from exc

    if document.case_id is not None:
        await asyncio.to_thread(case_status.sync_submission_finalized, document.case_id)

    updated = await get_document(document_id)
    return updated


@router.patch("/{document_id}/document-type", response_model=DocumentRecord)
async def update_document_type(
    document_id: str,
    request: UpdateDocumentTypeRequest,
    background_tasks: BackgroundTasks,
) -> DocumentRecord:
    """
    A loan officer confirming or correcting a low-confidence document_type
    (the frontend only offers this below settings.CLASSIFICATION_CONFIDENCE_THRESHOLD
    — enforced here too, so it can't be scripted around). Always records the
    type as officer-confirmed (confidence 1.0, ACCEPTED).

    Only actually changing the type re-runs Extraction, in the background —
    Extraction from the wrong schema is what made the old fields wrong in
    the first place, so a genuine correction needs a re-run. Confirming the
    type as-is has nothing new for Extraction to find (it already ran once
    against this same type), so that case only touches the classification
    columns and leaves status/extracted_fields untouched — the response
    reads EXTRACTING only when a re-run was actually kicked off.
    """
    document_type = request.document_type.strip().lower()
    if document_type not in _SETTABLE_DOCUMENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown document_type: {request.document_type}",
        )

    try:
        document = await asyncio.to_thread(document_repository.get_document, document_id)
    except Exception as exc:
        logger.exception("Failed to fetch document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the document. Check Supabase table access.",
        ) from exc

    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if document.status in _TYPE_OVERRIDE_BLOCKED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document is still being processed; try again once it finishes.",
        )

    if (
        document.confidence is not None
        and document.confidence >= settings.CLASSIFICATION_CONFIDENCE_THRESHOLD
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Classification confidence is above the threshold; the type can't be overridden.",
        )

    type_changed = document_type != document.document_type

    try:
        if type_changed:
            # Self-fulfilment only, like _finalise_classification's first
            # write: the existing extracted_fields came from the wrong
            # schema, so they shouldn't be used to claim e.g.
            # proof_of_address for the new type — the extraction rerun
            # recomputes this from the real re-extracted fields. Old
            # evidence was reasoning for the wrong type, so it's cleared too.
            await asyncio.to_thread(
                document_repository.record_classification,
                document_id,
                document_type=document_type,
                confidence=1.0,
                classification_outcome=ClassificationOutcome.ACCEPTED.value,
                evidence=[],
                fulfilled_requirements=compute_fulfilled_requirements(document_type, None),
            )
            await asyncio.to_thread(
                document_repository.update_status,
                document_id,
                DocumentStatus.EXTRACTING,
                processing_started_at=datetime.now(timezone.utc),
            )
        else:
            # Confirming an already-correct low-confidence guess: Extraction
            # already ran against this exact type, so its fields are still
            # valid — recompute fulfilled_requirements from them (more
            # accurate than the None used above) instead of discarding
            # anything, and keep the existing evidence since it was already
            # reasoning for this same type.
            await asyncio.to_thread(
                document_repository.record_classification,
                document_id,
                document_type=document_type,
                confidence=1.0,
                classification_outcome=ClassificationOutcome.ACCEPTED.value,
                evidence=document.evidence,
                fulfilled_requirements=compute_fulfilled_requirements(document_type, document.extracted_fields),
            )
    except Exception as exc:
        logger.exception("Failed to update document type for document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to save the document type. Check Supabase table access.",
        ) from exc

    if document.case_id is not None:
        await asyncio.to_thread(case_status.sync_submission_finalized, document.case_id)

    if type_changed:
        background_tasks.add_task(langgraph_pipeline.rerun_extraction, document_id)

    return await get_document(document_id)


@router.post("/{document_id}/retry", response_model=DocumentRecord)
async def retry_document(document_id: str) -> DocumentRecord:
    """
    Re-enqueues a document that's stuck or failed. Exists specifically for
    orphaned in-memory-queue state: this queue (see queue_service.py) does
    not survive a backend restart, so any document still QUEUED/PROCESSING
    at that moment is permanently abandoned — nothing else will ever pick
    it back up. Also useful for a genuinely FAILED document the officer
    wants to re-attempt. Refuses ANALYZED documents since there's nothing
    to retry there.
    """
    try:
        document = await asyncio.to_thread(document_repository.get_document, document_id)
    except Exception as exc:
        logger.exception("Failed to fetch document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the document. Check Supabase table access.",
        ) from exc

    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if document.status == DocumentStatus.ANALYZED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document already analyzed; nothing to retry.",
        )

    try:
        # update_status only ever writes fields passed as non-None, so this
        # can't clear a stale failure_reason from the prior attempt — it's
        # left in place and will read as stale once this retry succeeds.
        # Pre-existing limitation of update_status, not new to this retry
        # path (the classification-escalation loop's own success path has
        # the same gap), so not fixed here.
        await asyncio.to_thread(
            document_repository.update_status,
            document_id,
            DocumentStatus.QUEUED,
            retry_count=document.retry_count + 1,
        )
        await document_queue.enqueue(document_id)
    except Exception as exc:
        logger.exception("Failed to re-enqueue document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to re-enqueue the document.",
        ) from exc

    return await asyncio.to_thread(document_repository.get_document, document_id)


@router.get("/{document_id}/langgraph-trace")
async def get_document_langgraph_trace(document_id: str) -> dict:
    """
    Dev-only window into the langgraph pipeline (see app/langgraph/trace.py)
    — which route a document took (fast vs Sonnet agent), what rule/llm/
    vision each said, whether it escalated. Read from the Supabase
    `langgraph_trace` table; falls back to this process's in-memory copy if the
    row isn't there yet. langgraph-test branch only; not for production use.
    """
    try:
        result = await asyncio.to_thread(langgraph_trace.get_persisted, document_id)
    except Exception:  # noqa: BLE001 — table unreachable: fall back to in-memory
        result = None
    if result is None:
        result = langgraph_trace.get(document_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No langgraph trace found for this document.",
        )
    return result


@router.get("/{document_id}/download", response_model=DocumentDownloadResponse)
async def get_document_download_url(document_id: str) -> DocumentDownloadResponse:
    """
    Returns a short-lived signed URL for the document's file in Supabase
    Storage rather than proxying the bytes through this service.
    """
    try:
        document = await asyncio.to_thread(document_repository.get_document, document_id)
    except Exception as exc:
        logger.exception("Failed to fetch document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the document. Check Supabase table access.",
        ) from exc

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    if document.storage_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document has no stored file"
        )

    try:
        signed_url = await asyncio.to_thread(
            storage_service.create_signed_url,
            document.storage_path,
            settings.SIGNED_URL_EXPIRY_SECONDS,
            document.filename,
        )
    except Exception as exc:
        logger.exception("Failed to create signed URL for document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to generate download URL",
        ) from exc

    return DocumentDownloadResponse(
        document_id=document.id,
        signed_url=signed_url,
        expires_in=settings.SIGNED_URL_EXPIRY_SECONDS,
    )
