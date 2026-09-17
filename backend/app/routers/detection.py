from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, status

from .. import document_repository, extracted_fields_repository, spelling_anomaly_repository, storage_service
from ..fraud_detection.font_consistency import FontConsistencyResult, check_font_consistency
from ..fraud_detection.font_consistency_v2 import (
    ExtractedField,
    FontConsistencyResultV2,
    check_font_consistency_v2,
)
from ..fraud_detection.spelling_anomaly import SpellingAnomalyResult, check_spelling_anomalies

router = APIRouter(prefix="/documents", tags=["detection"])
logger = logging.getLogger(__name__)


async def _fetch_document_bytes(document_id: str) -> bytes:
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
        return await asyncio.to_thread(storage_service.download_file, document.storage_path)
    except Exception as exc:
        logger.exception("Failed to download document %s from storage", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the document from storage",
        ) from exc


@router.get("/{document_id}/detection/font-consistency", response_model=FontConsistencyResult)
async def get_font_consistency(document_id: str) -> FontConsistencyResult:
    file_bytes = await _fetch_document_bytes(document_id)
    return await asyncio.to_thread(check_font_consistency, document_id, file_bytes)


async def _fetch_extracted_fields(document_id: str) -> list[ExtractedField]:
    # Extracted fields are an enrichment, not a prerequisite — the check
    # still runs meaningfully without them (row-level only), so a fetch
    # failure here degrades gracefully instead of failing the request.
    try:
        return await asyncio.to_thread(
            extracted_fields_repository.get_extracted_fields, document_id
        )
    except Exception:
        logger.exception("Failed to fetch extracted fields for document %s", document_id)
        return []


@router.get(
    "/{document_id}/detection/font-consistency-v2", response_model=FontConsistencyResultV2
)
async def get_font_consistency_v2(document_id: str) -> FontConsistencyResultV2:
    file_bytes = await _fetch_document_bytes(document_id)
    extracted_fields = await _fetch_extracted_fields(document_id)
    return await asyncio.to_thread(
        check_font_consistency_v2, document_id, file_bytes, extracted_fields
    )


@router.get(
    "/{document_id}/detection/spelling-anomaly", response_model=SpellingAnomalyResult
)
async def get_spelling_anomaly(document_id: str) -> SpellingAnomalyResult:
    """
    On-demand, like the font-consistency checks above — not part of the
    automatic classification/extraction pipeline. Persists the result into
    the shared flags table on every call (delete-then-replace, scoped to
    this check's own rows — see spelling_anomaly_repository), since there's
    no separate automatic trigger to hang storage off instead.
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

    file_bytes = await _fetch_document_bytes(document_id)
    extracted_fields = await _fetch_extracted_fields(document_id)
    result = await check_spelling_anomalies(
        document_id, file_bytes, document.filename, extracted_fields
    )

    try:
        await asyncio.to_thread(
            spelling_anomaly_repository.replace_spelling_anomalies,
            document_id,
            document.case_id,
            result.anomalies,
        )
    except Exception:
        logger.exception("Failed to persist spelling anomalies for document %s", document_id)

    return result
