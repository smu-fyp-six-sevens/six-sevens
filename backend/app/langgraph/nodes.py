"""
Deterministic graph nodes — ingest, finalise classification, extract,
persist, fail, and the bounded classify<->extract reclassification loop
(record_escalation_node, capped by settings.MAX_RECLASSIFICATION_ATTEMPTS).

The repository-writing helpers (status, classification, extracted fields,
failure) live here too, next to the nodes that call them; pipeline.py reuses
them for its extraction-only rerun.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes

from . import extract, text_cache, trace
from .state import DocumentGraphState
from .. import document_repository, extracted_fields_repository, storage_service
from ..config import settings
from .extraction_prompts import schema_field_names
from ..ingestion.ingestion_service import ingest_document
from ..models import ClassificationOutcome, DocumentStatus, compute_fulfilled_requirements

logger = logging.getLogger("classifier_pipeline.langgraph.nodes")


class IngestError(Exception):
    """Raised when a document's file can't be downloaded or its text can't be extracted at all."""


# --- repository-writing helpers ---------------------------------------------


async def set_status(document_id: str, status: DocumentStatus) -> None:
    await asyncio.to_thread(document_repository.update_status, document_id, status)


async def fail_document(document_id: str, reason: str, *, document_type: str | None = None) -> None:
    await asyncio.to_thread(
        document_repository.update_status,
        document_id,
        DocumentStatus.FAILED,
        failure_reason=reason,
        document_type=document_type,
    )
    logger.warning("Document %s marked FAILED — manual review required: %s", document_id, reason)


async def persist_extracted_fields(document_id: str, document_type: str, extraction: dict) -> None:
    """
    Writes Extraction's output to both the jsonb blob on documents (what
    the frontend reads) and the normalised one-row-per-field table (for
    querying individual fields). Shared by the analyzed and failed paths,
    since a failed escalation shouldn't discard whatever Extraction found.
    """
    extracted_fields = extraction.get("extracted_fields", {})
    field_confidences = extraction.get("field_confidences", {})
    extraction_confidence = extraction.get("confidence", 0.0)
    # Recomputed here (not left as whatever classification recorded) since
    # this is the point individual_address is actually known — an NRIC only
    # turns out to also fulfil proof_of_address once Extraction has run.
    fulfilled_requirements = compute_fulfilled_requirements(document_type, extracted_fields)
    await asyncio.to_thread(
        document_repository.update_extracted_fields,
        document_id,
        extraction_confidence,
        fulfilled_requirements,
    )
    await asyncio.to_thread(
        extracted_fields_repository.replace_extracted_fields,
        document_id,
        extracted_fields,
        field_confidences=field_confidences,
        default_confidence=extraction_confidence,
        required_field_names=set(schema_field_names(document_type)),
    )


def _content_type_from_filename(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


async def ingest_document_text(document_id: str, document) -> str:
    """
    Downloads the stored file and runs text extraction (native PDF text, or
    OCR for scans/images) once, up front — before classification, so the
    agent can decide for itself whether to fall back to vision. Deliberately
    does NOT raise when the text comes back empty: an image-only document
    with nothing OCR can read is exactly the case vision exists for. The
    empty-text check happens right before Extraction (check_text_available).
    """
    try:
        file_bytes = await asyncio.to_thread(storage_service.download_file, document.storage_path)
    except Exception as exc:
        raise IngestError(f"Failed to download document for extraction: {exc}") from exc

    content_type = _content_type_from_filename(document.filename)

    try:
        ingestion_result = await asyncio.to_thread(
            ingest_document, file_bytes, document.filename, content_type
        )
    except Exception as exc:
        raise IngestError(f"Text extraction failed: {exc}") from exc

    # Persisted regardless of whether the text turns out usable — file
    # metadata (author, creation date, software, ...) is independent of text
    # extraction quality.
    if ingestion_result["metadata"]:
        await asyncio.to_thread(
            document_repository.update_metadata, document_id, ingestion_result["metadata"]
        )

    if ingestion_result["needs_review"]:
        logger.warning(
            "Document %s: extraction flagged needs_review (method=%s)",
            document_id, ingestion_result["extraction_method"],
        )

    return ingestion_result["raw_text"]


def _is_unrecognised(document_type: str | None) -> bool:
    return document_type in (None, "", "unrecognised")


def _is_below_threshold(confidence: float | None) -> bool:
    return confidence is None or confidence < settings.CLASSIFICATION_CONFIDENCE_THRESHOLD


async def record_classification_result(document_id: str, classification: dict) -> None:
    """
    Records the classification-stage result (type, confidence, outcome) and
    enforces the confidence threshold as a system-level gate — the agent's
    own needs_manual_review flag is OR'd in as an extra signal.
    document_repository.record_classification resyncs the case's
    submission_finalized flag itself, since this is where a document's
    fulfilled_requirements (and so the case's coverage) first changes.

    Extraction always runs after this. A genuinely typeless response is
    normalised to "unrecognised" (general field list). A *named* but
    unconfident guess is kept as-is so Extraction runs against that type's
    own schema and the frontend can show what the classifier thinks it is;
    classification_outcome still records MANUAL_REVIEW for it.
    """
    document_type = classification.get("document_type")
    confidence = classification.get("confidence")
    unnamed = _is_unrecognised(document_type)
    accepted = (
        not unnamed
        and not _is_below_threshold(confidence)
        and not classification.get("needs_manual_review")
    )
    if unnamed:
        # Written back into the dict (not just a local) so the caller's later
        # `classification["document_type"]` reads see the normalised value.
        document_type = "unrecognised"
        classification["document_type"] = document_type

    outcome = ClassificationOutcome.ACCEPTED if accepted else ClassificationOutcome.MANUAL_REVIEW
    # Self-fulfilment only at this stage — individual_address isn't known
    # until Extraction runs, so proof_of_address cross-fulfilment gets
    # recomputed by persist_extracted_fields afterwards.
    await asyncio.to_thread(
        document_repository.record_classification,
        document_id,
        document_type=document_type,
        confidence=confidence,
        classification_outcome=outcome.value,
        evidence=classification.get("evidence", []),
        fulfilled_requirements=compute_fulfilled_requirements(document_type, None),
        # reasoning intentionally not persisted for now — it goes into the
        # langgraph trace instead (see finalise_classification_node).
        # document_repository.record_classification still accepts it, so
        # this is a one-line flip later.
    )


# --- nodes ------------------------------------------------------------------


async def ingest_node(state: DocumentGraphState) -> dict:
    document_id = state["document_id"]
    trace.start(document_id)
    document = await asyncio.to_thread(
        document_repository.get_document, document_id, include_fields=False
    )
    if document is None:
        trace.record(document_id, "ingest", error=f"Document {document_id} not found")
        return {"status": "failed", "failure_reason": f"Document {document_id} not found"}

    try:
        raw_text = await ingest_document_text(document_id, document)
    except IngestError as exc:
        trace.record(document_id, "ingest", case_id=document.case_id, error=str(exc))
        return {"status": "failed", "failure_reason": str(exc)}

    text_hash = text_cache.put(document_id, raw_text)
    trace.record(document_id, "ingest", case_id=document.case_id, filename=document.filename, text_available=bool(raw_text.strip()))
    return {
        "case_id": document.case_id,
        "filename": document.filename,
        "storage_path": document.storage_path,
        "text_hash": text_hash,
        "text_available": bool(raw_text.strip()),
    }


async def finalise_classification_node(state: DocumentGraphState) -> dict:
    # classify subgraph always sets this before finalise_classification_node
    # runs (see graph.py's edges) — never actually None/missing here.
    raw_classification = state.get("classification")
    assert raw_classification is not None
    classification = dict(raw_classification)
    await record_classification_result(state["document_id"], classification)
    # record_classification_result may normalise document_type (unnamed ->
    # "unrecognised") in place — write the normalised dict back to state so
    # extract_node runs against the same type that was actually persisted.
    trace.record(
        state["document_id"],
        "classification_finalised",
        document_type=classification.get("document_type"),
        confidence=classification.get("confidence"),
        needs_manual_review=classification.get("needs_manual_review"),
        # Shown in the langgraph trace only — not persisted to the documents
        # table for now (see record_classification_result).
        classification_reasoning=classification.get("reasoning"),
    )
    return {"classification": classification}


async def check_text_available(state: DocumentGraphState) -> dict:
    if state.get("text_available"):
        return {}
    return {
        "status": "failed",
        "failure_reason": "Extraction produced no usable text",
    }


async def extract_node(state: DocumentGraphState) -> dict:
    document_id = state["document_id"]
    # Set by finalise_classification_node just before this runs, and always
    # normalised to a real type string there (see record_classification_result).
    classification = state.get("classification")
    assert classification is not None
    document_type = classification["document_type"]
    assert document_type is not None
    raw_text = text_cache.get(document_id) or ""

    await set_status(document_id, DocumentStatus.EXTRACTING)
    extraction = await extract.run_extraction(document_type, raw_text)

    # Persisted regardless of escalation — whatever fields Extraction
    # actually found are real data, worth keeping even if the document ends
    # up FAILED.
    await persist_extracted_fields(document_id, document_type, extraction)

    summary = {
        "confidence": extraction.get("confidence", 0.0),
        "field_count": len(extraction.get("extracted_fields") or {}),
        "escalation": extraction.get("escalation"),
    }
    trace.record(document_id, "extraction", document_type=document_type, **summary)
    # Whether an escalation here triggers a reclassify or a final FAILED is
    # graph.py's call (it also knows `attempts` vs MAX_RECLASSIFICATION_ATTEMPTS) —
    # this node only ever reports what Extraction found.
    return {"extraction": summary}


async def record_escalation_node(state: DocumentGraphState) -> dict:
    """
    Extraction judged the classified type wrong and there's still a
    reclassify attempt left (see graph.py's decide_after_extract). Persisted
    before the reclassify runs (not just logged) so the frontend can show
    "classification was re-run" regardless of what it eventually resolves to.
    """
    document_id = state["document_id"]
    escalation = (state.get("extraction") or {}).get("escalation") or {}
    attempts = state.get("attempts", 0) + 1

    await asyncio.to_thread(document_repository.record_escalation, document_id, escalation.get("reason", ""))
    await set_status(document_id, DocumentStatus.PROCESSING)
    logger.info(
        "Document %s: Extraction escalated classification (%s) — attempt %d/%d",
        document_id, escalation.get("reason"), attempts, settings.MAX_RECLASSIFICATION_ATTEMPTS,
    )
    trace.record(document_id, "record_escalation", attempts=attempts, reason=escalation.get("reason"))
    return {"attempts": attempts}


async def mark_unresolved_escalation_node(state: DocumentGraphState) -> dict:
    """
    The reclassify loop ran out of attempts (settings.MAX_RECLASSIFICATION_ATTEMPTS)
    with Extraction still objecting to the classified type. Whatever fields
    Extraction found are already persisted (extract_node always persists
    them) — only the terminal status/reason is set here.
    """
    escalation = (state.get("extraction") or {}).get("escalation") or {}
    return {
        "status": "failed",
        "failure_reason": (
            f"Unresolved after {settings.MAX_RECLASSIFICATION_ATTEMPTS} reclassification "
            f"attempt(s): {escalation.get('reason')}"
        ),
    }


async def mark_analyzed_node(state: DocumentGraphState) -> dict:
    document_id = state["document_id"]
    classification = state.get("classification")
    assert classification is not None
    document_type = classification["document_type"]
    assert document_type is not None
    await asyncio.to_thread(
        document_repository.update_status, document_id, DocumentStatus.ANALYZED, document_type=document_type
    )
    text_cache.discard(document_id)
    trace.record(document_id, "done", final_status="analyzed")
    logger.info("Document %s: langgraph classification+extraction complete", document_id)
    return {"status": "analyzed"}


async def fail_node(state: DocumentGraphState) -> dict:
    document_id = state["document_id"]
    reason = state.get("failure_reason") or "Unknown failure in langgraph pipeline"
    document_type = (state.get("classification") or {}).get("document_type")
    await fail_document(document_id, reason, document_type=document_type)
    text_cache.discard(document_id)
    trace.record(document_id, "done", final_status="failed", failure_reason=reason)
    return {"status": "failed"}
