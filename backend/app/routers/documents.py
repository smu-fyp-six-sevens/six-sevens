from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from .. import case_service, document_repository, events, extracted_fields_repository, monitoring, storage_service
from ..events import officer_event, types
from ..extracted_fields_repository import normalise_field_name
from ..guardrails import content_guard
from ..snapshots import push_case_snapshot

from ._dependencies import (
    bad_gateway_on_error,
    get_document_or_404,
    get_document_with_fields_or_404,
    to_thread_or_502,
)
from ..config import settings
from ..langgraph.extraction_prompts import (
    KNOWN_DOCUMENT_TYPES,
    drop_empty_optional_fields,
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
    APPROVED_STATUSES,
    is_classification_confirmed,
    skips_detection,
)
from ..langgraph import trace as langgraph_trace

# "unrecognised" is the "Other" catch-all.
_SETTABLE_DOCUMENT_TYPES = {*KNOWN_DOCUMENT_TYPES, "unrecognised"}
# A pipeline run is in flight for these; re-typing would race it.
_TYPE_OVERRIDE_BLOCKED_STATUSES = IN_FLIGHT_STATUSES
# The officer's decision is final: approved (and later) and rejected documents are locked.
_LOCKED_STATUSES = APPROVED_STATUSES | {DocumentStatus.REJECTED}
_UNDELETABLE_STATUSES = APPROVED_STATUSES

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)


def _ensure_unlocked(document: DocumentRecord, action: str) -> None:
    if document.status in _LOCKED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An {document.status.value} document is locked and cannot be {action}.",
        )


class DocumentDownloadResponse(BaseModel):
    document_id: str
    signed_url: str
    expires_in: int


class ContentGuardFinding(BaseModel):
    where: str  # hidden, image, metadata, filename or visible
    page: int | None = None
    field: str | None = None  # the metadata field, when where is metadata
    quote: str
    instruction: bool  # worded as an instruction to the AI; otherwise hidden text with no such wording
    why_hidden: list[str] = []


class ContentGuardResponse(BaseModel):
    checked: bool  # false: the guard didn't run on this document (or couldn't, see error)
    risk: str | None  # none, low, medium or high
    blocked: bool  # stopped at ingest, before any model read it
    error: str | None = None
    findings: list[ContentGuardFinding] = []


@router.get("", response_model=list[DocumentRecord])
async def list_documents(
    case_id: str | None = None, doc_status: DocumentStatus | None = None
) -> list[DocumentRecord]:
    return await to_thread_or_502(
        document_repository.list_documents, case_id, doc_status,
        detail="Failed to list documents. Check Supabase table access.",
        log=("Failed to list documents",),
    )


def _present(document: DocumentRecord, fields: list) -> DocumentRecord:
    """The document as returned: fields wrapped with id and confidence, plus missing required ones. Uses fields the caller already holds."""
    if fields:
        document.extracted_fields = {
            field.field_name: {
                **({"id": field.id} if field.id is not None else {}),
                "value": field.field_value,
                "confidence": field.confidence,
            }
            for field in fields
        }
    document.missing_required_fields = case_service.missing_required_fields(document.document_type, fields)
    return document


@router.get("/{document_id}", response_model=DocumentRecord)
async def get_document(document_id: str) -> DocumentRecord:
    document = await to_thread_or_502(
        document_repository.get_document,
        document_id,
        include_fields=False,
        detail="Failed to fetch the document. Check Supabase table access.",
        log=("Failed to fetch document %s", document_id),
    )

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    extracted_fields = await to_thread_or_502(
        extracted_fields_repository.get_extracted_fields,
        document_id,
        detail="Failed to fetch extracted fields.",
        log=("Failed to fetch extracted fields for document %s", document_id),
    )

    return _present(document, extracted_fields)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> None:
    """Removes a document from its case. Only once it is out of flight; cancel an in-flight one first."""
    if document.status in _UNDELETABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An {document.status.value} document cannot be deleted.",
        )
    if document.status in IN_FLIGHT_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Document is still being processed; cancel it first, or wait "
                "for it to finish, before deleting."
            ),
        )

    await to_thread_or_502(
        document_repository.remove_document, document,
        detail="Failed to delete the document. Check Supabase table access.",
        log=("Failed to delete document %s", document_id),
    )
    await _audit(types.DOCUMENT_DELETED, document, filename=document.filename)


async def _audit(event_type: str, document: DocumentRecord, **detail: object) -> None:
    """An officer action on one document, for the audit trail."""
    await events.record(officer_event(event_type, document_id=document.id, case_id=document.case_id, detail=detail))


def _normalise_value(value: object) -> str:
    """The frontend sends strings; the database may hold numbers or lists."""
    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    return str(value).strip()


# A field value kept in an audit entry is cut to this many characters.
_AUDIT_VALUE_CHARS = 500


def _whole_numbers(value: object) -> object:
    """6300.0 as 6300, at any depth: the database keeps floats, the page sends back what it displayed."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [_whole_numbers(item) for item in value]
    if isinstance(value, dict):
        return {key: _whole_numbers(item) for key, item in value.items()}
    return value


def _canonical_value(value: object) -> str:
    """
    One spelling per value, so a save only records what the officer actually changed: 6300.0, 6300 and "6300"
    match, and a list or object is compared as JSON with its keys in order.
    """
    value = _whole_numbers(value)
    # True as "true", as the page displays and sends it back.
    if isinstance(value, (list, dict, bool)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return _normalise_value(value)


def _audit_value(value: object) -> str:
    return _canonical_value(value)[:_AUDIT_VALUE_CHARS]


def _items(count: int) -> str:
    return f"{count} item{'' if count == 1 else 's'}"


def _list_change(name: str, before: list, after: list) -> dict[str, object]:
    """
    A list field (accounts, transactions, ...) is too long to show whole: its item counts, and the items the edit
    added or removed. An edited item is both: removed as it was, added as it is now.
    """
    old_items = [_canonical_value(item) for item in before]
    new_items = [_canonical_value(item) for item in after]
    remaining = list(old_items)
    added = []
    for item in new_items:
        if item in remaining:
            remaining.remove(item)
        else:
            added.append(item[:_AUDIT_VALUE_CHARS])
    return {
        "field": name, "from": _items(len(before)), "to": _items(len(after)),
        "added": added, "removed": [item[:_AUDIT_VALUE_CHARS] for item in remaining],
    }


def _field_changes(before: dict[str, object], after: dict[str, object]) -> list[dict[str, object]]:
    """
    Each field whose value an edit changed, with its old and new value (None: the field wasn't there), so the
    audit trail shows what the officer changed. Whole values are compared; only the stored copies are cut.
    """
    changes: list[dict[str, object]] = []
    for name in sorted(before.keys() | after.keys()):
        old = _canonical_value(before[name]) if name in before else None
        new = _canonical_value(after[name]) if name in after else None
        # A blank field the save drops (an optional one left empty) changed nothing.
        if (old or "") == (new or ""):
            continue
        old_value, new_value = before.get(name), after.get(name)
        if isinstance(old_value, list) and isinstance(new_value, list):
            changes.append(_list_change(name, old_value, new_value))
            continue
        changes.append({
            "field": name,
            "from": None if old is None else old[:_AUDIT_VALUE_CHARS],
            "to": None if new is None else new[:_AUDIT_VALUE_CHARS],
        })
    return changes


async def _resync_case(case_id: str) -> None:
    """Persists submission_finalized and pushes a snapshot, for any change to a case's document set. Never raises."""
    try:
        await asyncio.to_thread(case_service.sync_submission_finalized, case_id)
    except Exception:
        logger.exception("Failed to resync submission_finalized for case %s", case_id)
    await push_case_snapshot(case_id)


@router.post("/{document_id}/move", response_model=DocumentRecord)
async def move_document(
    document_id: str,
    body: MoveDocumentRequest,
    document: DocumentRecord = Depends(get_document_or_404),
) -> DocumentRecord:
    """Reassigns a document to another case. Same eligibility as delete."""
    if document.status in _LOCKED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An {document.status.value} document cannot be moved.",
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
    await to_thread_or_502(
        document_repository.move_document, document_id, body.case_id,
        detail="Failed to move the document. Check Supabase table access.",
        log=("Failed to move document %s to case %s", document_id, body.case_id),
    )

    # The repository resyncs the new case; the old one never sees an event carrying it.
    if old_case_id is not None:
        await _resync_case(old_case_id)
    await _audit(types.DOCUMENT_MOVED, document, from_case_id=old_case_id, to_case_id=body.case_id)

    return await get_document(document_id)


@router.post("/bulk-move", response_model=BulkMoveResponse)
async def bulk_move_documents(body: BulkMoveRequest) -> BulkMoveResponse:
    """The single move applied to a batch, with each affected case resynced once."""
    if not body.document_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No documents specified.")

    target_case = await asyncio.to_thread(case_service.get_case, body.case_id)
    if target_case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target case not found")

    documents = await to_thread_or_502(
        document_repository.get_documents_by_ids,
        body.document_ids,
        include_fields=False,
        detail="Failed to fetch the documents. Check Supabase table access.",
        log=("Failed to fetch documents %s", body.document_ids),
    )
    documents_by_id = {document.id: document for document in documents}

    eligible_ids: list[str] = []
    failed: list[BulkActionFailure] = []
    old_case_ids: set[str] = set()

    for document_id in body.document_ids:
        document = documents_by_id.get(document_id)
        if document is None:
            failed.append(BulkActionFailure(document_id=document_id, reason="Document not found"))
        elif document.status in _LOCKED_STATUSES:
            failed.append(
                BulkActionFailure(
                    document_id=document_id,
                    reason=f"An {document.status.value} document cannot be moved.",
                )
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
        await to_thread_or_502(
            document_repository.move_documents, eligible_ids, body.case_id,
            detail="Failed to move the documents. Check Supabase table access.",
            log=("Bulk move failed for documents %s", eligible_ids),
        )

        for case_id in {*old_case_ids, body.case_id}:
            await _resync_case(case_id)
        for document_id in eligible_ids:
            moved = documents_by_id[document_id]
            await _audit(types.DOCUMENT_MOVED, moved, from_case_id=moved.case_id, to_case_id=body.case_id)

    return BulkMoveResponse(moved=eligible_ids, failed=failed)


@router.post("/bulk-delete", response_model=BulkDeleteResponse)
async def bulk_delete_documents(body: BulkDeleteRequest) -> BulkDeleteResponse:
    """The single delete applied to a batch, with each affected case resynced once."""
    if not body.document_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No documents specified.")

    documents = await to_thread_or_502(
        document_repository.get_documents_by_ids,
        body.document_ids,
        include_fields=False,
        detail="Failed to fetch the documents. Check Supabase table access.",
        log=("Failed to fetch documents %s", body.document_ids),
    )
    documents_by_id = {document.id: document for document in documents}

    eligible: list[DocumentRecord] = []
    failed: list[BulkActionFailure] = []
    case_ids: set[str] = set()

    for document_id in body.document_ids:
        document = documents_by_id.get(document_id)
        if document is None:
            failed.append(BulkActionFailure(document_id=document_id, reason="Document not found"))
        elif document.status in _UNDELETABLE_STATUSES:
            failed.append(
                BulkActionFailure(
                    document_id=document_id, reason=f"An {document.status.value} document cannot be deleted.",
                )
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
        await to_thread_or_502(
            document_repository.remove_documents, eligible,
            detail="Failed to delete the documents. Check Supabase table access.",
            log=("Bulk delete failed for documents %s", [d.id for d in eligible]),
        )

        for case_id in case_ids:
            await _resync_case(case_id)
        for deleted in eligible:
            await _audit(types.DOCUMENT_DELETED, deleted, filename=deleted.filename)

    return BulkDeleteResponse(deleted=[d.id for d in eligible], failed=failed)


@router.patch("/{document_id}/extracted-fields", response_model=DocumentRecord)
async def update_extracted_fields(
    document_id: str,
    request: UpdateExtractedFieldsRequest,
    document: DocumentRecord = Depends(get_document_or_404),
) -> DocumentRecord:
    """The officer correcting extracted fields. Marks the document human-verified (extraction_confidence 1.0)."""
    _ensure_unlocked(document, "edited")
    # An optional field saved empty is dropped; a required one stays so it still blocks approval.
    drop_empty_optional_fields(document.document_type, request.extracted_fields)
    with bad_gateway_on_error(
        "Failed to save extracted fields. Check Supabase table access.",
        "Failed to update extracted fields for document %s",
        document_id,
    ):
        # An edited address can change whether the document now fulfils proof_of_address.
        fulfilled_requirements = compute_fulfilled_requirements(
            document.document_type, request.extracted_fields
        )
        # Original values and confidences, read before the fields are replaced.
        existing_fields = await asyncio.to_thread(
            extracted_fields_repository.get_extracted_fields,
            document_id,
        )

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

            old_value = _normalise_value(existing_field.field_value)

            # Fields the officer could edit.
            was_editable = (
                is_unrecognised_document
                or old_value == ""
                or (
                    existing_field.confidence is not None
                    and existing_field.confidence < settings.EXTRACTION_CONFIDENCE_THRESHOLD
                )
            )

            if was_editable:
                # Saved by the officer: human-verified.
                field_confidences[name] = 1.0
            else:
                # Not part of the review: keep its extraction confidence.
                field_confidences[name] = existing_field.confidence

        # Fields first: the document update fires a snapshot that must already see them.
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

        updated = await asyncio.to_thread(
            document_repository.update_extracted_fields,
            document_id,
            1.0,
            fulfilled_requirements,
            routing_status=RoutingStatus.ACCEPTED.value,
            fields_reviewed=is_unrecognised_document,
        )
        # Read back once: the rows just written have new ids the page needs.
        saved_fields = await asyncio.to_thread(
            extracted_fields_repository.get_extracted_fields, document_id
        )

    await _audit(
        types.DOCUMENT_FIELD_EDITED, document, action="saved", fields=sorted(request.extracted_fields),
        changes=_field_changes(
            {field.field_name: field.field_value for field in existing_fields}, request.extracted_fields
        ),
    )
    return _present(updated or document, saved_fields)


@router.post(
    "/{document_id}/extracted-fields",
    response_model=DocumentRecord,
)
async def add_extracted_field(
    document_id: str,
    request: AddExtractedFieldRequest,
    document: DocumentRecord = Depends(get_document_or_404),
) -> DocumentRecord:
    """Adds one manually entered field, stored at confidence 1.0."""
    _ensure_unlocked(document, "edited")
    field_name = normalise_field_name(request.field_name)

    if not field_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Field name cannot be empty.",
        )

    with bad_gateway_on_error(
        "Failed to add extracted field. Check Supabase table access.",
        "Failed to add extracted field to document %s",
        document_id,
    ):
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

        # Re-read the full set to recompute fulfilled_requirements.
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

        updated = await asyncio.to_thread(
            document_repository.update_extracted_fields,
            document_id,
            # Adding a field isn't the "Save fields" review, so the document's confidence is unchanged.
            document.extraction_confidence,
            fulfilled_requirements,
        )

    await _audit(
        types.DOCUMENT_FIELD_EDITED, document, action="added", fields=[field_name],
        changes=[{"field": field_name, "from": None, "to": _audit_value(request.field_value)}],
    )
    return _present(updated or document, all_fields)


@router.delete(
    "/{document_id}/extracted-fields/{field_id}",
    response_model=DocumentRecord,
)
async def delete_extracted_field(
    document_id: str,
    field_id: str,
    document: DocumentRecord = Depends(get_document_or_404),
) -> DocumentRecord:
    """Deletes one field of this document and recomputes fulfilled requirements."""
    _ensure_unlocked(document, "edited")
    with bad_gateway_on_error(
        "Failed to delete extracted field. Check Supabase table access.",
        "Failed to delete extracted field %s from document %s",
        field_id,
        document_id,
    ):
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

        # What was read above, less the deleted one.
        remaining_fields = [field for field in existing_fields if str(field.id) != field_id]

        extracted_fields = {
            field.field_name: field.field_value
            for field in remaining_fields
        }

        fulfilled_requirements = compute_fulfilled_requirements(
            document.document_type,
            extracted_fields,
        )

        updated = await asyncio.to_thread(
            document_repository.update_extracted_fields,
            document_id,
            # A deletion isn't a verification: confidence is carried through unchanged.
            document.extraction_confidence,
            fulfilled_requirements,
        )

    await _audit(
        types.DOCUMENT_FIELD_EDITED, document, action="deleted", fields=[field_to_delete.field_name],
        changes=[{"field": field_to_delete.field_name, "from": _audit_value(field_to_delete.field_value), "to": None}],
    )
    return _present(updated or document, remaining_fields)


@router.post("/{document_id}/approve", response_model=DocumentRecord)
async def approve_document(
    document_id: str,
    document: DocumentRecord = Depends(get_document_or_404),
) -> DocumentRecord:
    """Approves an analysed document after officer review, and starts detection."""
    if document.status != DocumentStatus.ANALYZED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only completed documents can be approved.",
        )

    if not is_classification_confirmed(document):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Cannot approve document. Confirm the document type first."
            ),
        )

    with bad_gateway_on_error("Failed to approve document.", "Failed to approve document %s", document_id):
        extracted_fields = await asyncio.to_thread(
            extracted_fields_repository.get_extracted_fields,
            document_id,
        )

        if not case_service.has_extracted_information(extracted_fields):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Cannot approve document. No information was extracted from it."
                ),
            )

        missing_required_fields = case_service.missing_required_fields(
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

        # Saving marks reviewed fields 1.0, so anything under the threshold is unreviewed.
        low_confidence_fields = sorted(
            field.field_name
            for field in extracted_fields
            if field.confidence is not None
            and field.confidence < settings.EXTRACTION_CONFIDENCE_THRESHOLD
        )

        if low_confidence_fields:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Cannot approve document. Low-confidence fields must be "
                    "reviewed and saved first: "
                    + ", ".join(low_confidence_fields)
                ),
            )

        # An Other document's fields come from no schema, so an officer must have saved them.
        if document.document_type == "unrecognised" and document.fields_reviewed_at is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Cannot approve document. Review and save the fields of "
                    "a document set to Other first."
                ),
            )

        approved = await asyncio.to_thread(
            document_repository.approve_document,
            document_id,
        )
        if approved is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

        skip = skips_detection(document)
        if skip:
            logger.info("Document %s approved with no type; detection skipped", document_id)
        # The event is what starts detection, so a failed publish fails the approval with a 502, and the approval
        # is undone: otherwise the document would sit approved with nothing ever scanning it.
        try:
            await events.publish(officer_event(
                types.DOCUMENT_APPROVED, document_id=document_id, case_id=document.case_id,
                detail={"document_type": document.document_type, "skip_detection": skip},
            ))
        except Exception:
            await asyncio.to_thread(document_repository.undo_approval, document_id, document.routing_status)
            raise

        return _present(approved, extracted_fields)


@router.post("/{document_id}/reject", response_model=DocumentRecord)
async def reject_document(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> DocumentRecord:
    if document.status != DocumentStatus.ANALYZED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only completed documents can be rejected.",
        )

    with bad_gateway_on_error("Failed to reject document.", "Failed to reject document %s", document_id):
        rejected = await asyncio.to_thread(
            document_repository.reject_document,
            document_id,
        )
        if rejected is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

        fields = await asyncio.to_thread(extracted_fields_repository.get_extracted_fields, document_id)
    await _audit(types.DOCUMENT_REJECTED, document, document_type=document.document_type)
    return _present(rejected, fields)
    

@router.patch("/{document_id}/document-type", response_model=DocumentRecord)
async def update_document_type(
    document_id: str,
    request: UpdateDocumentTypeRequest,
    document: DocumentRecord = Depends(get_document_with_fields_or_404),
) -> DocumentRecord:
    """
    The officer confirming or correcting a low-confidence type (enforced here, not just in the UI).
    Records it as confirmed (1.0, ACCEPTED). Only a changed type re-runs Extraction, on the doc-worker.
    """
    _ensure_unlocked(document, "re-typed")
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

    # No model read it, so there is no type to confirm, and a set type would tick a checklist item.
    if document.status == DocumentStatus.FAILED and content_guard.is_blocking_reason(document.failure_reason):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The content guard stopped this document; its type can't be set.",
        )

    # Same rule approval uses.
    if is_classification_confirmed(document):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Classification confidence is above the threshold; the type can't be overridden.",
        )

    # A first pass that went to manual review was extracted generically, so re-run even for the same type.
    first_pass_generic = (
        document.routing_status == RoutingStatus.MANUAL_REVIEW
        and document.document_type not in (None, "", "unrecognised")
    )
    needs_extraction = document_type != document.document_type or (
        first_pass_generic and document_type != "unrecognised"
    )

    with bad_gateway_on_error(
        "Failed to save the document type. Check Supabase table access.",
        "Failed to update document type for document %s",
        document_id,
    ):
        if needs_extraction:
            # The old fields came from the wrong schema: self-fulfilment only, and the old evidence is cleared.
            await asyncio.to_thread(
                document_repository.record_classification,
                document_id,
                document_type=document_type,
                classification_confidence=1.0,
                routing_status=RoutingStatus.ACCEPTED.value,
                evidence=[],
                fulfilled_requirements=compute_fulfilled_requirements(document_type, None),
            )
            if document.fields_reviewed_at is not None:
                await asyncio.to_thread(document_repository.clear_fields_review, document_id)
            await asyncio.to_thread(
                document_repository.update_status,
                document_id,
                DocumentStatus.EXTRACTING,
                processing_started_at=datetime.now(timezone.utc),
            )
        else:
            # Same type confirmed: the fields and evidence are still valid.
            await asyncio.to_thread(
                document_repository.record_classification,
                document_id,
                document_type=document_type,
                classification_confidence=1.0,
                routing_status=RoutingStatus.ACCEPTED.value,
                evidence=document.evidence,
                fulfilled_requirements=compute_fulfilled_requirements(document_type, document.extracted_fields),
            )

        detail = {"from_type": document.document_type, "to_type": document_type}
        if needs_extraction:
            # Starts the extraction rerun, so a failed publish is a 502 like any other failure here.
            await events.publish(officer_event(
                types.DOCUMENT_RETYPED, document_id=document_id, case_id=document.case_id, detail=detail,
            ))
    if not needs_extraction:
        await _audit(types.DOCUMENT_TYPE_CORRECTED, document, **detail)

    return await get_document(document_id)


@router.post("/{document_id}/retry", response_model=DocumentRecord)
async def retry_document(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> DocumentRecord:
    """
    Re-enqueues a FAILED document, or one stuck in PROCESSING past the threshold (the in-memory queue
    doesn't survive a restart). A merely queued or running one is refused: a second run would duplicate
    model calls and interleave writes. Cancel it first.
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

    with bad_gateway_on_error(
        "Failed to re-enqueue the document.",
        "Failed to re-enqueue document %s",
        document_id,
    ):
        # Only applies if the status hasn't moved on since the check above.
        updated = await asyncio.to_thread(
            document_repository.update_status,
            document_id,
            DocumentStatus.QUEUED,
            queued_at=datetime.now(timezone.utc),
            retry_count=document.retry_count + 1,
            expected_current_statuses=[document.status],
        )
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Document's state changed; refetch and try again.",
            )
        await events.publish(officer_event(
            types.DOCUMENT_RETRY_REQUESTED, document_id=document_id, case_id=document.case_id,
            detail={"retry_count": document.retry_count + 1, "from_status": document.status.value},
        ))

    refreshed = await asyncio.to_thread(document_repository.get_document, document_id)
    if refreshed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return refreshed


@router.post("/{document_id}/cancel", response_model=DocumentRecord)
async def cancel_document(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> DocumentRecord:
    """
    Stops a QUEUED, PROCESSING or EXTRACTING document. The status write is a compare-and-swap, so
    racing cancels or a finishing run can't both win; only then is the in-flight run interrupted.
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

    # The status is already CANCELLED, so the worker's own writes no longer match; this also stops its run.
    await _audit(types.DOCUMENT_CANCELLED, document, from_status=document.status.value)

    refreshed = await asyncio.to_thread(document_repository.get_document, document_id)
    if refreshed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return refreshed


@router.get("/{document_id}/langgraph-trace")
async def get_document_langgraph_trace(document_id: str) -> dict:
    """Dev-only: the pipeline trace for a document, from `langgraph_trace`, else this process's copy."""
    result = await asyncio.to_thread(langgraph_trace.load, document_id)
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
    """A short-lived signed URL for the file. inline=True lets the browser render it instead of downloading."""
    if document.storage_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document has no stored file"
        )

    signed_url = await to_thread_or_502(
        storage_service.create_signed_url,
        document.storage_path,
        settings.SIGNED_URL_EXPIRY_SECONDS,
        None if inline else document.filename,
        detail="Failed to generate download URL",
        log=("Failed to create signed URL for document %s", document_id),
    )

    return DocumentDownloadResponse(
        document_id=document.id,
        signed_url=signed_url,
        expires_in=settings.SIGNED_URL_EXPIRY_SECONDS,
    )


@router.get("/{document_id}/content-guard", response_model=ContentGuardResponse)
async def get_content_guard(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> ContentGuardResponse:
    """What the content guard found at ingest, for the officer to see on the document page."""
    result = await asyncio.to_thread(document_repository.get_guard_result, document_id)
    blocked = document.status == DocumentStatus.FAILED and content_guard.is_blocking_reason(document.failure_reason)
    if result is None:
        # From before the guard, not ingested yet, or no guard_result column.
        return ContentGuardResponse(checked=False, risk=None, blocked=blocked)
    return ContentGuardResponse(
        checked=bool(result.get("checks_run")),
        risk=result.get("risk"),
        blocked=blocked,
        error=result.get("error"),
        findings=[
            ContentGuardFinding(
                where=str(span.get("where") or "visible"),
                page=span.get("page"),
                field=span.get("field"),
                quote=str(span.get("quote") or ""),
                instruction=span.get("caught_by") == "dictionary",
                why_hidden=[str(reason) for reason in span.get("why_hidden") or []],
            )
            for span in result.get("spans") or []
        ],
    )
