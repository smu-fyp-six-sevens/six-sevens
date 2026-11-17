"""The document routes. The rules live in document_service; this maps its refusals and failures to HTTP."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from .. import document_service, storage_service, text_lines
from ..config import settings
from ..cpu_pool import run_cpu
from ..langgraph.detection import tools as detection_tools
from ..langgraph import trace as langgraph_trace
from ..models import (
    LocatedValues,
    LocateValuesRequest,
    AddExtractedFieldRequest,
    BulkApproveRequest,
    BulkApproveResponse,
    BulkDeleteRequest,
    BulkDeleteResponse,
    BulkMoveRequest,
    BulkMoveResponse,
    DocumentRecord,
    DocumentStatus,
    MoveDocumentRequest,
    UpdateDocumentTypeRequest,
    UpdateExtractedFieldsRequest,
)
from ._dependencies import (
    bad_gateway_on_error,
    get_document_or_404,
    get_editable_document_or_404,
    get_editable_document_with_fields_or_404,
    to_thread_or_502,
)

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)

_REFUSAL_STATUS = {
    "conflict": status.HTTP_409_CONFLICT,
    "invalid": status.HTTP_400_BAD_REQUEST,
    "not_found": status.HTTP_404_NOT_FOUND,
    "unprocessable": status.HTTP_422_UNPROCESSABLE_CONTENT,
}


@contextmanager
def _handled(detail: str, *log: object) -> Iterator[None]:
    """A refusal becomes its status code; any other failure is logged and becomes a 502 with `detail`."""
    with bad_gateway_on_error(detail, *log):
        try:
            yield
        except document_service.Refused as refused:
            raise HTTPException(status_code=_REFUSAL_STATUS[refused.kind], detail=refused.detail) from None


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
async def list_documents(case_id: str, doc_status: DocumentStatus | None = None) -> list[DocumentRecord]:
    """A case's documents (required: every document in every case, with all their fields, is too large to send)."""
    return await to_thread_or_502(
        document_service.list_documents, case_id, doc_status,
        detail="Failed to list documents. Check Supabase table access.",
        log=("Failed to list documents",),
    )


@router.get("/{document_id}", response_model=DocumentRecord)
async def get_document(document_id: str) -> DocumentRecord:
    document = await to_thread_or_502(
        document_service.read, document_id,
        detail="Failed to fetch the document. Check Supabase table access.",
        log=("Failed to fetch document %s", document_id),
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: str, document: DocumentRecord = Depends(get_editable_document_or_404)) -> None:
    """Removes a document from its case. Only once it is out of flight; cancel an in-flight one first."""
    with _handled("Failed to delete the document. Check Supabase table access.", "Failed to delete document %s", document_id):
        await document_service.delete(document)


@router.post("/{document_id}/move", response_model=DocumentRecord)
async def move_document(
    document_id: str, body: MoveDocumentRequest, document: DocumentRecord = Depends(get_editable_document_or_404)
) -> DocumentRecord:
    """Reassigns a document to another case. Same eligibility as delete."""
    with _handled(
        "Failed to move the document. Check Supabase table access.",
        "Failed to move document %s to case %s", document_id, body.case_id,
    ):
        await document_service.move(document, body.case_id)
    return await get_document(document_id)


@router.post("/bulk-move", response_model=BulkMoveResponse)
async def bulk_move_documents(body: BulkMoveRequest) -> BulkMoveResponse:
    """The single move applied to a batch, with each affected case resynced once."""
    with _handled(
        "Failed to move the documents. Check Supabase table access.", "Bulk move failed for documents %s", body.document_ids
    ):
        return await document_service.bulk_move(body.document_ids, body.case_id)


@router.post("/bulk-delete", response_model=BulkDeleteResponse)
async def bulk_delete_documents(body: BulkDeleteRequest) -> BulkDeleteResponse:
    """The single delete applied to a batch, with each affected case resynced once."""
    with _handled(
        "Failed to delete the documents. Check Supabase table access.", "Bulk delete failed for documents %s", body.document_ids
    ):
        return await document_service.bulk_delete(body.document_ids)


@router.patch("/{document_id}/extracted-fields", response_model=DocumentRecord)
async def update_extracted_fields(
    document_id: str, request: UpdateExtractedFieldsRequest, document: DocumentRecord = Depends(get_editable_document_or_404)
) -> DocumentRecord:
    """The officer correcting extracted fields. Marks the document human-verified (extraction_confidence 1.0)."""
    with _handled(
        "Failed to save extracted fields. Check Supabase table access.",
        "Failed to update extracted fields for document %s", document_id,
    ):
        return await document_service.save_fields(document, request.extracted_fields)


@router.post("/{document_id}/extracted-fields", response_model=DocumentRecord)
async def add_extracted_field(
    document_id: str, request: AddExtractedFieldRequest, document: DocumentRecord = Depends(get_editable_document_or_404)
) -> DocumentRecord:
    """Adds one manually entered field, stored at confidence 1.0."""
    with _handled(
        "Failed to add extracted field. Check Supabase table access.",
        "Failed to add extracted field to document %s", document_id,
    ):
        return await document_service.add_field(document, request.field_name, request.field_value)


@router.delete("/{document_id}/extracted-fields/{field_id}", response_model=DocumentRecord)
async def delete_extracted_field(
    document_id: str, field_id: str, document: DocumentRecord = Depends(get_editable_document_or_404)
) -> DocumentRecord:
    """Deletes one field of this document and recomputes fulfilled requirements."""
    with _handled(
        "Failed to delete extracted field. Check Supabase table access.",
        "Failed to delete extracted field %s from document %s", field_id, document_id,
    ):
        return await document_service.delete_field(document, field_id)


@router.post("/{document_id}/approve", response_model=DocumentRecord)
async def approve_document(document_id: str, document: DocumentRecord = Depends(get_editable_document_or_404)) -> DocumentRecord:
    """Approves an analysed document after officer review, and starts detection."""
    with _handled("Failed to approve document.", "Failed to approve document %s", document_id):
        return await document_service.approve(document)


@router.post("/bulk-approve", response_model=BulkApproveResponse)
async def bulk_approve_documents(body: BulkApproveRequest) -> BulkApproveResponse:
    """Approves several analysed documents at once, each by the same rules as one; refusals are listed per document."""
    with _handled(
        "Failed to approve the documents. Check Supabase table access.",
        "Bulk approve failed for documents %s", body.document_ids,
    ):
        return await document_service.bulk_approve(body.document_ids)


@router.post("/{document_id}/reject", response_model=DocumentRecord)
async def reject_document(document_id: str, document: DocumentRecord = Depends(get_editable_document_or_404)) -> DocumentRecord:
    with _handled("Failed to reject document.", "Failed to reject document %s", document_id):
        return await document_service.reject(document)


@router.patch("/{document_id}/document-type", response_model=DocumentRecord)
async def update_document_type(
    document_id: str,
    request: UpdateDocumentTypeRequest,
    document: DocumentRecord = Depends(get_editable_document_with_fields_or_404),
) -> DocumentRecord:
    """
    The officer confirming or correcting a low-confidence type (enforced here, not just in the UI).
    Records it as confirmed (1.0, ACCEPTED). Only a changed type re-runs Extraction, on the doc-worker.
    """
    with _handled(
        "Failed to save the document type. Check Supabase table access.",
        "Failed to update document type for document %s", document_id,
    ):
        await document_service.set_type(document, request.document_type)
    return await get_document(document_id)


@router.post("/{document_id}/retry", response_model=DocumentRecord)
async def retry_document(document_id: str, document: DocumentRecord = Depends(get_editable_document_or_404)) -> DocumentRecord:
    """
    Re-enqueues a FAILED document, or one stuck in PROCESSING past the threshold (its run died with the
    instance). A merely queued or running one is refused: a second run would duplicate
    model calls and interleave writes. Cancel it first.
    """
    with _handled("Failed to re-enqueue the document.", "Failed to re-enqueue document %s", document_id):
        return await document_service.retry(document)


@router.post("/{document_id}/process-anyway", response_model=DocumentRecord)
async def process_document_anyway(
    document_id: str, document: DocumentRecord = Depends(get_editable_document_or_404)
) -> DocumentRecord:
    """
    Lets a document the content guard stopped go through: the officer has read what the guard found and decided
    to continue. Logged in the audit trail; the document is re-queued, and the guard's warning stays on the page.
    """
    with _handled("Failed to process the document.", "Failed to process document %s anyway", document_id):
        return await document_service.process_anyway(document)


@router.post("/{document_id}/cancel", response_model=DocumentRecord)
async def cancel_document(document_id: str, document: DocumentRecord = Depends(get_editable_document_or_404)) -> DocumentRecord:
    """
    Stops a QUEUED, PROCESSING or EXTRACTING document. The status write is a compare-and-swap, so
    racing cancels or a finishing run can't both win; only then is the in-flight run interrupted.
    """
    with _handled("Failed to cancel the document.", "Failed to cancel document %s", document_id):
        return await document_service.cancel(document)


@router.get("/{document_id}/langgraph-trace")
async def get_document_langgraph_trace(document_id: str) -> dict:
    """The pipeline trace for a document (the review screen's field reasons), from `langgraph_trace`, else this process's copy."""
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

    signed_url, expires_in = await to_thread_or_502(
        storage_service.signed_url,
        document.storage_path,
        settings.SIGNED_URL_EXPIRY_SECONDS,
        None if inline else document.filename,
        detail="Failed to generate download URL",
        log=("Failed to create signed URL for document %s", document_id),
    )

    return DocumentDownloadResponse(document_id=document.id, signed_url=signed_url, expires_in=expires_in)


@router.post("/{document_id}/locate-values", response_model=LocatedValues)
async def locate_document_values(
    document_id: str, request: LocateValuesRequest, document: DocumentRecord = Depends(get_document_or_404)
) -> LocatedValues:
    """Where each value is printed on the document, found the way numerical and date findings are placed (in the
    forms it is likely printed in, beside its field's label), for boxing it in the officer's compare view. A photo
    or a scan is searched through its OCR'd lines (text_lines, worked out once and cached). Reads only."""
    if document.storage_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document has no stored file")
    values = [value.model_dump() for value in request.values]
    with bad_gateway_on_error("Failed to find the values on the document.", "Failed to locate values on document %s", document_id):
        file_bytes = await asyncio.to_thread(storage_service.download_file, document.storage_path)
        text_pages = None
        if detection_tools.needs_text_lines(file_bytes):
            text_pages = (await asyncio.to_thread(text_lines.get_or_build, document))["pages"]
        located = await run_cpu(detection_tools.locate_values, file_bytes, values, text_pages)
    return LocatedValues.model_validate({"values": located})


@router.get("/{document_id}/content-guard", response_model=ContentGuardResponse)
async def get_content_guard(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> ContentGuardResponse:
    """What the content guard found at ingest, for the officer to see on the document page."""
    result = await asyncio.to_thread(document_service.guard_result, document_id)
    blocked = document_service.is_guard_blocked(document)
    if result is None:
        # Not ingested yet, or saving the guard's result at ingest failed.
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
