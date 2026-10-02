from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel

from .. import case_service, document_repository, extracted_fields_repository, monitoring, storage_service
from ..case_broadcaster import broadcaster

from ._dependencies import get_document_or_404
from ..config import settings
from ..langgraph.extraction_prompts import (
    EXTRACTION_SCHEMAS,
    KNOWN_DOCUMENT_TYPES,
    schema_field_names,
)
from ..models import (
    IN_FLIGHT_STATUSES,
    AddExtractedFieldRequest,
    BulkActionFailure,
    BulkDeleteRequest,
    BulkDeleteResponse,
    BulkMoveRequest,
    BulkMoveResponse,
    DocumentRecord,
    DocumentStatus,
    MoveDocumentRequest,
    RoutingStatus,
    UpdateDocumentTypeRequest,
    UpdateExtractedFieldsRequest,
    compute_fulfilled_requirements,
)
from ..langgraph import pipeline as langgraph_pipeline
from ..langgraph import trace as langgraph_trace
from ..langgraph.detection.graph import run_detection
from ..queue_service import document_queue

# "unrecognised" is the "Other" catch-all: Extraction has a generic schema for it.
_SETTABLE_DOCUMENT_TYPES = {*KNOWN_DOCUMENT_TYPES, "unrecognised"}
# A pipeline run is already in flight for these — correcting the type now would race it.
_TYPE_OVERRIDE_BLOCKED_STATUSES = IN_FLIGHT_STATUSES

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)
async def _start_detection_after_approval(document_id: str) -> None:
    try:
        await run_detection(document_id)
    except Exception:
        logger.exception(
            "Fraud detection failed for approved document %s",
            document_id,
        )

class DocumentDownloadResponse(BaseModel):
    document_id: str
    signed_url: str
    expires_in: int


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

    document.missing_required_fields = _missing_required_fields(
        document.document_type,
        extracted_fields,
    )

    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> None:
    """
    Removes a document from its case. A following GET /documents/{id} or
    a case document list no longer returns it.

    Only allowed once the document has reached a terminal state (ANALYZED,
    FAILED, REJECTED, CANCELLED) — deleting one still QUEUED/PROCESSING/
    EXTRACTING would race the pipeline actively writing to it (status
    updates, extracted fields) and orphan a document row mid-flight. Cancel
    it first if you want to stop and then delete an in-flight document.
    """
    if document.status == DocumentStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An approved document cannot be deleted.",
        )
    if document.status in IN_FLIGHT_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Document is still being processed; cancel it first, or wait "
                "for it to finish, before deleting."
            ),
        )

    try:
        await asyncio.to_thread(document_repository.remove_document, document)
    except Exception as exc:
        logger.exception("Failed to delete document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to delete the document. Check Supabase table access.",
        ) from exc


async def _push_case_snapshot(case_id: str) -> None:
    """Best-effort live refresh for a case's websocket subscribers (see
    case_service.build_case_snapshot / routers/ws.py) — never raises, since
    the write that triggered this already succeeded and is the source of
    truth; a failed push just means that case's view goes stale until its
    next real update instead."""
    try:
        snapshot = await asyncio.to_thread(case_service.build_case_snapshot, case_id)
    except Exception:
        logger.exception("Failed to build case snapshot for case %s", case_id)
        return
    if snapshot is not None:
        broadcaster.publish(case_id, snapshot)


async def _resync_case(case_id: str) -> None:
    """Persists submission_finalized and pushes a fresh websocket snapshot —
    the two things any mutation that can change a case's document set needs
    to do. Best-effort/never raises, same reasoning as _push_case_snapshot."""
    try:
        await asyncio.to_thread(case_service.sync_submission_finalized, case_id)
    except Exception:
        logger.exception("Failed to resync submission_finalized for case %s", case_id)
    await _push_case_snapshot(case_id)


@router.post("/{document_id}/move", response_model=DocumentRecord)
async def move_document(
    document_id: str,
    body: MoveDocumentRequest,
    document: DocumentRecord = Depends(get_document_or_404),
) -> DocumentRecord:
    """
    Reassigns a document to a different case — corrects a document uploaded
    under the wrong case. Same eligibility as delete above: an approved
    document is locked, and one still mid-pipeline would race it.
    """
    if document.status == DocumentStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An approved document cannot be moved.",
        )
    if document.status in IN_FLIGHT_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Document is still being processed; cancel it first, or wait "
                "for it to finish, before moving it."
            ),
        )
    if document.case_id == body.case_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document is already in that case.",
        )

    target_case = await asyncio.to_thread(case_service.get_case, body.case_id)
    if target_case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target case not found")

    old_case_id = document.case_id
    try:
        await asyncio.to_thread(document_repository.move_document, document_id, body.case_id)
    except Exception as exc:
        logger.exception("Failed to move document %s to case %s", document_id, body.case_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to move the document. Check Supabase table access.",
        ) from exc

    # document_repository.move_document already resynced the new case itself
    # (see its own _sync_case call) — only the old case still needs it here.
    # Its own Realtime row-change event (if any) only ever carries this
    # document's NEW case_id, so it would never otherwise learn it lost one.
    if old_case_id is not None:
        await _resync_case(old_case_id)

    return await get_document(document_id)


@router.post("/bulk-move", response_model=BulkMoveResponse)
async def bulk_move_documents(body: BulkMoveRequest) -> BulkMoveResponse:
    """
    Same eligibility as the single-document move above, applied to a batch
    in one DB round trip — and each affected case is resynced exactly once
    after the whole batch lands, not once per document (that's the one
    thing a loop of N single-document moves can't do).
    """
    if not body.document_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No documents specified.")

    target_case = await asyncio.to_thread(case_service.get_case, body.case_id)
    if target_case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target case not found")

    try:
        documents = await asyncio.to_thread(
            document_repository.get_documents_by_ids, body.document_ids, include_fields=False
        )
    except Exception as exc:
        logger.exception("Failed to fetch documents %s", body.document_ids)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the documents. Check Supabase table access.",
        ) from exc
    documents_by_id = {document.id: document for document in documents}

    eligible_ids: list[str] = []
    failed: list[BulkActionFailure] = []
    old_case_ids: set[str] = set()

    for document_id in body.document_ids:
        document = documents_by_id.get(document_id)
        if document is None:
            failed.append(BulkActionFailure(document_id=document_id, reason="Document not found"))
        elif document.status == DocumentStatus.APPROVED:
            failed.append(
                BulkActionFailure(document_id=document_id, reason="An approved document cannot be moved.")
            )
        elif document.status in IN_FLIGHT_STATUSES:
            failed.append(
                BulkActionFailure(document_id=document_id, reason="Document is still being processed.")
            )
        elif document.case_id == body.case_id:
            failed.append(
                BulkActionFailure(document_id=document_id, reason="Document is already in that case.")
            )
        else:
            eligible_ids.append(document_id)
            if document.case_id is not None:
                old_case_ids.add(document.case_id)

    if eligible_ids:
        try:
            await asyncio.to_thread(document_repository.move_documents, eligible_ids, body.case_id)
        except Exception as exc:
            logger.exception("Bulk move failed for documents %s", eligible_ids)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to move the documents. Check Supabase table access.",
            ) from exc

        for case_id in {*old_case_ids, body.case_id}:
            await _resync_case(case_id)

    return BulkMoveResponse(moved=eligible_ids, failed=failed)


@router.post("/bulk-delete", response_model=BulkDeleteResponse)
async def bulk_delete_documents(body: BulkDeleteRequest) -> BulkDeleteResponse:
    """
    Same eligibility as the single-document delete above, applied to a batch
    — each affected case is resynced exactly once after the whole batch
    lands, not once per document.
    """
    if not body.document_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No documents specified.")

    try:
        documents = await asyncio.to_thread(
            document_repository.get_documents_by_ids, body.document_ids, include_fields=False
        )
    except Exception as exc:
        logger.exception("Failed to fetch documents %s", body.document_ids)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the documents. Check Supabase table access.",
        ) from exc
    documents_by_id = {document.id: document for document in documents}

    eligible: list[DocumentRecord] = []
    failed: list[BulkActionFailure] = []
    case_ids: set[str] = set()

    for document_id in body.document_ids:
        document = documents_by_id.get(document_id)
        if document is None:
            failed.append(BulkActionFailure(document_id=document_id, reason="Document not found"))
        elif document.status == DocumentStatus.APPROVED:
            failed.append(
                BulkActionFailure(document_id=document_id, reason="An approved document cannot be deleted.")
            )
        elif document.status in IN_FLIGHT_STATUSES:
            failed.append(
                BulkActionFailure(document_id=document_id, reason="Document is still being processed.")
            )
        else:
            eligible.append(document)
            if document.case_id is not None:
                case_ids.add(document.case_id)

    if eligible:
        try:
            await asyncio.to_thread(document_repository.remove_documents, eligible)
        except Exception as exc:
            logger.exception("Bulk delete failed for documents %s", [d.id for d in eligible])
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to delete the documents. Check Supabase table access.",
            ) from exc

        for case_id in case_ids:
            await _resync_case(case_id)

    return BulkDeleteResponse(deleted=[d.id for d in eligible], failed=failed)


@router.patch("/{document_id}/extracted-fields", response_model=DocumentRecord)
async def update_extracted_fields(
    document_id: str,
    request: UpdateExtractedFieldsRequest,
    document: DocumentRecord = Depends(get_document_or_404),
) -> DocumentRecord:
    """
    A loan officer manually correcting low-confidence extracted fields (see
    settings.EXTRACTION_CONFIDENCE_THRESHOLD — the frontend only shows an
    editable form below that threshold). Sets extraction_confidence to 1.0
    since a human has now verified the values, which also takes the document
    back above the threshold so the frontend stops showing it as editable.
    """
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

        # The officer has now reviewed every flagged field, which resolves
        # the manual review: routing moves Manual Review -> Accepted here,
        # and only approve_document later moves it on to Approved.
        await asyncio.to_thread(
            document_repository.update_extracted_fields,
            document_id,
            1.0,
            fulfilled_requirements,
            routing_status=RoutingStatus.ACCEPTED.value,
        )

        await asyncio.to_thread(
            extracted_fields_repository.replace_extracted_fields,
            document_id,
            request.extracted_fields,
            field_confidences=field_confidences,
            default_confidence=None,
            required_field_names = set(
                schema_field_names(document.document_type or "")
            )
        )
    except Exception as exc:
        logger.exception("Failed to update extracted fields for document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to save extracted fields. Check Supabase table access.",
        ) from exc

    updated = await get_document(document_id)
    return updated

@router.post(
    "/{document_id}/extracted-fields",
    response_model=DocumentRecord,
)
async def add_extracted_field(
    document_id: str,
    request: AddExtractedFieldRequest,
    document: DocumentRecord = Depends(get_document_or_404),
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

        # Re-fetch the full field set (including the one just added) to
        # recompute fulfilled_requirements against it.
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
            # The new field itself is stored at 1.0, but adding one isn't
            # the officer's "Save fields" review — extraction_confidence=1.0
            # (with routing_status=accepted) is what the frontend reads as
            # "fields verified and locked", so it's carried through unchanged.
            document.extraction_confidence,
            fulfilled_requirements,
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
    document: DocumentRecord = Depends(get_document_or_404),
) -> DocumentRecord:
    """
    Deletes one manually entered extracted field.

    The field must belong to the specified document.
    Fulfilled requirements are recalculated after deletion.
    """
    try:
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
            # A deletion doesn't make the document human-verified (unlike
            # add_extracted_field's confidence=1.0), so whatever confidence
            # it already had — including None, for a document Extraction
            # never ran against — is carried through unchanged.
            document.extraction_confidence,
            fulfilled_requirements,
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

def _missing_required_fields(
    document_type: str | None,
    extracted_fields,
) -> list[str]:
    required_field_names = set(
        EXTRACTION_SCHEMAS.get(document_type or "", {}).get("required", [])
    )

    if not required_field_names:
        return []

    fields_by_name = {
        field.field_name: field
        for field in extracted_fields
    }

    missing: list[str] = []

    for field_name in sorted(required_field_names):
        field = fields_by_name.get(field_name)

        if field is None:
            missing.append(field_name)
            continue

        value = field.field_value

        if value is None:
            missing.append(field_name)
            continue

        if isinstance(value, str) and not value.strip():
            missing.append(field_name)
            continue

        if isinstance(value, (list, dict)) and not value:
            missing.append(field_name)

    return missing

@router.post("/{document_id}/approve", response_model=DocumentRecord)
async def approve_document(
    document_id: str,
    background_tasks: BackgroundTasks,
    document: DocumentRecord = Depends(get_document_or_404),
) -> DocumentRecord:
    """
    Approves a completed document after loan officer review.

    Only documents that have finished extraction can be approved.
    """
    if document.status != DocumentStatus.ANALYZED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only completed documents can be approved.",
        )

    try:
        extracted_fields = await asyncio.to_thread(
            extracted_fields_repository.get_extracted_fields,
            document_id,
        )

        missing_required_fields = _missing_required_fields(
            document.document_type,
            extracted_fields,
        )

        if missing_required_fields:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Cannot approve document. Required fields are missing "
                    "or empty: "
                    + ", ".join(missing_required_fields)
                ),
            )

        await asyncio.to_thread(
            document_repository.approve_document,
            document_id,
        )

        background_tasks.add_task(
            _start_detection_after_approval,
            document_id,
        )

        return await get_document(document_id)

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to approve document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to approve document.",
        ) from exc
    
@router.post("/{document_id}/reject", response_model=DocumentRecord)
async def reject_document(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> DocumentRecord:
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
    document: DocumentRecord = Depends(get_document_or_404),
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

    if document.status in _TYPE_OVERRIDE_BLOCKED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document is still being processed; try again once it finishes.",
        )

    if (
        document.classification_confidence is not None
        and document.classification_confidence >= settings.CLASSIFICATION_CONFIDENCE_THRESHOLD
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Classification confidence is above the threshold; the type can't be overridden.",
        )

    type_changed = document_type != document.document_type

    try:
        if type_changed:
            # Self-fulfilment only, like nodes.record_classification_result's first
            # write: the existing extracted_fields came from the wrong
            # schema, so they shouldn't be used to claim e.g.
            # proof_of_address for the new type — the extraction rerun
            # recomputes this from the real re-extracted fields. Old
            # evidence was reasoning for the wrong type, so it's cleared too.
            await asyncio.to_thread(
                document_repository.record_classification,
                document_id,
                document_type=document_type,
                classification_confidence=1.0,
                routing_status=RoutingStatus.ACCEPTED.value,
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
                classification_confidence=1.0,
                routing_status=RoutingStatus.ACCEPTED.value,
                evidence=document.evidence,
                fulfilled_requirements=compute_fulfilled_requirements(document_type, document.extracted_fields),
            )
    except Exception as exc:
        logger.exception("Failed to update document type for document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to save the document type. Check Supabase table access.",
        ) from exc

    if type_changed:
        background_tasks.add_task(langgraph_pipeline.rerun_extraction, document_id)

    return await get_document(document_id)


@router.post("/{document_id}/retry", response_model=DocumentRecord)
async def retry_document(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> DocumentRecord:
    """
    Re-enqueues a document that's FAILED, or stuck — PROCESSING for longer
    than settings.STUCK_PROCESSING_RETRY_THRESHOLD_SECONDS (see
    monitoring.is_stuck_processing_for_retry). The stuck case exists
    specifically for orphaned in-memory-queue state: this queue (see
    queue_service.py) does not survive a backend restart, so a document
    still PROCESSING at that moment is permanently abandoned — nothing else
    will ever pick it back up. Deliberately does NOT allow retrying a
    document that's merely QUEUED or normally PROCESSING (not yet past the
    stuck threshold) — a worker may genuinely still be running it, and
    racing that with a second concurrent pipeline run would duplicate LLM
    calls and interleave writes unpredictably. Use cancel_document first if
    you want to stop an actively-running document instead.
    """
    is_stuck = monitoring.is_stuck_processing_for_retry(document)
    if document.status != DocumentStatus.FAILED and not is_stuck:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Document can only be retried when it has failed, or has been "
                "processing for an unusually long time (possibly orphaned)."
            ),
        )

    try:
        # Guards against a race with e.g. a concurrent cancel or the
        # in-flight run itself finishing between the check above and here —
        # if the status has moved on, this simply doesn't match and updated
        # stays False below.
        updated = await asyncio.to_thread(
            document_repository.update_status,
            document_id,
            DocumentStatus.QUEUED,
            retry_count=document.retry_count + 1,
            expected_current_statuses=[document.status],
        )
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Document's state changed; refetch and try again.",
            )
        # update_status only ever writes fields passed as non-None, so this
        # can't clear a stale failure_reason from the prior attempt — it's
        # left in place and will read as stale once this retry succeeds.
        # Pre-existing limitation of update_status, not new to this retry
        # path (the classification-escalation loop's own success path has
        # the same gap), so not fixed here.
        await document_queue.enqueue(document_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to re-enqueue document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to re-enqueue the document.",
        ) from exc

    refreshed = await asyncio.to_thread(document_repository.get_document, document_id)
    if refreshed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return refreshed


@router.post("/{document_id}/cancel", response_model=DocumentRecord)
async def cancel_document(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> DocumentRecord:
    """
    Stops a document that's still QUEUED, PROCESSING, or EXTRACTING. The
    status write is an atomic compare-and-swap (only applies if the document
    is still in one of those statuses), so two concurrent cancels — or a
    cancel racing the pipeline reaching a terminal status on its own — can't
    both "win". Only once that write succeeds do we attempt to interrupt an
    in-flight run (see queue_service.DocumentQueue.cancel_document); a
    QUEUED document that hasn't been dequeued yet needs no interruption,
    since queue_service._process_document checks status on dequeue and
    skips it.
    """
    updated = await asyncio.to_thread(
        document_repository.update_status,
        document_id,
        DocumentStatus.CANCELLED,
        expected_current_statuses=[
            DocumentStatus.QUEUED,
            DocumentStatus.PROCESSING,
            DocumentStatus.EXTRACTING,
        ],
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document can only be cancelled while queued, processing, or extracting.",
        )

    document_queue.cancel_document(document_id)

    refreshed = await asyncio.to_thread(document_repository.get_document, document_id)
    if refreshed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return refreshed


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
async def get_document_download_url(
    document_id: str,
    inline: bool = False,
    document: DocumentRecord = Depends(get_document_or_404),
) -> DocumentDownloadResponse:
    """
    Returns a short-lived signed URL for the document's file in Supabase
    Storage rather than proxying the bytes through this service.

    inline=True omits the `download` option Storage otherwise sets, which is
    what puts a `Content-Disposition: attachment` header on the response —
    without it, a browser renders the file (PDF/image) in place instead of
    downloading it. Used by the frontend's preview modal; plain downloads
    (the default) still get the attachment header + filename.
    """
    if document.storage_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document has no stored file"
        )

    try:
        signed_url = await asyncio.to_thread(
            storage_service.create_signed_url,
            document.storage_path,
            settings.SIGNED_URL_EXPIRY_SECONDS,
            None if inline else document.filename,
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
