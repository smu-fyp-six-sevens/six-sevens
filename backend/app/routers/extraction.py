from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from ..config import settings
from ..ingestion.ingestion_service import ingest_document
from ..models import ExtractionTestResponse

router = APIRouter(prefix="/extraction", tags=["extraction"])
logger = logging.getLogger(__name__)


@router.post("/test", response_model=ExtractionTestResponse)
async def test_extraction(file: UploadFile = File(...)) -> ExtractionTestResponse:
    """
    Dev/QA endpoint: runs just the raw-text extraction step (native PDF
    text, OCR fallback for scanned PDFs, or straight OCR for images) on an
    uploaded file and returns the result directly. Bypasses Supabase
    storage, the document queue, and Dify entirely — this exists so the
    extraction stage can be eyeballed on its own before it ever reaches
    classification/LLM extraction.
    """
    if file.content_type not in settings.ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported content type: {file.content_type}",
        )

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file must include a filename",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty"
        )
    if len(file_bytes) > settings.MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {settings.MAX_FILE_SIZE_BYTES} byte limit",
        )

    try:
        result = await asyncio.to_thread(
            ingest_document, file_bytes, file.filename, file.content_type
        )
    except Exception as exc:
        logger.exception("Extraction test failed for %s", file.filename)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Extraction failed: {exc}",
        ) from exc

    return ExtractionTestResponse(**result)
