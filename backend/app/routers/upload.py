from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from postgrest.exceptions import APIError
from pydantic import BaseModel

from .. import case_service, document_repository, storage_service
from ..config import settings
from ..models import DocumentRecord, DocumentStatus
from ..queue_service import document_queue

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)


class BulkUploadItemResult(BaseModel):
    filename: str
    success: bool
    document_id: Optional[str] = None
    status: Optional[DocumentStatus] = None
    error: Optional[str] = None


class BulkUploadResponse(BaseModel):
    results: list[BulkUploadItemResult]


async def _ingest_one(
    case_id: str | None, file: UploadFile, *, case_verified: bool = False
) -> DocumentRecord:
    """
    Validate -> store -> queue -> (async) hand off to the Classifier Agent
    for one file, called once per file by upload_documents_batch. Raises
    HTTPException on any validation or storage/queue failure. case_verified
    means the batch already confirmed the case exists, so each file needn't.
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
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File exceeds {settings.MAX_FILE_SIZE_BYTES} byte limit",
        )

    if case_id is not None and not case_verified:
        case = await asyncio.to_thread(case_service.get_case, case_id)
        if case is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"case_id '{case_id}' does not exist",
            )

    try:
        storage_path = await asyncio.to_thread(
            storage_service.upload_file,
            case_id,
            filename,
            file_bytes,
            file.content_type,
        )
    except Exception as exc:
        logger.exception("Failed to store uploaded file in Supabase Storage")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to store the uploaded file. Check Supabase bucket access.",
        ) from exc

    try:
        document = await asyncio.to_thread(
            document_repository.create_document, case_id, filename, storage_path
        )
    except APIError as exc:
        await _cleanup_storage_object(storage_path)
        if exc.code == "23503":  # foreign key violation
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"case_id '{case_id}' does not exist",
            ) from exc
        logger.exception("Supabase error while creating document row")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to store the uploaded document record. Check Supabase table access.",
        ) from exc
    except Exception as exc:
        await _cleanup_storage_object(storage_path)
        logger.exception("Failed to create document row after storing file")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to store the uploaded document record. Check Supabase table access.",
        ) from exc

    try:
        # AC1: enters the background task queue for async processing.
        await document_queue.enqueue(document.id)
    except Exception as exc:
        logger.exception(
            "Document %s stored but failed to enqueue for processing", document.id
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Document was stored but could not be queued for processing.",
        ) from exc

    return document


async def _cleanup_storage_object(storage_path: str) -> None:
    """Best-effort delete of a just-uploaded file when the document row failed to save."""
    try:
        await asyncio.to_thread(storage_service.delete_file, storage_path)
    except Exception:
        logger.exception(
            "Failed to clean up orphaned storage object %s after a failed document insert",
            storage_path,
        )


@router.post(
    "/upload-batch", response_model=BulkUploadResponse, status_code=status.HTTP_202_ACCEPTED
)
async def upload_documents_batch(
    case_id: str | None = Form(None),
    files: list[UploadFile] = File(default_factory=list),
) -> BulkUploadResponse:
    """
    Validates/stores/queues one or more files under one case_id in a single
    request (AC1: returns as soon as they're queued, without waiting for
    classification to complete). Each file is handled independently — one
    bad file does not fail the rest of the batch.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one file is required",
        )

    if len(files) > settings.MAX_BULK_FILES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Batch exceeds {settings.MAX_BULK_FILES} file limit",
        )

    # Once for the whole batch rather than once per file. A missing case fails every file the same
    # way it did before, each with its own result.
    case_exists = True
    if case_id is not None:
        case_exists = await asyncio.to_thread(case_service.get_case, case_id) is not None

    async def _handle(file: UploadFile) -> BulkUploadItemResult:
        filename = file.filename or ""
        if not case_exists:
            return BulkUploadItemResult(
                filename=filename, success=False, error=f"case_id '{case_id}' does not exist"
            )
        try:
            document = await _ingest_one(case_id, file, case_verified=True)
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