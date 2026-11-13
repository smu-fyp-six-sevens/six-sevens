from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from postgrest.exceptions import APIError
from pydantic import BaseModel

from .. import case_service, document_repository, events, storage_service
from ._dependencies import ensure_case_editable, to_thread_or_502
from ..config import settings
from ..events import officer_event, types
from ..models import DocumentRecord, DocumentStatus

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)


class BulkUploadItemResult(BaseModel):
    filename: str
    success: bool
    document_id: str | None = None
    status: DocumentStatus | None = None
    error: str | None = None


class BulkUploadResponse(BaseModel):
    results: list[BulkUploadItemResult]


_NO_CASE = "This case no longer exists."
_NOT_SAVED = "This file couldn't be saved. Upload it again, and tell the team if it keeps happening."


def _bytes_match_type(file_bytes: bytes, content_type: str) -> bool:
    """The file's first bytes are what its declared type says (the browser's type alone isn't trusted)."""
    if content_type == "application/pdf":
        return b"%PDF-" in file_bytes[:1024]  # the spec allows a little junk before the header
    if content_type == "image/png":
        return file_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/jpeg":
        return file_bytes.startswith(b"\xff\xd8\xff")
    return False

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
            detail="This file type isn't supported. Upload a PDF, PNG or JPG file.",
        )

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The file has no name. Rename it and upload it again.",
        )

    filename = file.filename

    file_bytes = await file.read()
    if len(file_bytes) > settings.MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"This file is over the {settings.MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB limit. Upload a smaller file.",
        )
    if not _bytes_match_type(file_bytes, file.content_type or ""):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="This file's contents don't match its type. Upload a real PDF, PNG or JPG file.",
        )

    if case_id is not None and not case_verified:
        case = await to_thread_or_502(
            case_service.get_case, case_id,
            detail="Failed to look up the case. Check Supabase table access.",
            log=("Failed to look up case %s for an upload", case_id),
        )
        if case is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_NO_CASE,
            )

    storage_path = await to_thread_or_502(
        storage_service.upload_file,
        case_id,
        filename,
        file_bytes,
        file.content_type,
        detail="Failed to store the uploaded file. Check Supabase bucket access.",
        log=("Failed to store uploaded file in Supabase Storage",),
    )

    try:
        document = await asyncio.to_thread(
            document_repository.create_document, case_id, filename, storage_path
        )
    except APIError as exc:
        await _cleanup_storage_object(storage_path)
        if exc.code == "23503":  # foreign key violation
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_NO_CASE,
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
        # AC1: queued, then the event hands it to the doc-worker.
        await asyncio.to_thread(
            document_repository.update_status, document.id, DocumentStatus.QUEUED, queued_at=datetime.now(timezone.utc)
        )
        await events.publish(officer_event(
            types.DOCUMENT_UPLOADED, document_id=document.id, case_id=case_id,
            detail={"filename": filename, "content_type": file.content_type, "size_bytes": len(file_bytes)},
        ))
    except Exception as exc:
        # Undo it, so uploading again doesn't leave a second copy behind.
        logger.exception("Document %s stored but failed to enqueue for processing; removing it", document.id)
        try:
            await asyncio.to_thread(document_repository.remove_document, document)
        except Exception:
            logger.exception("Failed to remove document %s after its upload event failed", document.id)
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
    await ensure_case_editable(case_id)

    if len(files) > settings.MAX_BULK_FILES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Batch exceeds {settings.MAX_BULK_FILES} file limit",
        )

    # Once for the whole batch rather than once per file. A missing case fails every file the same
    # way it did before, each with its own result.
    case_exists = True
    if case_id is not None:
        case_exists = await to_thread_or_502(
            case_service.get_case, case_id,
            detail="Failed to look up the case. Check Supabase table access.",
            log=("Failed to look up case %s for a bulk upload", case_id),
        ) is not None

    async def _handle(file: UploadFile) -> BulkUploadItemResult:
        filename = file.filename or ""
        if not case_exists:
            return BulkUploadItemResult(
                filename=filename, success=False, error=_NO_CASE
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
            # A 4xx is the file's own problem, already worded for the officer; a 5xx's detail is for whoever reads
            # the logs (it was logged where it was raised), so the officer gets a sentence instead.
            if exc.status_code >= 500:
                return BulkUploadItemResult(filename=filename, success=False, error=_NOT_SAVED)
            return BulkUploadItemResult(filename=filename, success=False, error=str(exc.detail))

    results = await asyncio.gather(*(_handle(file) for file in files))
    return BulkUploadResponse(results=list(results))