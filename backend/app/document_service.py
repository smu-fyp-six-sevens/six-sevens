"""
What an officer can do to a document, and the rules for each: edit, add or delete fields, set the type, approve,
reject, move, delete, retry, cancel. routers/documents.py only translates HTTP: it maps `Refused` to a status code
and storage failures to a 502.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Literal

from . import case_service, case_stage, document_repository, events, extracted_fields_repository, monitoring
from .config import settings
from .events import officer_event, types
from .extracted_fields_repository import normalise_field_name
from .guardrails import content_guard
from .langgraph.extraction_prompts import KNOWN_DOCUMENT_TYPES, drop_empty_optional_fields, schema_field_names
from .models import (
    APPROVED_STATUSES,
    IN_FLIGHT_STATUSES,
    BulkActionFailure,
    BulkDeleteResponse,
    BulkMoveResponse,
    CaseStage,
    DocumentRecord,
    DocumentStatus,
    RoutingStatus,
    compute_fulfilled_requirements,
    is_classification_confirmed,
    skips_detection,
)
from .snapshots import push_case_snapshot

logger = logging.getLogger("classifier_pipeline.document_service")

# "unrecognised" is the "Other" catch-all.
_SETTABLE_DOCUMENT_TYPES = {*KNOWN_DOCUMENT_TYPES, "unrecognised"}
# A pipeline run is in flight for these; re-typing would race it.
_TYPE_OVERRIDE_BLOCKED_STATUSES = IN_FLIGHT_STATUSES
# The officer's decision is final: approved (and later) and rejected documents are locked.
_LOCKED_STATUSES = APPROVED_STATUSES | {DocumentStatus.REJECTED}
_UNDELETABLE_STATUSES = APPROVED_STATUSES
# A field value kept in an audit entry is cut to this many characters.
_AUDIT_VALUE_CHARS = 500


class Refused(Exception):
    """The document's state doesn't allow the action. `detail` is written for the officer."""

    def __init__(self, kind: Literal["conflict", "invalid", "not_found", "unprocessable"], detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def _decided(document: DocumentRecord) -> str:
    """Why a locked document can't change, as an officer would put it: it was approved (scanning or not) or rejected."""
    return "rejected" if document.status == DocumentStatus.REJECTED else "approved"


def _ensure_unlocked(document: DocumentRecord, consequence: str) -> None:
    if document.status in _LOCKED_STATUSES:
        raise Refused("conflict", f"This document has been {_decided(document)}, so {consequence}.")


def present(document: DocumentRecord, fields: list) -> DocumentRecord:
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


# --- Reads (sync: run on a worker thread) ---------------------------------------------------------------------


def list_documents(case_id: str | None, doc_status: DocumentStatus | None) -> list[DocumentRecord]:
    return document_repository.list_documents(case_id, doc_status)


def read(document_id: str) -> DocumentRecord | None:
    """The document with its fields, as the page shows it."""
    document = document_repository.get_document(document_id, include_fields=False)
    if document is None:
        return None
    return present(document, extracted_fields_repository.get_extracted_fields(document_id))


def guard_result(document_id: str) -> dict | None:
    return document_repository.get_guard_result(document_id)


def is_guard_blocked(document: DocumentRecord) -> bool:
    return document.status == DocumentStatus.FAILED and content_guard.is_blocking_reason(document.failure_reason)


# --- The audit trail's record of a field edit -------------------------------------------------------------------


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


def field_changes(before: dict[str, object], after: dict[str, object]) -> list[dict[str, object]]:
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


# --- Moving and deleting ----------------------------------------------------------------------------------------


async def _resync_case(case_id: str) -> None:
    """Updates the stage and pushes a snapshot, for any change to a case's document set. Never raises."""
    try:
        await asyncio.to_thread(case_stage.sync_case_stage, case_id)
    except Exception:
        logger.exception("Failed to update the stage of case %s", case_id)
    await push_case_snapshot(case_id)


async def _finalised_cases(case_ids: set[str]) -> set[str]:
    """Which of these cases are finalised, and so take no changes: one read per case."""
    finalised: set[str] = set()
    for case_id in case_ids:
        if await asyncio.to_thread(case_service.is_case_finalised, case_id):
            finalised.add(case_id)
    return finalised


async def delete(document: DocumentRecord) -> None:
    """Removes a document from its case. Only once it is out of flight; cancel an in-flight one first."""
    if document.status in _UNDELETABLE_STATUSES:
        raise Refused("conflict", f"This document has been {_decided(document)}, so it can't be deleted.")
    if document.status in IN_FLIGHT_STATUSES:
        raise Refused(
            "conflict",
            "Document is still being processed; cancel it first, or wait for it to finish, before deleting.",
        )
    await asyncio.to_thread(document_repository.remove_document, document)
    await _audit(types.DOCUMENT_DELETED, document, filename=document.filename)


async def move(document: DocumentRecord, case_id: str) -> None:
    """Reassigns a document to another case. Same eligibility as delete."""
    if document.status in _LOCKED_STATUSES:
        raise Refused(
            "conflict", f"This document has been {_decided(document)}, so it can't be moved to another case."
        )
    if document.status in IN_FLIGHT_STATUSES:
        raise Refused(
            "conflict",
            "Document is still being processed; cancel it first, or wait for it to finish, before moving it.",
        )
    if document.case_id == case_id:
        raise Refused("invalid", "Document is already in that case.")
    target_case = await asyncio.to_thread(case_service.get_case, case_id)
    if target_case is None:
        raise Refused("not_found", "Target case not found")
    if target_case.status == CaseStage.FINALISED.value:
        raise Refused("conflict", case_service.FINALISED_DETAIL)

    old_case_id = document.case_id
    await asyncio.to_thread(document_repository.move_document, document.id, case_id)
    # The repository resyncs the new case; the old one never sees an event carrying it.
    if old_case_id is not None:
        await _resync_case(old_case_id)
    await _audit(types.DOCUMENT_MOVED, document, from_case_id=old_case_id, to_case_id=case_id)


async def _documents_by_id(document_ids: list[str]) -> dict[str, DocumentRecord]:
    if not document_ids:
        raise Refused("invalid", "No documents specified.")
    documents = await asyncio.to_thread(document_repository.get_documents_by_ids, document_ids, include_fields=False)
    return {document.id: document for document in documents}


async def bulk_move(document_ids: list[str], case_id: str) -> BulkMoveResponse:
    """The single move applied to a batch, with each affected case resynced once."""
    if not document_ids:
        raise Refused("invalid", "No documents specified.")
    target_case = await asyncio.to_thread(case_service.get_case, case_id)
    if target_case is None:
        raise Refused("not_found", "Target case not found")
    if target_case.status == CaseStage.FINALISED.value:
        raise Refused("conflict", case_service.FINALISED_DETAIL)
    documents_by_id = await _documents_by_id(document_ids)
    finalised = await _finalised_cases({d.case_id for d in documents_by_id.values() if d.case_id is not None})

    eligible_ids: list[str] = []
    failed: list[BulkActionFailure] = []
    old_case_ids: set[str] = set()
    for document_id in document_ids:
        document = documents_by_id.get(document_id)
        if document is None:
            failed.append(BulkActionFailure(document_id=document_id, reason="Document not found"))
        elif document.case_id in finalised:
            failed.append(BulkActionFailure(document_id=document_id, reason=case_service.FINALISED_DETAIL))
        elif document.status in _LOCKED_STATUSES:
            reason = f"This document has been {_decided(document)}, so it can't be moved to another case."
            failed.append(BulkActionFailure(document_id=document_id, reason=reason))
        elif document.status in IN_FLIGHT_STATUSES:
            failed.append(BulkActionFailure(document_id=document_id, reason="Document is still being processed."))
        elif document.case_id == case_id:
            failed.append(BulkActionFailure(document_id=document_id, reason="Document is already in that case."))
        else:
            eligible_ids.append(document_id)
            if document.case_id is not None:
                old_case_ids.add(document.case_id)

    if eligible_ids:
        await asyncio.to_thread(document_repository.move_documents, eligible_ids, case_id)
        for affected in {*old_case_ids, case_id}:
            await _resync_case(affected)
        for document_id in eligible_ids:
            moved = documents_by_id[document_id]
            await _audit(types.DOCUMENT_MOVED, moved, from_case_id=moved.case_id, to_case_id=case_id)
    return BulkMoveResponse(moved=eligible_ids, failed=failed)


async def bulk_delete(document_ids: list[str]) -> BulkDeleteResponse:
    """The single delete applied to a batch, with each affected case resynced once."""
    documents_by_id = await _documents_by_id(document_ids)
    finalised = await _finalised_cases({d.case_id for d in documents_by_id.values() if d.case_id is not None})

    eligible: list[DocumentRecord] = []
    failed: list[BulkActionFailure] = []
    case_ids: set[str] = set()
    for document_id in document_ids:
        document = documents_by_id.get(document_id)
        if document is None:
            failed.append(BulkActionFailure(document_id=document_id, reason="Document not found"))
        elif document.case_id in finalised:
            failed.append(BulkActionFailure(document_id=document_id, reason=case_service.FINALISED_DETAIL))
        elif document.status in _UNDELETABLE_STATUSES:
            reason = f"This document has been {_decided(document)}, so it can't be deleted."
            failed.append(BulkActionFailure(document_id=document_id, reason=reason))
        elif document.status in IN_FLIGHT_STATUSES:
            failed.append(BulkActionFailure(document_id=document_id, reason="Document is still being processed."))
        else:
            eligible.append(document)
            if document.case_id is not None:
                case_ids.add(document.case_id)

    if eligible:
        await asyncio.to_thread(document_repository.remove_documents, eligible)
        for case_id in case_ids:
            await _resync_case(case_id)
        for deleted in eligible:
            await _audit(types.DOCUMENT_DELETED, deleted, filename=deleted.filename)
    return BulkDeleteResponse(deleted=[d.id for d in eligible], failed=failed)


# --- Fields -----------------------------------------------------------------------------------------------------


async def save_fields(document: DocumentRecord, extracted_fields: dict) -> DocumentRecord:
    """The officer correcting extracted fields. Marks the document human-verified (extraction_confidence 1.0)."""
    _ensure_unlocked(document, "its fields can't be edited")
    # An optional field saved empty is dropped; a required one stays so it still blocks approval.
    drop_empty_optional_fields(document.document_type, extracted_fields)
    # An edited address can change whether the document now fulfils proof_of_address.
    fulfilled_requirements = compute_fulfilled_requirements(document.document_type, extracted_fields)
    # Original values and confidences, read before the fields are replaced.
    existing_fields = await asyncio.to_thread(extracted_fields_repository.get_extracted_fields, document.id)
    existing_by_name = {field.field_name: field for field in existing_fields}
    is_unrecognised_document = document.document_type == "unrecognised"

    field_confidences = {}
    for name in extracted_fields:
        existing_field = existing_by_name.get(name)
        if existing_field is None:
            # New field added during manual review.
            field_confidences[name] = 1.0
            continue
        # Fields the officer could edit.
        was_editable = (
            is_unrecognised_document
            or _normalise_value(existing_field.field_value) == ""
            or (
                existing_field.confidence is not None
                and existing_field.confidence < settings.EXTRACTION_CONFIDENCE_THRESHOLD
            )
        )
        # Saved by the officer: human-verified. Not part of the review: keep its extraction confidence.
        field_confidences[name] = 1.0 if was_editable else existing_field.confidence

    # Fields first: the document update fires a snapshot that must already see them.
    await asyncio.to_thread(
        extracted_fields_repository.replace_extracted_fields,
        document.id,
        extracted_fields,
        field_confidences=field_confidences,
        default_confidence=None,
        required_field_names=set(schema_field_names(document.document_type or "")),
    )
    updated = await asyncio.to_thread(
        document_repository.update_extracted_fields,
        document.id,
        1.0,
        fulfilled_requirements,
        routing_status=RoutingStatus.ACCEPTED.value,
        fields_reviewed=is_unrecognised_document,
    )
    # Read back once: the rows just written have new ids the page needs.
    saved_fields = await asyncio.to_thread(extracted_fields_repository.get_extracted_fields, document.id)

    await _audit(
        types.DOCUMENT_FIELD_EDITED, document, action="saved", fields=sorted(extracted_fields),
        changes=field_changes({field.field_name: field.field_value for field in existing_fields}, extracted_fields),
    )
    return present(updated or document, saved_fields)


async def add_field(document: DocumentRecord, name: str, value: object) -> DocumentRecord:
    """Adds one manually entered field, stored at confidence 1.0."""
    _ensure_unlocked(document, "its fields can't be edited")
    field_name = normalise_field_name(name)
    if not field_name:
        raise Refused("invalid", "Field name cannot be empty.")

    existing_fields = await asyncio.to_thread(extracted_fields_repository.get_extracted_fields, document.id)
    if any(field.field_name == field_name for field in existing_fields):
        raise Refused("conflict", "A field with this name already exists.")

    await asyncio.to_thread(
        extracted_fields_repository.add_extracted_field,
        document.id,
        field_name,
        value,
        required_field_names=set(schema_field_names(document.document_type or "")),
    )
    # Re-read the full set to recompute fulfilled_requirements.
    all_fields = await asyncio.to_thread(extracted_fields_repository.get_extracted_fields, document.id)
    fulfilled_requirements = compute_fulfilled_requirements(
        document.document_type, {field.field_name: field.field_value for field in all_fields}
    )
    updated = await asyncio.to_thread(
        document_repository.update_extracted_fields,
        document.id,
        # Adding a field isn't the "Save fields" review, so the document's confidence is unchanged.
        document.extraction_confidence,
        fulfilled_requirements,
    )

    await _audit(
        types.DOCUMENT_FIELD_EDITED, document, action="added", fields=[field_name],
        changes=[{"field": field_name, "from": None, "to": _audit_value(value)}],
    )
    return present(updated or document, all_fields)


async def delete_field(document: DocumentRecord, field_id: str) -> DocumentRecord:
    """Deletes one field of this document and recomputes fulfilled requirements."""
    _ensure_unlocked(document, "its fields can't be edited")
    existing_fields = await asyncio.to_thread(extracted_fields_repository.get_extracted_fields, document.id)
    field_to_delete = next((field for field in existing_fields if str(field.id) == field_id), None)
    if field_to_delete is None:
        raise Refused("not_found", "Extracted field not found")

    await asyncio.to_thread(extracted_fields_repository.delete_extracted_field, document.id, field_id)
    # What was read above, less the deleted one.
    remaining_fields = [field for field in existing_fields if str(field.id) != field_id]
    fulfilled_requirements = compute_fulfilled_requirements(
        document.document_type, {field.field_name: field.field_value for field in remaining_fields}
    )
    updated = await asyncio.to_thread(
        document_repository.update_extracted_fields,
        document.id,
        # A deletion isn't a verification: confidence is carried through unchanged.
        document.extraction_confidence,
        fulfilled_requirements,
    )

    await _audit(
        types.DOCUMENT_FIELD_EDITED, document, action="deleted", fields=[field_to_delete.field_name],
        changes=[{"field": field_to_delete.field_name, "from": _audit_value(field_to_delete.field_value), "to": None}],
    )
    return present(updated or document, remaining_fields)


# --- The officer's decision -------------------------------------------------------------------------------------


async def approve(document: DocumentRecord) -> DocumentRecord:
    """Approves an analysed document after officer review, and starts detection."""
    if document.status != DocumentStatus.ANALYZED:
        raise Refused("invalid", "Only completed documents can be approved.")
    if not is_classification_confirmed(document):
        raise Refused("invalid", "Cannot approve document. Confirm the document type first.")

    extracted_fields = await asyncio.to_thread(extracted_fields_repository.get_extracted_fields, document.id)
    if not case_service.has_extracted_information(extracted_fields):
        raise Refused("invalid", "Cannot approve document. No information was extracted from it.")
    missing = case_service.missing_required_fields(document.document_type, extracted_fields)
    if missing:
        raise Refused("invalid", "Cannot approve document. Required fields are missing or empty: " + ", ".join(missing))
    # Saving marks reviewed fields 1.0, so anything under the threshold is unreviewed.
    low_confidence = sorted(
        field.field_name
        for field in extracted_fields
        if field.confidence is not None and field.confidence < settings.EXTRACTION_CONFIDENCE_THRESHOLD
    )
    if low_confidence:
        raise Refused(
            "invalid",
            "Cannot approve document. Low-confidence fields must be reviewed and saved first: "
            + ", ".join(low_confidence),
        )
    # An Other document's fields come from no schema, so an officer must have saved them.
    if document.document_type == "unrecognised" and document.fields_reviewed_at is None:
        raise Refused(
            "invalid", "Cannot approve document. Review and save the fields of a document set to Other first."
        )

    approved = await asyncio.to_thread(document_repository.approve_document, document.id)
    if approved is None:
        raise Refused("not_found", "Document not found")

    skip = skips_detection(document)
    if skip:
        logger.info("Document %s approved with no type; detection skipped", document.id)
    # The event is what starts detection, so a failed publish fails the approval with a 502, and the approval
    # is undone: otherwise the document would sit approved with nothing ever scanning it.
    try:
        await events.publish(officer_event(
            types.DOCUMENT_APPROVED, document_id=document.id, case_id=document.case_id,
            detail={"document_type": document.document_type, "skip_detection": skip},
        ))
    except Exception:
        await asyncio.to_thread(document_repository.undo_approval, document.id, document.routing_status)
        raise
    return present(approved, extracted_fields)


async def reject(document: DocumentRecord) -> DocumentRecord:
    # Any document the pipeline has finished with can be rejected, including a failed one and one the content guard
    # blocked. One still in flight would be overwritten by the run when it lands, so it is cancelled first.
    _ensure_unlocked(document, "it can't be rejected")
    if document.status in IN_FLIGHT_STATUSES:
        raise Refused("conflict", "This document is still being processed. Cancel it first, then reject it.")

    rejected = await asyncio.to_thread(document_repository.reject_document, document.id)
    if rejected is None:
        raise Refused("not_found", "Document not found")
    fields = await asyncio.to_thread(extracted_fields_repository.get_extracted_fields, document.id)
    await _audit(types.DOCUMENT_REJECTED, document, document_type=document.document_type)
    return present(rejected, fields)


async def set_type(document: DocumentRecord, requested_type: str) -> None:
    """
    The officer confirming or correcting a low-confidence type. `document` carries its plain fields. Records the
    type as confirmed (1.0, ACCEPTED). Only a changed type re-runs Extraction, on the doc-worker.
    """
    _ensure_unlocked(document, "its type can't be changed")
    document_type = requested_type.strip().lower()
    if document_type not in _SETTABLE_DOCUMENT_TYPES:
        raise Refused("unprocessable", f"Unknown document_type: {requested_type}")
    if document.status in _TYPE_OVERRIDE_BLOCKED_STATUSES:
        raise Refused("conflict", "Document is still being processed; try again once it finishes.")
    # No model read it, so there is no type to confirm, and a set type would tick a checklist item.
    if is_guard_blocked(document):
        raise Refused("conflict", "The content guard stopped this document; its type can't be set.")
    # Same rule approval uses.
    if is_classification_confirmed(document):
        raise Refused("invalid", "Classification confidence is above the threshold; the type can't be overridden.")

    # A first pass that went to manual review was extracted generically, so re-run even for the same type.
    first_pass_generic = (
        document.routing_status == RoutingStatus.MANUAL_REVIEW
        and document.document_type not in (None, "", "unrecognised")
    )
    needs_extraction = document_type != document.document_type or (
        first_pass_generic and document_type != "unrecognised"
    )

    if needs_extraction:
        # The old fields came from the wrong schema: self-fulfilment only, and the old evidence is cleared.
        await asyncio.to_thread(
            document_repository.record_classification,
            document.id,
            document_type=document_type,
            classification_confidence=1.0,
            routing_status=RoutingStatus.ACCEPTED.value,
            evidence=[],
            fulfilled_requirements=compute_fulfilled_requirements(document_type, None),
        )
        if document.fields_reviewed_at is not None:
            await asyncio.to_thread(document_repository.clear_fields_review, document.id)
        await asyncio.to_thread(
            document_repository.update_status,
            document.id,
            DocumentStatus.EXTRACTING,
            processing_started_at=datetime.now(timezone.utc),
        )
    else:
        # Same type confirmed: the fields and evidence are still valid.
        await asyncio.to_thread(
            document_repository.record_classification,
            document.id,
            document_type=document_type,
            classification_confidence=1.0,
            routing_status=RoutingStatus.ACCEPTED.value,
            evidence=document.evidence,
            fulfilled_requirements=compute_fulfilled_requirements(document_type, document.extracted_fields),
        )

    detail = {"from_type": document.document_type, "to_type": document_type}
    if needs_extraction:
        # Starts the extraction rerun, so a failed publish fails the request like any other failure here.
        await events.publish(officer_event(
            types.DOCUMENT_RETYPED, document_id=document.id, case_id=document.case_id, detail=detail,
        ))
    else:
        await _audit(types.DOCUMENT_TYPE_CORRECTED, document, **detail)


# --- Runs -------------------------------------------------------------------------------------------------------


async def _reread(document_id: str) -> DocumentRecord:
    refreshed = await asyncio.to_thread(document_repository.get_document, document_id)
    if refreshed is None:
        raise Refused("not_found", "Document not found")
    return refreshed


async def retry(document: DocumentRecord) -> DocumentRecord:
    """
    Re-enqueues a FAILED document, or one stuck in PROCESSING past the threshold (the in-memory queue
    doesn't survive a restart). A merely queued or running one is refused: a second run would duplicate
    model calls and interleave writes. Cancel it first.
    """
    if document.status != DocumentStatus.FAILED and not monitoring.is_stuck_processing_for_retry(document):
        raise Refused(
            "conflict",
            "Document can only be retried when it has failed, or has been "
            "processing for an unusually long time (possibly orphaned).",
        )
    # Only applies if the status hasn't moved on since the check above.
    updated = await asyncio.to_thread(
        document_repository.update_status,
        document.id,
        DocumentStatus.QUEUED,
        queued_at=datetime.now(timezone.utc),
        retry_count=document.retry_count + 1,
        expected_current_statuses=[document.status],
    )
    if not updated:
        raise Refused("conflict", "This document changed while you were working on it. Refresh the page and try again.")
    await events.publish(officer_event(
        types.DOCUMENT_RETRY_REQUESTED, document_id=document.id, case_id=document.case_id,
        detail={"retry_count": document.retry_count + 1, "from_status": document.status.value},
    ))
    return await _reread(document.id)


async def cancel(document: DocumentRecord) -> DocumentRecord:
    """
    Stops a QUEUED, PROCESSING or EXTRACTING document. The status write is a compare-and-swap, so
    racing cancels or a finishing run can't both win; only then is the in-flight run interrupted.
    """
    updated = await asyncio.to_thread(
        document_repository.update_status,
        document.id,
        DocumentStatus.CANCELLED,
        expected_current_statuses=[DocumentStatus.QUEUED, DocumentStatus.PROCESSING, DocumentStatus.EXTRACTING],
    )
    if not updated:
        raise Refused("conflict", "Document can only be cancelled while queued, processing, or extracting.")
    # The status is already CANCELLED, so the worker's own writes no longer match; this also stops its run.
    await _audit(types.DOCUMENT_CANCELLED, document, from_status=document.status.value)
    return await _reread(document.id)
