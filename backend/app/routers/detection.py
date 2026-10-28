from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..langgraph import trace as langgraph_trace
from ..langgraph.detection import tools as detection_tools
from ..langgraph.detection_report import read_report
from ..models import DocumentRecord, DocumentStatus, skips_detection
from .. import case_service, document_repository, events, extracted_fields_repository, storage_service
from ..events import Stopwatch, officer_event, types
from ..cpu_pool import run_cpu
from ._dependencies import get_document_or_404, get_document_with_metadata_or_404, to_thread_or_502
from ..case_match import DocumentCaseMatch
from ..case_pipeline.cross_document.company_identity import (
    CompanyNameConsistencyResult,
    check_company_name_consistency,
)
from ..fraud_detection.authenticity.ai_generation import AIGenerationResult, check_ai_generation
from ..fraud_detection.font_consistency import (
    ExtractedField,
    FontConsistencyResult,
    check_font_consistency,
)
from ..fraud_detection.images.image_integrity import ImageIntegrityReport, build_image_integrity_report
from ..fraud_detection.images.image_manipulation import (
    ImageManipulationResult,
    ImageTamperingResult,
    check_image_manipulation,
    check_image_tampering,
)
from ..fraud_detection.authenticity.metadata_analysis import MetadataAnalysisResult, check_metadata_anomalies
from ..fraud_detection.layout_anomalies import LayoutAnomalyResult, check_layout_anomalies
from ..fraud_detection.numerical_validation.models import NumericalValidationResult
from ..fraud_detection.numerical_validation.runner import build_result, run_numerical_checks
from ..langgraph.detection.field_checks.binder import bind_checks
from ..fraud_detection.dates.date_anomalies import (
    DateAnomalyResult,
    check_date_anomalies,
)
from ..fraud_detection.pages import check_page_consistency
from ..fraud_detection.pages.models import PageConsistencyResult
from ..fraud_detection.spelling.spelling_anomaly import SpellingAnomalyResult, check_spelling_anomalies
from ..fraud_detection.external_validation.runner import ExternalValidationResult, run_external_validation

router = APIRouter(prefix="/documents", tags=["detection"])
cases_router = APIRouter(prefix="/cases", tags=["detection"])
logger = logging.getLogger(__name__)


async def _download_document_bytes(document: DocumentRecord) -> bytes:
    if document.storage_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document has no stored file"
        )
    return await to_thread_or_502(
        storage_service.download_file, document.storage_path,
        detail="Failed to fetch the document from storage",
        log=("Failed to download document %s from storage", document.id),
    )


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
    return await _check_detection_has_run(document_id, document)


async def _require_detection_has_run_with_metadata(
    document_id: str, document: DocumentRecord = Depends(get_document_with_metadata_or_404)
) -> DocumentRecord:
    return await _check_detection_has_run(document_id, document)


async def _check_detection_has_run(document_id: str, document: DocumentRecord) -> DocumentRecord:
    """
    Gate for the individual on-demand checks below: a loan officer can run
    one specific check manually, but only after the full Lead Investigator
    pass (triggered automatically on approval — see
    workers/detection.py) has completed at least once
    for this document — never the other way round. Checked by looking for
    a stored detection_report on the document's langgraph trace (same
    signal GET .../detection/report uses).
    """
    if skips_detection(document):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Detection doesn't run on documents with no type.",
        )
    trace_data = await asyncio.to_thread(langgraph_trace.load, document_id)
    if not (trace_data or {}).get("detection_report"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run full detection first before running an individual check.",
        )
    return document


class ImageIntegrityResult(BaseModel):
    """
    Both image checks on one document: SCRUM-64 and SCRUM-65. `report` comes
    first and is written for a reader; the raw measurement-level results
    follow unchanged for debugging.
    """

    report: ImageIntegrityReport
    manipulation: ImageManipulationResult
    tampering: ImageTamperingResult


# ---------------------------------------------------------------------------
# On-demand checks: an officer re-runs one check after the full detection run.
# Read-only; most record a step on the document's trace.
# ---------------------------------------------------------------------------

Run = Callable[[DocumentRecord, bytes, list[ExtractedField]], Awaitable[Any]]


@dataclass(frozen=True)
class _Check:
    path: str
    model: type[BaseModel]
    run: Run
    description: str
    file: bool = False  # downloads the stored file
    fields: bool = False  # reads the extracted fields
    metadata: bool = False  # reads document.metadata (exiftool output)
    # The trace step's name and what it records; None records nothing.
    step: str | None = None
    trace: Callable[[Any], dict] | None = None


def _counted(findings: str) -> Callable[[Any], dict]:
    return lambda result: {"unable_to_assess": result.unable_to_assess, "finding_count": len(getattr(result, findings))}


async def _image_integrity(document: DocumentRecord, file_bytes: bytes, _: list[ExtractedField]) -> ImageIntegrityResult:
    manipulation, tampering = await asyncio.gather(
        run_cpu(check_image_manipulation, document.id, file_bytes),
        run_cpu(check_image_tampering, document.id, file_bytes),
    )
    return ImageIntegrityResult(
        report=build_image_integrity_report(manipulation, tampering), manipulation=manipulation, tampering=tampering,
    )


async def _numerical(document: DocumentRecord, _: bytes, fields: list[ExtractedField]) -> NumericalValidationResult:
    bound = await bind_checks(document.document_type, fields)
    outcome = await asyncio.to_thread(run_numerical_checks, document.document_type, fields, bound.bindings)
    return build_result(document.id, document.document_type, fields, outcome, bound.error)


async def _external(document: DocumentRecord, _: bytes, fields: list[ExtractedField]) -> ExternalValidationResult:
    bound = await bind_checks(document.document_type, fields, "external")
    result = await run_external_validation(document.id, document.document_type, fields, bound.bindings)
    result.binding_error = bound.error
    return result


async def _company_name(document: DocumentRecord, _: bytes, __: list[ExtractedField]) -> CompanyNameConsistencyResult:
    case_documents = (
        await asyncio.to_thread(document_repository.list_documents, case_id=document.case_id)
        if document.case_id is not None
        else []
    )
    return check_company_name_consistency(document, case_documents)


async def _metadata(document: DocumentRecord, _: bytes, __: list[ExtractedField]) -> MetadataAnalysisResult:
    return check_metadata_anomalies(document.id, document.metadata, document.document_type)


_NOT_CLEAN = "An empty result with `unable_to_assess: false` means nothing crossed a threshold, not that the document is clean."

_CHECKS = [
    _Check(
        "font-consistency", FontConsistencyResult,
        lambda d, f, x: run_cpu(check_font_consistency, d.id, f, x),
        "Font, size and embedding consistency across the document's text.",
        file=True, fields=True, step="check_font_consistency", trace=_counted("anomalies"),
    ),
    _Check(
        "layout-anomalies", LayoutAnomalyResult,
        lambda d, f, _: run_cpu(check_layout_anomalies, d.id, f, d.document_type, d.classification_confidence),
        "Alignment, margins, spacing and overlaid text. Each finding lists `type`, `page`, `where`, `text`, "
        "`summary` and `checks` (detected, expected, difference). " + _NOT_CLEAN,
        file=True, step="check_layout_anomalies", trace=_counted("anomalies"),
    ),
    _Check(
        "image-manipulation", ImageManipulationResult,
        lambda d, f, _: run_cpu(check_image_manipulation, d.id, f),
        "Compares the resolution and compression of a PDF's embedded images, and checks each for signs of "
        "having been enlarged. " + _NOT_CLEAN,
        file=True,
    ),
    _Check(
        "image-tampering", ImageTamperingResult,
        lambda d, f, _: run_cpu(check_image_tampering, d.id, f),
        "Images edited in after the document was produced: painted over and replaced, stretched or rotated, "
        "pasted with a seam, or with altered brightness, colour or noise. " + _NOT_CLEAN,
        file=True,
    ),
    _Check(
        "ai-generation", AIGenerationResult,
        lambda d, f, _: run_cpu(check_ai_generation, d.id, f),
        "Scores the document's photographs, scans or image upload for traces of AI generation, with each "
        "indicator's value and contribution. A low score is not proof the image is genuine.",
        file=True,
    ),
    _Check(
        "image-integrity", ImageIntegrityResult, _image_integrity,
        "Both image checks in one call (the file is downloaded once), with a report written for a reader.",
        file=True,
    ),
    _Check(
        "spelling-anomaly", SpellingAnomalyResult,
        lambda d, f, x: check_spelling_anomalies(d.id, f, d.filename, x),
        "Misspellings in the document's text and extracted fields.",
        file=True, fields=True, step="check_spelling_anomaly", trace=_counted("anomalies"),
    ),
    _Check(
        "page-consistency", PageConsistencyResult,
        lambda d, f, x: check_page_consistency(d.id, f, d.filename, x),
        "Whether a multi-page PDF's pages belong together: page numbering, balances carried between pages, "
        "identifiers repeated on each page, a page of another size or a scan among digital pages, and duplicated "
        "pages. Evidence about the same page or boundary is one finding; `check_codes` lists every check behind it.",
        file=True, fields=True, step="check_page_consistency", trace=_counted("findings"),
    ),
    _Check(
        "numerical-validation", NumericalValidationResult, _numerical,
        "The per-type arithmetic and consistency checks on the extracted fields (no file download). "
        "One LLM call binds each check's inputs.",
        fields=True, step="check_numerical_validation", trace=_counted("findings"),
    ),
    _Check(
        "date-anomalies", DateAnomalyResult,
        lambda d, _, x: asyncio.to_thread(check_date_anomalies, d.id, x, d.document_type),
        "Every labelled date in the extracted fields, and the inconsistent or suspicious ones (no file download).",
        fields=True, step="check_date_anomalies", trace=_counted("findings"),
    ),
    _Check(
        "external-validation", ExternalValidationResult, _external,
        "The extracted facts against outside references chosen by document type: live ACRA, OneMap and HDB "
        "lookups, and published tables (CPF, GST, IRAS, bank identifiers, GIRO holidays). An unreachable "
        "registry is a skipped check, never a finding.",
        fields=True, step="check_external_validation", trace=_counted("findings"),
    ),
    _Check(
        "company-name-consistency", CompanyNameConsistencyResult, _company_name,
        "This document's company name against every other company document in its case.",
        step="check_company_name_consistency",
        trace=lambda r: {"skipped_reason": r.skipped_reason, "finding_count": len(r.discrepancies)},
    ),
    _Check(
        "metadata-anomaly", MetadataAnalysisResult, _metadata,
        "Rules over the file metadata captured at ingestion (author, creator, producer, dates, software); "
        "some apply only to institution-issued document types.",
        metadata=True, step="check_metadata_anomaly", trace=_counted("anomalies"),
    ),
]


def _check_summary(check: _Check, result: Any) -> dict[str, Any]:
    """What a check found, for its audit entry: the same counts its trace step records, and AI generation's likelihood."""
    summary = check.trace(result) if check.trace else {}
    likelihood = getattr(result, "likelihood", None)
    if likelihood is not None:
        summary["likelihood"] = str(likelihood)
    return summary


def _add_check_route(check: _Check) -> None:
    gate = _require_detection_has_run_with_metadata if check.metadata else _require_detection_has_run

    async def endpoint(document_id: str, document: DocumentRecord = Depends(gate)) -> Any:
        # Audited even though read-only: the officer chose to look again, and some checks call outside services.
        timer = Stopwatch()
        try:
            file_bytes = await _download_document_bytes(document) if check.file else b""
            fields = await _fetch_extracted_fields(document_id) if check.fields else []
            result = await check.run(document, file_bytes, fields)
        except Exception as exc:
            error = exc.detail if isinstance(exc, HTTPException) else f"{type(exc).__name__}: {exc}"
            await events.record(officer_event(
                types.DETECTION_CHECK_RUN, document_id=document_id, case_id=document.case_id, outcome="failed",
                detail={"check": check.path, "error": str(error)[:300]}, **timer.fields(),
            ))
            raise
        if check.step and check.trace:
            await _trace_check(document_id, check.step, **check.trace(result))
        await events.record(officer_event(
            types.DETECTION_CHECK_RUN, document_id=document_id, case_id=document.case_id,
            detail={"check": check.path, **_check_summary(check, result)}, **timer.fields(),
        ))
        return result

    router.add_api_route(
        f"/{{document_id}}/detection/{check.path}", endpoint, methods=["GET"], response_model=check.model,
        name=f"get_{check.path.replace('-', '_')}", description=check.description,
    )


for _check in _CHECKS:
    _add_check_route(_check)


# Reports placed on the page, by document and run: a report doesn't change until detection runs again,
# so the file isn't downloaded and searched on every read.
_placed_reports: dict[tuple[str, str], dict] = {}
_PLACED_REPORTS_KEPT = 64


async def _with_finding_locations(document_id: str, report: dict) -> dict:
    """The report with the findings it can't place itself (older font, spelling and layout findings;
    numerical and date findings) placed on the page for the preview to highlight. Best effort: the
    report is returned as stored if the file can't be read."""
    if not detection_tools.needs_locations(report):
        return report
    key = (document_id, str(report.get("generated_at")))
    if key in _placed_reports:
        return _placed_reports[key]
    try:
        document = await get_document_or_404(document_id)
        file_bytes = await _download_document_bytes(document)
        fields = (
            await asyncio.to_thread(extracted_fields_repository.get_extracted_fields_dict, document_id)
            if detection_tools.needs_fields(report)
            else {}
        )
        placed = await run_cpu(
            detection_tools.add_missing_locations,
            report,
            file_bytes,
            fields,
            document.document_type,
            document.classification_confidence,
        )
    except Exception:
        logger.warning("Could not place the findings of document %s's report", document_id, exc_info=True)
        return report
    if len(_placed_reports) >= _PLACED_REPORTS_KEPT:
        _placed_reports.pop(next(iter(_placed_reports)))
    _placed_reports[key] = placed
    return placed


@router.get("/{document_id}/detection/report")
async def get_document_detection_report(document_id: str) -> dict:
    trace = await asyncio.to_thread(langgraph_trace.load, document_id)
    report = read_report((trace or {}).get("detection_report"))
    if report:
        return await _with_finding_locations(document_id, report)
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
    still completes) — see workers/detection.py. The cancellation is then
    recorded the same way a crash is, so GET .../detection/report and
    retry_document_detection below both already know how to handle it.
    """
    if document.status != DocumentStatus.DETECTING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No detection run is currently in progress for this document.",
        )
    await events.publish(officer_event(
        types.DETECTION_CANCEL_REQUESTED, document_id=document_id, case_id=document.case_id,
    ))
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
    if skips_detection(document):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Detection doesn't run on documents with no type.",
        )
    if document.status == DocumentStatus.DETECTING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Detection is already running for this document.",
        )
    trace_data = await asyncio.to_thread(langgraph_trace.load, document_id)
    has_report = bool((trace_data or {}).get("detection_report"))
    has_error = bool((trace_data or {}).get("detection_error"))
    if has_report or not has_error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Detection can only be retried after a cancelled or failed run.",
        )
    await events.publish(officer_event(
        types.DETECTION_RETRY_REQUESTED, document_id=document_id, case_id=document.case_id,
        detail={"document_type": document.document_type},
    ))
    return {"status": "retrying"}


@cases_router.get("/{case_id}/detection/reports")
async def get_case_detection_reports(case_id: str) -> list[dict]:
    """Every stored detection report for a case's documents, for one combined view."""
    return await to_thread_or_502(
        langgraph_trace.get_case_detection_reports,
        case_id,
        detail="Failed to load detection reports.",
        log=("Failed to load detection reports for case %s", case_id),
    )


@router.get("/{document_id}/detection/case-match", response_model=DocumentCaseMatch)
async def get_case_match(
    document_id: str, document: DocumentRecord = Depends(get_document_or_404)
) -> DocumentCaseMatch:
    """
    Whether this document belongs to its case's company and individuals —
    the identity comparison used by fraud detection (see case_match.py). The check needs
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
