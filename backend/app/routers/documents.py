from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, status

from .. import document_repository, extracted_fields_repository, storage_service
from ..config import settings
from ..extraction_prompts import EXTRACTION_SCHEMAS
from ..models import (
    DocumentDownloadResponse,
    DocumentRecord,
    DocumentStatus,
    UpdateExtractedFieldsRequest,
)
from ..queue_service import document_queue

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
    return document


<<<<<<< Updated upstream
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
=======
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
>>>>>>> Stashed changes
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

<<<<<<< Updated upstream
    try:
        await asyncio.to_thread(
            document_repository.update_extracted_fields,
            document_id,
            request.extracted_fields,
            1.0,
        )
        await asyncio.to_thread(
            extracted_fields_repository.replace_extracted_fields,
            document_id,
            request.extracted_fields,
            confidence=1.0,
            required_field_names=set(EXTRACTION_SCHEMAS.get(document.document_type or "", [])),
        )
    except Exception as exc:
        logger.exception("Failed to update extracted fields for document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to save extracted fields. Check Supabase table access.",
        ) from exc

    updated = await asyncio.to_thread(document_repository.get_document, document_id)
    return updated
=======
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
>>>>>>> Stashed changes


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
