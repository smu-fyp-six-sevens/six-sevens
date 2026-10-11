from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..langgraph import trace as langgraph_trace
from ..models import DocumentRecord
from .. import case_service, document_repository, extracted_fields_repository, storage_service
from . import documents as documents_router
from ._dependencies import get_document_or_404
from ..case_match import DocumentCaseMatch
from ..fraud_detection.company_identity import (
    CompanyNameConsistencyResult,
    check_company_name_consistency,
)
from ..fraud_detection.ai_generation import AIGenerationResult, check_ai_generation
from ..fraud_detection.font_consistency import (
    ExtractedField,
    FontConsistencyResult,
    check_font_consistency,
)
from ..fraud_detection.image_integrity import ImageIntegrityReport, build_image_integrity_report
from ..fraud_detection.image_manipulation import (
    ImageManipulationResult,
    ImageTamperingResult,
    check_image_manipulation,
    check_image_tampering,
)
from ..fraud_detection.metadata_analysis import MetadataAnalysisResult, check_metadata_anomalies
from ..fraud_detection.layout_anomalies import LayoutAnomalyResult, check_layout_anomalies
from ..fraud_detection.numerical_validation.models import NumericalValidationResult
from ..fraud_detection.numerical_validation.runner import build_result, run_numerical_checks
from ..langgraph.detection.binder import bind_numerical_checks
from ..fraud_detection.date_anomalies import (
    DateAnomalyResult,
    check_date_anomalies,
)
from ..fraud_detection.spelling_anomaly import SpellingAnomalyResult, check_spelling_anomalies
from ..fraud_detection.registry_lookups import RegistryResult, public_holidays_for, verify_against_registries

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


async def _trace_check(document_id: str, step: str, **fields: object) -> None:
    """Records an on-demand check on the document's langgraph trace — same
    table classify/extract/detection-run steps land on (see langgraph/trace.py)."""
    await asyncio.to_thread(langgraph_trace.resume, document_id)
    langgraph_trace.record(document_id, step, **fields)


async def _require_detection_has_run(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> DocumentRecord:
    """
    Gate for the individual on-demand checks below: a loan officer can run
    one specific check manually, but only after the full Lead Investigator
    pass (triggered automatically on approval — see
    routers/documents.py's _launch_detection) has completed at least once
    for this document — never the other way round. Checked by looking for
    a stored detection_report on the document's langgraph trace (same
    signal GET .../detection/report uses).
    """
    if documents_router.skips_detection(document):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Detection doesn't run on unrecognised documents.",
        )
    try:
        persisted = await asyncio.to_thread(langgraph_trace.get_persisted, document_id)
    except Exception:  # noqa: BLE001 — table unreachable: fall back to in-memory
        persisted = None
    trace_data = persisted or langgraph_trace.get(document_id)
    if not (trace_data or {}).get("detection_report"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run full detection first before running an individual check.",
        )
    return document


@router.get(
    "/{document_id}/detection/font-consistency", response_model=FontConsistencyResult
)
async def get_font_consistency(
    document_id: str, document: DocumentRecord = Depends(_require_detection_has_run)
) -> FontConsistencyResult:
    file_bytes = await _download_document_bytes(document)
    extracted_fields = await _fetch_extracted_fields(document_id)
    result = await asyncio.to_thread(
        check_font_consistency, document_id, file_bytes, extracted_fields
    )
    await _trace_check(
        document_id, "check_font_consistency",
        unable_to_assess=result.unable_to_assess, finding_count=len(result.anomalies),
    )
    return result


@router.get(
    "/{document_id}/detection/layout-anomalies",
    response_model=LayoutAnomalyResult,
)
async def get_layout_anomalies(
    document_id: str, document: DocumentRecord = Depends(_require_detection_has_run)
) -> LayoutAnomalyResult:
    """
    On-demand layout anomaly check (not part of the automatic pipeline).

    Returns `LayoutAnomalyResult`. The JSON lists each finding as `type`,
    `page`, `where`, `text`, `summary`, and `checks` (detected, expected,
    difference). Several checks on the same area stay one finding.
    Empty `anomalies` with `unable_to_assess: false` means the checks ran
    and nothing crossed a threshold — not that the document is clean.
    """
    file_bytes = await _download_document_bytes(document)
    result = await asyncio.to_thread(
        check_layout_anomalies,
        document_id,
        file_bytes,
        document.document_type,
        document.classification_confidence,
    )
    await _trace_check(
        document_id, "check_layout_anomalies",
        unable_to_assess=result.unable_to_assess, finding_count=len(result.anomalies),
    )
    return result


@router.get(
    "/{document_id}/detection/image-manipulation",
    response_model=ImageManipulationResult,
)
async def get_image_manipulation(
    document_id: str, document: DocumentRecord = Depends(_require_detection_has_run)
) -> ImageManipulationResult:
    """
    On-demand, side-effect free: compares the resolution and compression of
    the images embedded in a PDF against each other, and checks each for
    signs of having been enlarged. Empty `indicators` with
    `unable_to_assess: false` means the checks ran and nothing crossed a
    tolerance — not that the document is clean.
    """
    file_bytes = await _download_document_bytes(document)
    return await asyncio.to_thread(check_image_manipulation, document_id, file_bytes)


@router.get(
    "/{document_id}/detection/image-tampering",
    response_model=ImageTamperingResult,
)
async def get_image_tampering(
    document_id: str, document: DocumentRecord = Depends(_require_detection_has_run)
) -> ImageTamperingResult:
    """
    On-demand, side-effect free: looks for images edited into the document
    after it was produced — painted over and replaced, stretched or
    rotated, pasted with a visible seam, or with altered brightness, colour
    or noise. Empty `indicators` with `unable_to_assess: false` means the
    checks ran and nothing crossed a tolerance — not that the document is
    clean.
    """
    file_bytes = await _download_document_bytes(document)
    return await asyncio.to_thread(check_image_tampering, document_id, file_bytes)


@router.get(
    "/{document_id}/detection/ai-generation",
    response_model=AIGenerationResult,
)
async def get_ai_generation(
    document_id: str, document: DocumentRecord = Depends(_require_detection_has_run)
) -> AIGenerationResult:
    """
    On-demand, side-effect free: scores the document's photographs, scans or
    image upload for traces of AI generation, with every indicator's
    measured value and contribution to the score. A low score means no
    indicator crossed its threshold — not that the image is proven genuine.
    """
    file_bytes = await _download_document_bytes(document)
    return await asyncio.to_thread(check_ai_generation, document_id, file_bytes)


class ImageIntegrityResult(BaseModel):
    """
    Both image checks on one document: SCRUM-64 and SCRUM-65. `report` comes
    first and is written for a reader; the raw measurement-level results
    follow unchanged for debugging.
    """

    report: ImageIntegrityReport
    manipulation: ImageManipulationResult
    tampering: ImageTamperingResult


@router.get(
    "/{document_id}/detection/image-integrity",
    response_model=ImageIntegrityResult,
)
async def get_image_integrity(
    document_id: str, document: DocumentRecord = Depends(_require_detection_has_run)
) -> ImageIntegrityResult:
    """
    On-demand, side-effect free: both image checks in one call, so the
    individual-check panel offers a single "Image integrity" option. The file
    is downloaded once and both checks run on it in parallel.
    """
    file_bytes = await _download_document_bytes(document)
    manipulation, tampering = await asyncio.gather(
        asyncio.to_thread(check_image_manipulation, document_id, file_bytes),
        asyncio.to_thread(check_image_tampering, document_id, file_bytes),
    )
    return ImageIntegrityResult(
        report=build_image_integrity_report(manipulation, tampering),
        manipulation=manipulation,
        tampering=tampering,
    )


@router.get(
    "/{document_id}/detection/spelling-anomaly", response_model=SpellingAnomalyResult
)
async def get_spelling_anomaly(
    document_id: str, document: DocumentRecord = Depends(_require_detection_has_run)
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
    result = await check_spelling_anomalies(
        document_id, file_bytes, document.filename, extracted_fields
    )
    await _trace_check(
        document_id, "check_spelling_anomaly",
        unable_to_assess=result.unable_to_assess, finding_count=len(result.anomalies),
    )
    return result


@router.get("/{document_id}/detection/report")
async def get_document_detection_report(document_id: str) -> dict:
    try:
        trace = await asyncio.to_thread(langgraph_trace.get_persisted, document_id)
    except Exception:  # noqa: BLE001 — table unreachable: fall back to in-memory
        trace = None
    trace = trace or langgraph_trace.get(document_id)
    report = (trace or {}).get("detection_report")
    if report:
        return report
    # A report always wins over a stale error from an earlier failed attempt
    # (above) — only fall back to reporting the error when there's no report
    # at all, so the frontend can tell "crashed" apart from "never run yet"
    # instead of both looking like the same 404 forever.
    error = (trace or {}).get("detection_error")
    if error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Detection failed: {error}. Run detection again.",
        )
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No detection report for this document yet.")


@router.post("/{document_id}/detection/cancel")
async def cancel_document_detection(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> dict:
    """
    Interrupts an in-flight detection run — the Detection panel's Cancel
    action while a document reads "Scanning"/"Detecting". Only takes effect
    at the graph's next await point (an Anthropic call already in flight
    still completes) — see routers/documents.py's _run_detection_task.
    The cancellation is then recorded the same way a crash is, so
    GET .../detection/report and retry_document_detection below both
    already know how to handle it.
    """
    task = documents_router._detection_tasks.get(document_id)
    if task is None or task.done():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No detection run is currently in progress for this document.",
        )
    task.cancel()
    return {"status": "cancelling"}


@router.post("/{document_id}/detection/retry")
async def retry_document_detection(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> dict:
    """
    Re-launches detection after a previous attempt was cancelled or
    crashed. NOT a general "run detection" trigger — that only ever
    happens automatically on approval (routers/documents.py's
    approve_document) — so this is only allowed when the last attempt left
    an error/cancellation behind and produced no report, and only when
    nothing is already running for this document.
    """
    if documents_router.skips_detection(document):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Detection doesn't run on unrecognised documents.",
        )
    existing = documents_router._detection_tasks.get(document_id)
    if existing is not None and not existing.done():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Detection is already running for this document.",
        )
    try:
        persisted = await asyncio.to_thread(langgraph_trace.get_persisted, document_id)
    except Exception:  # noqa: BLE001 — table unreachable: fall back to in-memory
        persisted = None
    trace_data = persisted or langgraph_trace.get(document_id)
    has_report = bool((trace_data or {}).get("detection_report"))
    has_error = bool((trace_data or {}).get("detection_error"))
    if has_report or not has_error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Detection can only be retried after a cancelled or failed run.",
        )
    documents_router._launch_detection(document_id, document.case_id)
    return {"status": "retrying"}


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
    document_id: str, document: DocumentRecord = Depends(_require_detection_has_run)
) -> NumericalValidationResult:
    """
    Content Validation Specialist. On-demand and side-effect free, like the
    spelling and layout checks — the result is returned as JSON for a lead
    agent to consume and nothing is written to the database.

    Works from the document's extracted fields alone (trusted as already
    human-reviewed) — no file download or re-ingestion. document_type
    decides which of the per-type checks in
    numerical_validation.DOCUMENT_TYPE_CHECKS apply, the same officer-
    confirmed value metadata_analysis uses for its own type-scoped rules.
    """
    extracted_fields = await _fetch_extracted_fields(document_id)
    bound = await bind_numerical_checks(document.document_type, extracted_fields)
    outcome = await asyncio.to_thread(
        run_numerical_checks, document.document_type, extracted_fields, bound.bindings,
    )
    result = build_result(document_id, document.document_type, extracted_fields, outcome, bound.error)
    await _trace_check(
        document_id, "check_numerical_validation",
        unable_to_assess=result.unable_to_assess, finding_count=len(result.findings),
    )
    return result


@router.get(
    "/{document_id}/detection/date-anomalies",
    response_model=DateAnomalyResult,
)
async def get_date_anomalies(
    document_id: str, document: DocumentRecord = Depends(_require_detection_has_run)
) -> DateAnomalyResult:
    """
    Content Validation Specialist — "Detect Date Anomalies". On-demand and
    side-effect free, same contract as numerical-validation above: the
    result is returned as JSON for a lead agent to consume, nothing is
    written to the database.

    Split out of numerical_validation's date-logic checks into their own
    module (date_anomalies.py) so this dimension has its own user story,
    ACs, and endpoint, mirroring numerical-validation's own split from the
    combined checks it started life with. Works from extracted fields
    alone (trusted as already human-reviewed) — no file download or
    re-ingestion.
    """
    extracted_fields = await _fetch_extracted_fields(document_id)
    holidays = await public_holidays_for(document.document_type)
    result = await asyncio.to_thread(
        check_date_anomalies,
        document_id,
        extracted_fields,
        document.document_type,
        holidays,
    )
    await _trace_check(
        document_id, "check_date_anomalies",
        unable_to_assess=result.unable_to_assess, finding_count=len(result.findings),
    )
    return result


@router.get("/{document_id}/detection/registry-verification", response_model=RegistryResult)
async def get_registry_verification(
    document_id: str, document: DocumentRecord = Depends(_require_detection_has_run)
) -> RegistryResult:
    """
    The document's extracted facts against Singapore's public registries, live: ACRA
    (the company exists, under this name, is live, with this incorporation date and
    registered address), and OneMap and HDB (the address exists and, for a home address,
    is a residential block). Read-only.
    A registry that can't be reached is reported as a skipped check, never a finding.
    """
    extracted_fields = await _fetch_extracted_fields(document_id)
    result = await verify_against_registries(document_id, extracted_fields, document.document_type)
    await _trace_check(
        document_id, "check_registry_verification",
        unable_to_assess=result.unable_to_assess, finding_count=len(result.findings),
    )
    return result


@router.get(
    "/{document_id}/detection/company-name-consistency",
    response_model=CompanyNameConsistencyResult,
)
async def get_company_name_consistency(
    document_id: str, document: DocumentRecord = Depends(_require_detection_has_run)
) -> CompanyNameConsistencyResult:
    """
    Content Validation Specialist — "Company Identity Consistency". Compares
    this document's company name with every other company document in its
    case (see fraud_detection/company_identity.py). Read-only, from
    extracted fields alone.
    """
    case_documents = (
        await asyncio.to_thread(document_repository.list_documents, case_id=document.case_id)
        if document.case_id is not None
        else []
    )
    result = check_company_name_consistency(document, case_documents)
    await _trace_check(
        document_id, "check_company_name_consistency",
        skipped_reason=result.skipped_reason, finding_count=len(result.discrepancies),
    )
    return result


@router.get(
    "/{document_id}/detection/metadata-anomaly", response_model=MetadataAnalysisResult
)
async def get_metadata_anomaly(
    document_id: str, document: DocumentRecord = Depends(_require_detection_has_run)
) -> MetadataAnalysisResult:
    """
    Digital Authenticity Specialist. Like the checks above, this doesn't
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
    result = check_metadata_anomalies(document_id, document.metadata, document.document_type)
    await _trace_check(
        document_id, "check_metadata_anomaly",
        unable_to_assess=result.unable_to_assess, finding_count=len(result.anomalies),
    )
    return result


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
