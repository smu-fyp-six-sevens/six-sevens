from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status


from ..langgraph import trace as langgraph_trace
from ..langgraph.detection.graph import DetectionError, run_detection
from ..models import IN_FLIGHT_STATUSES, DocumentRecord
from .. import case_service, extracted_fields_repository, storage_service
from ._dependencies import get_document_or_404
from ..case_match import DocumentCaseMatch
from ..fraud_detection.font_consistency import (
    ExtractedField,
    FontConsistencyResult,
    check_font_consistency,
)
from ..fraud_detection.metadata_analysis import MetadataAnalysisResult, check_metadata_anomalies
from ..fraud_detection.layout_anomalies import LayoutAnomalyResult, check_layout_anomalies
from ..fraud_detection.numerical_validation import (
    NumericalValidationResult,
    check_numerical_validation,
)
from ..fraud_detection.spelling_anomaly import SpellingAnomalyResult, check_spelling_anomalies

router = APIRouter(prefix="/documents", tags=["detection"])
cases_router = APIRouter(prefix="/cases", tags=["detection"])
logger = logging.getLogger(__name__)


async def _download_document_bytes(document: DocumentRecord) -> bytes:
    if document.storage_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document has no stored file"
        )
    try:
        return await asyncio.to_thread(storage_service.download_file, document.storage_path)
    except Exception as exc:
        logger.exception("Failed to download document %s from storage", document.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the document from storage",
        ) from exc


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
    "/{document_id}/detection/font-consistency", response_model=FontConsistencyResult
)
async def get_font_consistency(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> FontConsistencyResult:
    file_bytes = await _download_document_bytes(document)
    extracted_fields = await _fetch_extracted_fields(document_id)
    return await asyncio.to_thread(
        check_font_consistency, document_id, file_bytes, extracted_fields
    )


@router.get(
    "/{document_id}/detection/layout-anomalies",
    response_model=LayoutAnomalyResult,
)
async def get_layout_anomalies(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> LayoutAnomalyResult:
    """
    On-demand layout anomaly check (not part of the automatic pipeline).

    Returns `LayoutAnomalyResult`. Several checks on the same area become
    one finding with `evidence.signals`. Neighbouring flagged paragraphs
    add `local_pattern` / `consecutive_run` enrichment, not a new type.
    Empty `anomalies` with `unable_to_assess: false` means the checks ran
    and nothing crossed a threshold — not that the document is clean.
    """
    file_bytes = await _download_document_bytes(document)
    return await asyncio.to_thread(check_layout_anomalies, document_id, file_bytes)


@router.get(
    "/{document_id}/detection/spelling-anomaly", response_model=SpellingAnomalyResult
)
async def get_spelling_anomaly(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> SpellingAnomalyResult:
    """
    On-demand, like the font-consistency checks above — not part of the
    automatic classification/extraction pipeline, and a pure read/compute:
    nothing is written to the database (no flags-table row, unlike an
    earlier version of this endpoint). Takes just a document_id and returns
    a plain JSON body (SpellingAnomalyResult, serialised automatically by
    FastAPI's response_model) — safe for another service/agent to call
    directly and repeatedly without side effects.
    """
    file_bytes = await _download_document_bytes(document)
    extracted_fields = await _fetch_extracted_fields(document_id)
    return await check_spelling_anomalies(
        document_id, file_bytes, document.filename, extracted_fields
    )


# A pipeline run is still in flight for these — detection needs finished extraction.
_DETECTION_BLOCKED_STATUSES = IN_FLIGHT_STATUSES


@router.post("/{document_id}/detection/run")
async def run_document_detection(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> dict:
    """
    Runs the Lead Investigator + specialists over one document and returns the
    combined report (also stored on the document's langgraph trace, so it
    survives restarts — see langgraph/detection/graph.py). Nothing is written
    to the documents table.
    """
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
@router.get(
    "/{document_id}/detection/numerical-validation",
    response_model=NumericalValidationResult,
)
async def get_numerical_validation(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> NumericalValidationResult:
    """
    Content Validation Specialist. On-demand and side-effect free, like the
    spelling and layout checks — the result is returned as JSON for a lead
    agent to consume and nothing is written to the database.

    Takes both the file (for the figures in the text, and the page each one
    sits on) and the document's extracted fields (for the figures that are
    supposed to reconcile against each other — a statement's running
    balance, a portfolio's quantity x price). Extracted fields degrade
    gracefully: without them the format and Benford passes still run.
    """
    file_bytes = await _download_document_bytes(document)
    extracted_fields = await _fetch_extracted_fields(document_id)
    return await asyncio.to_thread(
        check_numerical_validation,
        document_id,
        file_bytes,
        document.filename,
        extracted_fields,
    )


@router.get(
    "/{document_id}/detection/metadata-anomaly", response_model=MetadataAnalysisResult
)
async def get_metadata_anomaly(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> MetadataAnalysisResult:
    """
    Digital Authenticity Specialist. Unlike the checks above, this doesn't
    download or re-process the file at all — file metadata (author,
    creator, producer, dates, software) is already captured once, up front,
    for every document by ingestion.metadata_extractor and sits on
    document.metadata; this just reads that and runs the local pattern/rule
    checks in fraud_detection.metadata_analysis over it.

    Like the other checks above, this is read-only: the result is returned
    as JSON for a lead agent to consume and nothing is written to the
    database.

    document_type is passed through because some rules only apply to
    documents an institution issued (see
    metadata_analysis.INSTITUTION_ORIGINATED_DOCUMENT_TYPES). That assumes
    the type is correct by the time this runs — the intended flow is that
    an officer confirms classification before anomaly checks are run.
    """
    return check_metadata_anomalies(document_id, document.metadata, document.document_type)


@router.get("/{document_id}/detection/case-match", response_model=DocumentCaseMatch)
async def get_case_match(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> DocumentCaseMatch:
    """
    Whether this document belongs to its case's company and individuals —
    the gate before fraud detection (see case_match.py). The check needs
    every document in the case (the certificate of incumbency is only
    trusted once other documents corroborate it), so this runs the whole
    case's check and returns this document's entry. Read-only.
    """
    if document.case_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document is not attached to a case"
        )

    match = await asyncio.to_thread(case_service.get_case_match_for_document, document_id)
    if match is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return match
