from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, status

from .. import classification_cache, document_repository, extracted_fields_repository, storage_service
from ..config import settings
from ..extraction_prompts import EXTRACTION_SCHEMAS
from ..models import (
    ClassificationPreviewResponse,
    DocumentDownloadResponse,
    DocumentRecord,
    DocumentStatus,
    UpdateExtractedFieldsRequest,
)

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


@router.get("/{document_id}/classification-preview", response_model=ClassificationPreviewResponse)
async def get_classification_preview(document_id: str) -> ClassificationPreviewResponse:
    """
    Evidence from the Classifier's raw output — held in an in-memory cache
    (see classification_cache.py), not persisted to Supabase. Confidence
    lives on DocumentRecord.confidence now (a real column) rather than
    here. Returns empty until classification has actually run for this
    document, rather than 404ing, since "not classified yet" is an
    expected transient state while the frontend is polling.
    """
    evidence = classification_cache.get_evidence(document_id)
    return ClassificationPreviewResponse(evidence=evidence or [])


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
