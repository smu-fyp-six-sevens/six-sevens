from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, status

from .. import document_repository, extracted_fields_repository, storage_service
from ..langgraph import trace as langgraph_trace
from ..langgraph.detection.graph import DetectionError, run_detection
from ..models import DocumentStatus
from ..fraud_detection.font_consistency import FontConsistencyResult, check_font_consistency
from ..fraud_detection.font_consistency_v2 import (
    ExtractedField,
    FontConsistencyResultV2,
    check_font_consistency_v2,
)
from ..fraud_detection.spelling_anomaly import SpellingAnomalyResult, check_spelling_anomalies

router = APIRouter(prefix="/documents", tags=["detection"])
cases_router = APIRouter(prefix="/cases", tags=["detection"])
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


# A pipeline run is still in flight for these — detection needs finished extraction.
_DETECTION_BLOCKED_STATUSES = {
    DocumentStatus.UPLOADED,
    DocumentStatus.QUEUED,
    DocumentStatus.PROCESSING,
    DocumentStatus.EXTRACTING,
}


@router.post("/{document_id}/detection/run")
async def run_document_detection(document_id: str) -> dict:
    """
    Runs the Lead Investigator + specialists over one document and returns the
    combined report (also stored on the document's langgraph trace, so it
    survives restarts — see langgraph/detection/graph.py). Nothing is written
    to the documents table.
    """
    document = await asyncio.to_thread(document_repository.get_document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if document.status in _DETECTION_BLOCKED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document is still being processed; run detection once extraction has finished.",
        )
    try:
        return await run_detection(document_id)
    except DetectionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Detection failed for document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Detection failed: {exc}"
        ) from exc


@router.get("/{document_id}/detection/report")
async def get_document_detection_report(document_id: str) -> dict:
    try:
        trace = await asyncio.to_thread(langgraph_trace.get_persisted, document_id)
    except Exception:  # noqa: BLE001 — table unreachable: fall back to in-memory
        trace = None
    trace = trace or langgraph_trace.get(document_id)
    report = (trace or {}).get("detection_report")
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No detection report for this document yet.")
    return report


@cases_router.get("/{case_id}/detection/reports")
async def get_case_detection_reports(case_id: str) -> list[dict]:
    """Every stored detection report for a case's documents, for one combined view."""
    try:
        return await asyncio.to_thread(langgraph_trace.get_case_detection_reports, case_id)
    except Exception as exc:
        logger.exception("Failed to load detection reports for case %s", case_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to load detection reports."
        ) from exc
