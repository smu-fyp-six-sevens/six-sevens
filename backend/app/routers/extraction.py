from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from ..config import settings
from ..ingestion.ingestion_service import ingest_document

router = APIRouter(prefix="/extraction", tags=["extraction"])
logger = logging.getLogger(__name__)


class ExtractionTestPage(BaseModel):
    """
    One page's worth of extraction output. `elements` shape depends on
    extraction_method: native-PDF elements carry font/size/flags, OCR
    elements carry a per-line confidence score — kept as free-form dicts
    here rather than a union model since this endpoint is a debugging
    window onto whatever ingest_document actually returned.
    """

    page_number: int
    text: str
    elements: list[dict] = Field(default_factory=list)


class ExtractionTestResponse(BaseModel):
    """Mirrors the dict returned by ingestion.ingestion_service.ingest_document."""

    file_name: str
    file_type: str
    metadata: dict = Field(default_factory=dict)
    extraction_method: str
    raw_text: str
    pages: list[ExtractionTestPage]
    needs_review: bool
    status: str


async def _read_and_validate_upload(file: UploadFile) -> tuple[bytes, str, str]:
    """Returns (file_bytes, filename, content_type) — all validated non-empty,
    unlike UploadFile's own optional filename/content_type attributes."""
    content_type = file.content_type
    if content_type not in settings.ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported content type: {content_type}",
        )

    filename = file.filename
    if not filename:
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
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File exceeds {settings.MAX_FILE_SIZE_BYTES} byte limit",
        )

    return file_bytes, filename, content_type


@router.post("/test", response_model=ExtractionTestResponse)
async def test_extraction(file: UploadFile = File(...)) -> ExtractionTestResponse:
    """
    Dev/QA endpoint: runs just the raw-text extraction step (native PDF
    text, OCR fallback for scanned PDFs, or straight OCR for images) on an
    uploaded file and returns the result directly. Bypasses Supabase
    storage, the document queue, and the LangGraph pipeline entirely — this
    exists so the extraction stage can be eyeballed on its own before it
    ever reaches classification/LLM extraction.
    """
    file_bytes, filename, content_type = await _read_and_validate_upload(file)

    try:
        result = await asyncio.to_thread(
            ingest_document, file_bytes, filename, content_type
        )
    except Exception as exc:
        logger.exception("Extraction test failed for %s", filename)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Extraction failed: {exc}",
        ) from exc

    return ExtractionTestResponse(**result)

