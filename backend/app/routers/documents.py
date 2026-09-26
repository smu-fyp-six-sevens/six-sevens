from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from .. import (
    case_status,
    document_repository,
    extracted_fields_repository,
    orchestrator,
    storage_service,
)
from ..config import settings
from ..extraction_prompts import KNOWN_DOCUMENT_TYPES, schema_field_names
from ..models import (
    AddExtractedFieldRequest,
    ClassificationOutcome,
    DocumentDownloadResponse,
    DocumentRecord,
    DocumentStatus,
    UpdateDocumentTypeRequest,
    UpdateExtractedFieldsRequest,
    compute_fulfilled_requirements,
)
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
                **({"id": field.id} if field.id is not None else {}),
                "value": field.field_value,
                "confidence": field.confidence,
            }
            for field in extracted_fields
        }

    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: str) -> None:
    """
    Removes a document from its case. A following GET /documents/{id} or
    a case document list no longer returns it.
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

    if document.status == DocumentStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An approved document cannot be deleted.",
        )

    try:
        await asyncio.to_thread(document_repository.remove_document, document)
    except Exception as exc:
        logger.exception("Failed to delete document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to delete the document. Check Supabase table access.",
        ) from exc

    if document.case_id is None:
        return

    try:
        await asyncio.to_thread(case_status.sync_submission_finalized, document.case_id)
    except Exception:
        # The document row is already gone. The live case snapshot recomputes
        # requirement coverage from the documents that remain, so a failed
        # checklist write should not tell the officer the delete failed.
        logger.exception(
            "Deleted document %s but failed to resync case %s",
            document_id,
            document.case_id,
        )


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

@router.post(
    "/{document_id}/extracted-fields",
    response_model=DocumentRecord,
)
async def add_extracted_field(
    document_id: str,
    request: AddExtractedFieldRequest,
) -> DocumentRecord:
    """
    Adds one manually entered extracted field.

    Manually added fields are considered human-verified,
    so their confidence is automatically set to 1.0.
    """
    field_name = request.field_name.strip()

    if not field_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Field name cannot be empty.",
        )

    try:
        document = await asyncio.to_thread(
            document_repository.get_document,
            document_id,
        )

        if document is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found",
            )

        existing_fields = await asyncio.to_thread(
            extracted_fields_repository.get_extracted_fields,
            document_id,
        )

        if any(field.field_name == field_name for field in existing_fields):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A field with this name already exists.",
            )

        required_field_names = set(
            schema_field_names(document.document_type or "")
        )

        await asyncio.to_thread(
            extracted_fields_repository.add_extracted_field,
            document_id,
            field_name,
            request.field_value,
            required_field_names=required_field_names,
        )

        # Re-fetch the fields so the documents.extracted_fields JSON
        # and the normalized extracted_fields table stay in sync.
        all_fields = await asyncio.to_thread(
            extracted_fields_repository.get_extracted_fields,
            document_id,
        )

        extracted_fields = {
            field.field_name: field.field_value
            for field in all_fields
        }

        fulfilled_requirements = compute_fulfilled_requirements(
            document.document_type,
            extracted_fields,
        )

        await asyncio.to_thread(
            document_repository.update_extracted_fields,
            document_id,
            extracted_fields,
            1.0,
            fulfilled_requirements,
        )

        if document.case_id is not None:
            await asyncio.to_thread(
                case_status.sync_submission_finalized,
                document.case_id,
            )

        return await get_document(document_id)

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to add extracted field to document %s",
            document_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to add extracted field. Check Supabase table access.",
        ) from exc

@router.delete(
    "/{document_id}/extracted-fields/{field_id}",
    response_model=DocumentRecord,
)
async def delete_extracted_field(
    document_id: str,
    field_id: str,
) -> DocumentRecord:
    """
    Deletes one manually entered extracted field.

    The field must belong to the specified document.
    Fulfilled requirements are recalculated after deletion.
    """
    try:
        document = await asyncio.to_thread(
            document_repository.get_document,
            document_id,
        )

        if document is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found",
            )

        existing_fields = await asyncio.to_thread(
            extracted_fields_repository.get_extracted_fields,
            document_id,
        )

        field_to_delete = next(
            (field for field in existing_fields if str(field.id) == field_id),
            None,
        )

        if field_to_delete is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Extracted field not found",
            )

        await asyncio.to_thread(
            extracted_fields_repository.delete_extracted_field,
            document_id,
            field_id,
        )

        remaining_fields = await asyncio.to_thread(
            extracted_fields_repository.get_extracted_fields,
            document_id,
        )

        extracted_fields = {
            field.field_name: field.field_value
            for field in remaining_fields
        }

        fulfilled_requirements = compute_fulfilled_requirements(
            document.document_type,
            extracted_fields,
        )

        await asyncio.to_thread(
            document_repository.update_extracted_fields,
            document_id,
            extracted_fields,
            document.extraction_confidence,
            fulfilled_requirements,
        )

        if document.case_id is not None:
            await asyncio.to_thread(
                case_status.sync_submission_finalized,
                document.case_id,
            )

        return await get_document(document_id)

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to delete extracted field %s from document %s",
            field_id,
            document_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to delete extracted field. Check Supabase table access.",
        ) from exc

@router.post("/{document_id}/approve", response_model=DocumentRecord)
async def approve_document(document_id: str) -> DocumentRecord:
    """
    Approves a completed document after loan officer review.

    Only documents that have finished extraction can be approved.
    """
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

    if document.status != DocumentStatus.ANALYZED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only completed documents can be approved.",
        )

    try:
        await asyncio.to_thread(
            document_repository.approve_document,
            document_id,
        )

        if document.case_id is not None:
            await asyncio.to_thread(
                case_status.sync_submission_finalized,
                document.case_id,
            )

        return await get_document(document_id)

    except Exception as exc:
        logger.exception("Failed to approve document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to approve document.",
        ) from exc
    
@router.post("/{document_id}/reject", response_model=DocumentRecord)
async def reject_document(document_id: str) -> DocumentRecord:
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

    if document.status != DocumentStatus.ANALYZED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only completed documents can be rejected.",
        )

    try:
        await asyncio.to_thread(
            document_repository.reject_document,
            document_id,
        )

        return await get_document(document_id)

    except Exception as exc:
        logger.exception("Failed to reject document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to reject document.",
        ) from exc
    

@router.patch("/{document_id}/document-type", response_model=DocumentRecord)
async def update_document_type(
    document_id: str,
    request: UpdateDocumentTypeRequest,
    background_tasks: BackgroundTasks,
) -> DocumentRecord:
    """
    A loan officer correcting a low-confidence document_type (the frontend
    only offers this below settings.CLASSIFICATION_CONFIDENCE_THRESHOLD —
    enforced here too, so it can't be scripted around). Records the type as
    officer-confirmed (confidence 1.0, ACCEPTED), then re-runs Extraction
    only, in the background, against the new type — Extraction from the
    wrong schema is what made the old fields wrong in the first place. The
    response already reads EXTRACTING; the result arrives via the case
    status WebSocket like any other status change.
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

    try:
        # Self-fulfilment only, like _finalise_classification's first write:
        # the existing extracted_fields came from the wrong schema, so they
        # shouldn't be used to claim e.g. proof_of_address for the new type
        # — rerun_extraction recomputes this from the real re-extracted fields.
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
    except Exception as exc:
        logger.exception("Failed to update document type for document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to save the document type. Check Supabase table access.",
        ) from exc

    if document.case_id is not None:
        await asyncio.to_thread(case_status.sync_submission_finalized, document.case_id)

    background_tasks.add_task(orchestrator.rerun_extraction, document_id)

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
