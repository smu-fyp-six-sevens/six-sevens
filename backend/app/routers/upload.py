from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from ..config import settings
from ..models import UploadResponse
from ..queue_service import document_queue
from ..supabase_client import supabase_client

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(case_id: str | None = Form(None), file: UploadFile = File(...)) -> UploadResponse:
    """
    Validate -> store -> queue -> (async) hand off to the Classifier Agent.

    Returns as soon as the document is queued (AC1) — it does not wait for
    classification to complete.
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

    filename = file.filename

    file_bytes = await file.read()
    if len(file_bytes) > settings.MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {settings.MAX_FILE_SIZE_BYTES} byte limit",
        )

    try:
        storage_path = supabase_client.upload_file(
            case_id=case_id,
            filename=filename,
            file_bytes=file_bytes,
            content_type=file.content_type,
        )
        document = supabase_client.create_document(
            case_id=case_id, filename=filename, storage_path=storage_path
        )

        # AC1: enters the background task queue for async processing.
        await document_queue.enqueue(document.id)
    except Exception as exc:
        logger.exception("Failed to store or queue uploaded document")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to store or queue the uploaded document. Check Supabase bucket/table access.",
        ) from exc

    return UploadResponse(document_id=document.id, status=document.status)