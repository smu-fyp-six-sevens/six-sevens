"""
Detection tools — one thin wrapper per check, each parameter-bound to the
document under review (`document_id` comes from graph state via InjectedState,
never from the model). Every tool returns the same shape:

    {"tool": name, "unable_to_assess": bool, "reason": str | None,
     "findings": [{"id": "<tool>-<n>", "summary": str, ...detail}], "truncated": int}

Findings are size-capped and fact-only; severity and rationale are added by
the specialist that called the tool, never by the tool itself. A check's own
deterministic severity vocabulary (numerical/date findings carry
impossible/inconsistent/implausible/signal; metadata anomalies carry
high/medium/low) is passed through as a fact under `severity_hint` — kept
distinct from the specialist's own `info/low/medium/high` scale, which is
added later in specialists._collect_node and must not be shadowed by it.
"Unable to assess" is its own result, never a pass. Text inside findings is
document content and must be treated as untrusted data by whatever reads it.

Each tool is a thin adapter onto the real, already-tested check in
`fraud_detection/*.py` — same functions and calling convention as
`routers/detection.py`'s on-demand endpoints, just fed from the per-run
context cache below instead of a request's `Depends(get_document_or_404)`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ... import document_repository, extracted_fields_repository, storage_service
from ...cpu_pool import run_cpu
from ...fraud_detection.ai_generation import AIImageAssessment, AIIndicator, check_ai_generation
from ...fraud_detection.date_anomalies import check_date_anomalies
from ...fraud_detection.font_consistency import check_font_consistency
from ...fraud_detection.image_manipulation import check_image_manipulation, check_image_tampering
from ...fraud_detection.layout_anomalies import check_layout_anomalies
from ...fraud_detection.metadata_analysis import check_metadata_anomalies
from ...fraud_detection.spelling_anomaly import check_spelling_anomalies

logger = logging.getLogger("classifier_pipeline.langgraph.detection.tools")

MAX_FINDINGS = 15
_MAX_TEXT = 80

# Per-run cache so several specialists calling tools on the same document
# don't each re-download the file or re-read the row. Cleared by
# graph.run_detection when the run ends.
_context: dict[str, dict[str, Any]] = {}


def clear_context(document_id: str) -> None:
    _context.pop(document_id, None)


async def _document(document_id: str):
    entry = _context.setdefault(document_id, {})
    if "document" not in entry:
        entry["document"] = await asyncio.to_thread(
            document_repository.get_document, document_id, include_fields=False
        )
    return entry["document"]


async def _extracted_fields(document_id: str) -> list:
    entry = _context.setdefault(document_id, {})
    if "fields" not in entry:
        try:
            entry["fields"] = await asyncio.to_thread(
                extracted_fields_repository.get_extracted_fields, document_id
            )
        except Exception:  # noqa: BLE001 — enrichment only; checks degrade without it
            logger.exception("Failed to fetch extracted fields for document %s", document_id)
            entry["fields"] = []
    return entry["fields"]


async def document_fields(document_id: str) -> list:
    """The document's extracted fields, fetched once per run."""
    return await _extracted_fields(document_id)


async def _file_bytes(document_id: str) -> bytes | None:
    entry = _context.setdefault(document_id, {})
    if "bytes" not in entry:
        document = await _document(document_id)
        if document is None or not document.storage_path:
            entry["bytes"] = None
        else:
            entry["bytes"] = await asyncio.to_thread(storage_service.download_file, document.storage_path)
    return entry["bytes"]


# Reasons a check gives when the document simply has nothing it applies to:
# an image with no authorship metadata, a PDF with no embedded images, a
# document without dates. That is a pass with nothing to report, not a check
# that failed, so it doesn't make the verdict inconclusive. Anything else
# (a file that couldn't be opened, OCR with no text, a tool error) stays
# Unable to Assess.
_NOTHING_TO_CHECK_REASONS = tuple(re.compile(pattern) for pattern in (
    r"^No embedded images found",
    r"^Only \d+ distinct image asset",
    r"^This \w+ image carries no authorship or editing metadata",
    r"^None of the supported metadata fields",
    r"^No dates could be reliably extracted",
    r"^Not enough comparable text to assess layout",
    r"^Nothing to check:",
    # AI generation says so itself; its real failures start "Could not run:".
    r"^Nothing to check:",
))


def _is_nothing_to_check(reason: str) -> bool:
    return any(pattern.match(reason) for pattern in _NOTHING_TO_CHECK_REASONS)


def _result(tool_name: str, findings: list[dict], *, unable: str | None = None) -> dict:
    truncated = max(0, len(findings) - MAX_FINDINGS)
    nothing_to_check = unable is not None and _is_nothing_to_check(unable)
    return {
        "tool": tool_name,
        "unable_to_assess": unable is not None and not nothing_to_check,
        "nothing_to_check": nothing_to_check,
        "reason": unable,
        "findings": findings[:MAX_FINDINGS],
        "truncated": truncated,
    }


def _with_ids(tool_name: str, findings: list[dict]) -> list[dict]:
    # "summary" is part of every tool's contract (module docstring) — the lead's
    # review prompt and the frontend both index findings by it unconditionally,
    # so a tool that forgets it crashes detection downstream instead of here.
    for finding in findings:
        assert "summary" in finding, f"{tool_name} finding missing required 'summary' key: {finding}"
    return [{"id": f"{tool_name}-{i + 1}", **finding} for i, finding in enumerate(findings)]


def _short(text: Any) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= _MAX_TEXT else text[: _MAX_TEXT - 1] + "…"


def _font_deviation_summary(
    deviation_type: str, detected: dict, expected: dict, field_name: str | None, occurrence_count: int
) -> str:
    where = f"'{field_name}'" if field_name else "text"
    changed = [
        f"{label} {detected.get(key)!r} vs. expected {expected.get(key)!r}"
        for key, label in (
            ("font", "font"), ("size", "size"), ("bold", "bold"),
            ("italic", "italic"), ("underline", "underline"),
        )
        if detected.get(key) != expected.get(key)
    ]
    detail = "; ".join(changed) if changed else deviation_type
    plural = "s" if occurrence_count != 1 else ""
    return f"{deviation_type.capitalize()} deviation on {where}: {detail} ({occurrence_count} occurrence{plural})."


# --- Layout & Rendering -----------------------------------------------------


@tool
async def font_consistency_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Checks the document's rendered text for font, font-size and emphasis inconsistencies — text that deviates from the surrounding row/region or from how the document renders the same kind of field elsewhere (a classic sign of edited text). Only meaningful for PDFs with native text."""
    file_bytes = await _file_bytes(document_id)
    if file_bytes is None:
        return _result("font_consistency_tool", [], unable="Document file is not available in storage")

    fields = await _extracted_fields(document_id)
    result = await run_cpu(check_font_consistency, document_id, file_bytes, fields)
    if result.unable_to_assess:
        return _result(
            "font_consistency_tool", [], unable=result.unable_to_assess_reason or "Unable to assess"
        )

    findings = [
        {
            "deviation_type": anomaly.deviation_type,
            "detected": anomaly.detected,
            "expected": anomaly.expected,
            "occurrence_count": anomaly.occurrence_count,
            "field_name": anomaly.field_name,
            "locations": [
                {"page": loc.page + 1, "text": _short(loc.text)} for loc in anomaly.locations[:5]
            ],
            "summary": _font_deviation_summary(
                anomaly.deviation_type, anomaly.detected, anomaly.expected,
                anomaly.field_name, anomaly.occurrence_count,
            ),
        }
        for anomaly in result.anomalies
    ]
    return _result("font_consistency_tool", _with_ids("font_consistency_tool", findings))


@tool
async def layout_anomalies_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Checks the document's text positioning for indentation, alignment, margin and paragraph-spacing that clash with the rest of the document, and for overlapping/overlaid text blocks — all classic signs of an inserted, edited, or pasted-over clause. Only meaningful for PDFs with native text."""
    file_bytes = await _file_bytes(document_id)
    if file_bytes is None:
        return _result("layout_anomalies_tool", [], unable="Document file is not available in storage")

    # The type selects a layout profile; an unknown document gets the generic checks.
    document = await _document(document_id)
    result = await run_cpu(
        check_layout_anomalies,
        document_id,
        file_bytes,
        getattr(document, "document_type", None),
        getattr(document, "classification_confidence", None),
    )
    if result.unable_to_assess:
        return _result(
            "layout_anomalies_tool", [], unable=result.unable_to_assess_reason or "Unable to assess"
        )

    findings = [anomaly.model_dump() for anomaly in result.anomalies]
    return _result("layout_anomalies_tool", _with_ids("layout_anomalies_tool", findings))


# --- Content Validation -----------------------------------------------------


@tool
async def spelling_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Finds words in the document that look misspelled (after ignoring legal/financial terms, names and the document's own extracted values). Genuine typos in a formal issued document can indicate tampering or forgery."""
    document = await _document(document_id)
    if document is None:
        return _result("spelling_tool", [], unable="Document not found")

    file_bytes = await _file_bytes(document_id)
    if file_bytes is None:
        return _result("spelling_tool", [], unable="Document file is not available in storage")

    fields = await _extracted_fields(document_id)
    result = await check_spelling_anomalies(document_id, file_bytes, document.filename, fields)
    if result.unable_to_assess:
        return _result("spelling_tool", [], unable=result.unable_to_assess_reason or "Unable to assess")

    findings = [
        {
            "word": anomaly.word,
            "context": _short(anomaly.surrounding_text),
            "suggested_correction": anomaly.suggested_correction,
            "summary": (
                f'Possible misspelling "{anomaly.word}"'
                + (f' (suggested: "{anomaly.suggested_correction}")' if anomaly.suggested_correction else "")
                + "."
            ),
        }
        for anomaly in result.anomalies
    ]
    return _result("spelling_tool", _with_ids("spelling_tool", findings))


@tool
async def date_consistency_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Checks the dates extracted from the document for logical problems: dates in the future, an expiry before its issue date, a certificate dated before the company existed, a birth date after issue."""
    document = await _document(document_id)
    if document is None:
        return _result("date_consistency_tool", [], unable="Document not found")

    fields = await _extracted_fields(document_id)
    result = await asyncio.to_thread(
        check_date_anomalies, document_id, fields, document.document_type
    )
    if result.unable_to_assess:
        return _result(
            "date_consistency_tool", [], unable=result.unable_to_assess_reason or "Unable to assess"
        )

    findings = [
        {
            "check_code": finding.check_code,
            "severity_hint": finding.severity,
            "field": finding.field,
            "stated": finding.stated,
            "expected": finding.expected,
            "needs_review": finding.needs_review,
            "summary": finding.summary,
        }
        for finding in result.findings
    ]
    return _result("date_consistency_tool", _with_ids("date_consistency_tool", findings))


# --- Digital Authenticity ---------------------------------------------------


@tool
async def metadata_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Inspects the file's embedded metadata (author, creation/modification dates, producing software) for signs of editing: consumer editing software in the producer chain, or a modification date well after creation."""
    document = await _document(document_id)
    if document is None:
        return _result("metadata_tool", [], unable="Document not found")

    result = check_metadata_anomalies(document_id, document.metadata, document.document_type)
    if result.unable_to_assess:
        return _result("metadata_tool", [], unable=result.unable_to_assess_reason or "Unable to assess")

    findings = [
        {
            "field": anomaly.field,
            "value": anomaly.value,
            "reason": anomaly.reason,
            "severity_hint": anomaly.severity,
            "summary": f"{anomaly.field}: {anomaly.reason}",
        }
        for anomaly in result.anomalies
    ]
    return _result("metadata_tool", _with_ids("metadata_tool", findings))


def _with_indicator(indicator: str, summary: str) -> str:
    # The image checks' findings carry an AC-defined category (Visible
    # Boundary, Resolution Mismatch, ...). The report only shows each
    # finding's summary, so the category leads it.
    return f"[{indicator}] {summary}"


@tool
async def image_manipulation_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Compares the resolution and compression of the images in the file against each other — the images embedded in a PDF, or a standalone PNG/JPEG upload — and checks each for signs of having been enlarged. A region drawn at a very different DPI, compressed differently, or pasted over an original image is a classic sign of a spliced-in photo, signature or figure. Works on PDFs and image uploads."""
    file_bytes = await _file_bytes(document_id)
    if file_bytes is None:
        return _result("image_manipulation_tool", [], unable="Document file is not available in storage")

    result = await run_cpu(check_image_manipulation, document_id, file_bytes)
    if result.unable_to_assess:
        return _result(
            "image_manipulation_tool", [], unable=result.unable_to_assess_reason or "Unable to assess"
        )

    # `evidence` is left out: it's raw measurement detail for the on-demand
    # endpoint, and detected/expected/deviation already state the finding.
    findings = [
        {
            "indicator": indicator.indicator,
            "page": indicator.page + 1,
            "bbox": indicator.bbox,
            "bbox_units": result.bbox_units,
            "detected": indicator.detected,
            "expected": indicator.expected,
            "deviation": indicator.deviation,
            "severity_hint": indicator.severity,
            "summary": _with_indicator(indicator.indicator, indicator.summary),
        }
        for indicator in result.indicators
    ]
    return _result("image_manipulation_tool", _with_ids("image_manipulation_tool", findings))


@tool
async def image_tampering_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Looks for images edited into the file after it was produced: an image painted over with a replacement drawn on top (the original is often still in the file underneath), a replacement stretched, squashed or rotated out of proportion, a visible seam or halo around a pasted region, or a replacement whose brightness, colour, sharpness or noise differs from the original it hides. Works on PDFs and image uploads; on an image upload only pasted-region seams can be checked."""
    file_bytes = await _file_bytes(document_id)
    if file_bytes is None:
        return _result("image_tampering_tool", [], unable="Document file is not available in storage")

    result = await run_cpu(check_image_tampering, document_id, file_bytes)
    if result.unable_to_assess:
        return _result(
            "image_tampering_tool", [], unable=result.unable_to_assess_reason or "Unable to assess"
        )

    # `evidence` left out for the same reason as image_manipulation_tool.
    findings = [
        {
            "indicator": indicator.indicator,
            "page": indicator.page + 1,
            "bbox": indicator.bbox,
            "bbox_units": result.bbox_units,
            "detected": indicator.detected,
            "expected": indicator.expected,
            "deviation": indicator.deviation,
            "severity_hint": indicator.severity,
            "summary": _with_indicator(indicator.indicator, indicator.summary),
        }
        for indicator in result.indicators
    ]
    return _result("image_tampering_tool", _with_ids("image_tampering_tool", findings))


# --- Digital Authenticity: AI generation --------------------------------------


def _top_contributors(indicators: list[AIIndicator], limit: int = 3) -> list[AIIndicator]:
    contributing = [i for i in indicators if i.contribution > 0]
    return sorted(contributing, key=lambda i: i.contribution, reverse=True)[:limit]


def _ai_image_summary(image: AIImageAssessment) -> str:
    where = f"Image on page {image.page + 1}" if image.page is not None else "Uploaded image"
    drivers = "; ".join(
        f"{i.indicator} +{i.contribution:.2f} ({i.detail})" for i in _top_contributors(image.indicators)
    )
    return f"{where} scores {image.score:.2f} ({image.likelihood}) for AI generation: {drivers}"


@tool
async def ai_generation_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Scores the photographs, scans and image uploads in the file for traces of AI generation: periodic upsampling peaks in the image spectrum, too little or unnatural sensor noise, compression history (double compression, error level analysis, quantisation tables), missing camera metadata, and any provenance the file declares (C2PA manifest, IPTC digital source type, a generator's own tags). Reports images scoring medium or high, each with the indicators that drove its score. Logos and other flat graphics are not scored. Works on PDFs (their embedded images) and image uploads."""
    file_bytes = await _file_bytes(document_id)
    if file_bytes is None:
        return _result("ai_generation_tool", [], unable="Document file is not available in storage")

    result = await run_cpu(check_ai_generation, document_id, file_bytes)
    if result.unable_to_assess:
        return _result("ai_generation_tool", [], unable=result.unable_to_assess_reason or "Unable to assess")

    def contributors(indicators: list[AIIndicator]) -> list[dict]:
        return [
            {"indicator": i.indicator, "value": i.value, "contribution": i.contribution}
            for i in _top_contributors(indicators)
        ]

    # Low scores are not findings — like every other tool, a clean result is
    # an empty list. The full per-indicator breakdown stays on the on-demand
    # endpoint.
    findings = [
        {
            "indicator": "AI Generation",
            "page": image.page + 1 if image.page is not None else None,
            "bbox": image.bbox,
            "bbox_units": result.bbox_units,
            "score": image.score,
            "contributors": contributors(image.indicators),
            "severity_hint": image.likelihood,
            "summary": _with_indicator("AI Generation", _ai_image_summary(image)),
        }
        for image in result.images
        if image.likelihood != "low"
    ]
    provenance = result.file_provenance
    if provenance is not None and provenance.assessed and provenance.strength >= result.config.medium_threshold:
        findings.append({
            "indicator": "AI Generation",
            "score": provenance.strength,
            "severity_hint": "high" if provenance.strength >= result.config.high_threshold else "medium",
            "summary": _with_indicator("AI Generation", f"The file's own metadata declares an AI source: {provenance.detail}"),
        })
    return _result("ai_generation_tool", _with_ids("ai_generation_tool", findings))
