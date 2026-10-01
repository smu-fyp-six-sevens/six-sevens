from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from ..config import settings
from ..fraud_detection.image_manipulation import ImageManipulationResult, check_image_manipulation

router = APIRouter(prefix="/extraction", tags=["extraction"])


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


@router.post("/test-image-manipulation", response_model=ImageManipulationResult)
async def test_image_manipulation(file: UploadFile = File(...)) -> ImageManipulationResult:
    """
    Dev/QA upload: same `ImageManipulationResult` as
    GET /documents/{id}/detection/image-manipulation. `document_id` is a
    fresh UUID for this request, not a stored document.
    """
    file_bytes, _filename, _content_type = await _read_and_validate_upload(file)
    return await asyncio.to_thread(
        check_image_manipulation, str(uuid.uuid4()), file_bytes
    )
