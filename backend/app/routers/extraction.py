from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from ..config import settings
from ..fraud_detection.image_manipulation import ImageManipulationResult, check_image_manipulation
from ..fraud_detection.metadata_analysis import MetadataAnalysisResult, check_metadata_anomalies
from ..fraud_detection.layout_anomalies import LayoutAnomalyResult, check_layout_anomalies
from ..fraud_detection.numerical_validation import NumericalValidationResult, check_numerical_validation
from ..fraud_detection.date_anomalies import DateAnomalyResult, check_date_anomalies

from ..fraud_detection.spelling_anomaly import SpellingAnomalyResult, check_spelling_anomalies
##why are these parked here

from ..ingestion.ingestion_service import ingest_document
from ..ingestion.metadata_extractor import extract_metadata

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


@router.post("/test-spelling-anomaly", response_model=SpellingAnomalyResult)
async def test_spelling_anomaly(file: UploadFile = File(...)) -> SpellingAnomalyResult:
    """
    Dev/QA endpoint, same spirit as /test above: runs the spelling-anomaly
    check (see fraud_detection.spelling_anomaly) on an uploaded file and
    returns the result directly — no case, no persisted document, and
    (unlike the real GET /documents/{id}/detection/spelling-anomaly
    endpoint) nothing written to the flags table, since there's no real
    document_id/case_id to attach a flag to here. Also runs without any
    extracted_fields to cross-reference against (there's no real document
    to have extracted anything from), so it'll flag more proper nouns here
    than the real endpoint would for the same file.
    """
    file_bytes, filename, _content_type = await _read_and_validate_upload(file)

    # A random id purely for log messages inside check_spelling_anomalies —
    # no document exists to key it to here.
    return await check_spelling_anomalies(str(uuid.uuid4()), file_bytes, filename)


@router.post("/test-metadata-anomaly", response_model=MetadataAnalysisResult)
async def test_metadata_anomaly(
    file: UploadFile = File(...),
    document_type: Optional[str] = Form(None),
) -> MetadataAnalysisResult:
    """
    Dev/QA endpoint, same spirit as /test-spelling-anomaly above: unlike the
    real GET /documents/{id}/detection/metadata-anomaly endpoint (which
    reads metadata already captured at ingestion time), this one runs
    exiftool on the upload directly since there's no real document here for
    ingestion to have already captured anything against — no case, no
    persisted document, nothing written to the database.

    document_type is optional here because there's no classified document
    to take it from; supplying it lets this tool exercise the rules that
    only apply to institution-issued documents (see
    metadata_analysis.INSTITUTION_ORIGINATED_DOCUMENT_TYPES), which would
    otherwise be unreachable from the dev tool.
    """
    file_bytes, filename, _content_type = await _read_and_validate_upload(file)
    metadata = await asyncio.to_thread(extract_metadata, file_bytes, filename)
    return check_metadata_anomalies(str(uuid.uuid4()), metadata, document_type or None)
    
@router.post("/test-numerical-validation", response_model=NumericalValidationResult)
async def test_numerical_validation(
    document_type: Optional[str] = Form(None),
) -> NumericalValidationResult:
    """
    Dev/QA endpoint: same `NumericalValidationResult` as
    GET /documents/{id}/detection/numerical-validation.

    No file upload — the check runs entirely from extracted_fields, which
    there's none of for an ad-hoc dev call, so every per-type check reports
    SKIPPED (nothing to read fields from). document_type is optional here
    for the same reason metadata_analysis's dev endpoint takes one: it lets
    this tool at least confirm which checks WOULD be wired up for a type,
    without pretending there's real extracted data behind them.
    """
    return await asyncio.to_thread(
        check_numerical_validation, str(uuid.uuid4()), None, document_type or None
    )


@router.post("/test-date-anomalies", response_model=DateAnomalyResult)
async def test_date_anomalies(
    document_type: Optional[str] = Form(None),
) -> DateAnomalyResult:
    """
    Dev/QA endpoint: same `DateAnomalyResult` as
    GET /documents/{id}/detection/date-anomalies.

    No file upload — the check runs entirely from extracted_fields, which
    there's none of for an ad-hoc dev call, so every per-type check reports
    SKIPPED, and labelled date extraction (AC1/AC2) has nothing to read
    from either. document_type is optional for the same reason
    numerical-validation's dev endpoint takes one.
    """
    return await asyncio.to_thread(
        check_date_anomalies, str(uuid.uuid4()), None, document_type or None
    )


@router.post("/test-layout-anomalies", response_model=LayoutAnomalyResult)
async def test_layout_anomalies(file: UploadFile = File(...)) -> LayoutAnomalyResult:
    """
    Dev/QA upload: same `LayoutAnomalyResult` as
    GET /documents/{id}/detection/layout-anomalies. `document_id` is a
    fresh UUID for this request, not a stored document.

    The JSON lists each finding as `type`, `page`, `where`, `text`,
    `summary`, and `checks` (detected, expected, difference). Several
    checks on the same area stay one finding.
    """
    file_bytes, _filename, _content_type = await _read_and_validate_upload(file)
    return await asyncio.to_thread(
        check_layout_anomalies, str(uuid.uuid4()), file_bytes
    )


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
