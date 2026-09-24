from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, status

from .. import document_repository, extracted_fields_repository, storage_service
from ..fraud_detection.font_consistency import FontConsistencyResult, check_font_consistency
from ..fraud_detection.font_consistency_v2 import (
    ExtractedField,
    FontConsistencyResultV2,
    check_font_consistency_v2,
)
from ..fraud_detection.metadata_analysis import MetadataAnalysisResult, check_metadata_anomalies
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
    automatic classification/extraction pipeline, and a pure read/compute:
    nothing is written to the database (no flags-table row, unlike an
    earlier version of this endpoint). Takes just a document_id and returns
    a plain JSON body (SpellingAnomalyResult, serialised automatically by
    FastAPI's response_model) — safe for another service/agent to call
    directly and repeatedly without side effects.
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
    return await check_spelling_anomalies(
        document_id, file_bytes, document.filename, extracted_fields
    )


@router.get(
    "/{document_id}/detection/metadata-anomaly", response_model=MetadataAnalysisResult
)
async def get_metadata_anomaly(document_id: str) -> MetadataAnalysisResult:
    """
    Digital Authenticity Specialist. Unlike the checks above, this doesn't
    download or re-process the file at all — file metadata (author,
    creator, producer, dates, software) is already captured once, up
    front, for every document by ingestion.metadata_extractor and sits on
    document.metadata (see orchestrator._ingest_document_text); this just
    reads that and runs the local pattern/rule checks in
    fraud_detection.metadata_analysis over it.

    AC6: unlike the read-only checks above (spelling-anomaly explicitly
    writes nothing), this one persists its result — the user story asked
    for it to be stored ready for a future lead-agent aggregator to pull.
    Persistence is best-effort: a write failure is logged but doesn't fail
    the request, since the officer viewing this page shouldn't lose the
    result they can already see over a transient DB error.

    document_type is passed through because some rules only apply to
    documents an institution issued (see
    metadata_analysis.INSTITUTION_ORIGINATED_DOCUMENT_TYPES). That assumes
    the type is correct by the time this runs — the intended flow is that
    an officer confirms classification before anomaly checks are run.
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

    result = check_metadata_anomalies(document_id, document.metadata, document.document_type)

    try:
        await asyncio.to_thread(
            document_repository.update_metadata_anomalies, document_id, result.model_dump()
        )
    except Exception:
        logger.exception("Failed to persist metadata anomaly result for document %s", document_id)

    return result
