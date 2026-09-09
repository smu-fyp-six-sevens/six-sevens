from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, status

from .. import document_repository, storage_service
from ..fraud_detection.font_consistency import FontConsistencyResult, check_font_consistency

router = APIRouter(prefix="/documents", tags=["detection"])
logger = logging.getLogger(__name__)


@router.get("/{document_id}/detection/font-consistency", response_model=FontConsistencyResult)
async def get_font_consistency(document_id: str) -> FontConsistencyResult:
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
    if document.storage_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document has no stored file"
        )

    try:
        file_bytes = await asyncio.to_thread(storage_service.download_file, document.storage_path)
    except Exception as exc:
        logger.exception("Failed to download document %s from storage", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the document from storage",
        ) from exc

    return await asyncio.to_thread(check_font_consistency, document_id, file_bytes)
