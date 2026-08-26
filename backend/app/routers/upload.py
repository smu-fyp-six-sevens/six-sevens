from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from postgrest.exceptions import APIError

from .. import document_repository, storage_service
from ..config import settings
from ..models import BulkUploadItemResult, BulkUploadResponse, DocumentRecord, UploadResponse
from ..queue_service import document_queue

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)


async def _ingest_one(case_id: str | None, file: UploadFile) -> DocumentRecord:
    """
    Validate -> store -> queue -> (async) hand off to the Classifier Agent.

    Shared by the single- and bulk-upload endpoints. Raises HTTPException on
    any validation or storage/queue failure.
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
        storage_path = await asyncio.to_thread(
            storage_service.upload_file,
            case_id,
            filename,
            file_bytes,
            file.content_type,
        )
        document = await asyncio.to_thread(
            document_repository.create_document, case_id, filename, storage_path
        )

        # AC1: enters the background task queue for async processing.
        await document_queue.enqueue(document.id)
    except HTTPException:
        raise
    except APIError as exc:
        if exc.code == "23503":  # foreign key violation
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"case_id '{case_id}' does not exist",
            ) from exc
        logger.exception("Supabase error while storing uploaded document")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to store or queue the uploaded document. Check Supabase bucket/table access.",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to store or queue uploaded document")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to store or queue the uploaded document. Check Supabase bucket/table access.",
        ) from exc

    return document


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(case_id: str | None = Form(None), file: UploadFile = File(...)) -> UploadResponse:
    """
    Returns as soon as the document is queued (AC1) — it does not wait for
    classification to complete.
    """
    document = await _ingest_one(case_id, file)
    return UploadResponse(document_id=document.id, status=document.status)


@router.post(
    "/upload-batch", response_model=BulkUploadResponse, status_code=status.HTTP_202_ACCEPTED
)
async def upload_documents_batch(
    case_id: str | None = Form(None), files: list[UploadFile] = File(...)
) -> BulkUploadResponse:
    """
    Same as /upload, but for multiple files under one case_id in a single
    request. Each file is validated/stored/queued independently — one bad
    file does not fail the rest of the batch.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one file is required",
        )

    if len(files) > settings.MAX_BULK_FILES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Batch exceeds {settings.MAX_BULK_FILES} file limit",
        )

    async def _handle(file: UploadFile) -> BulkUploadItemResult:
        filename = file.filename or ""
        try:
            document = await _ingest_one(case_id, file)
            return BulkUploadItemResult(
                filename=filename,
                success=True,
                document_id=document.id,
                status=document.status,
            )
        except HTTPException as exc:
            return BulkUploadItemResult(filename=filename, success=False, error=str(exc.detail))

    results = await asyncio.gather(*(_handle(file) for file in files))
    return BulkUploadResponse(results=list(results))