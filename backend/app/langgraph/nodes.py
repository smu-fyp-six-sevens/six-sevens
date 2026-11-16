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

from . import extract, text_cache, trace
from .state import DocumentGraphState
from .. import document_repository, extracted_fields_repository, metrics, storage_service
from ..config import settings
from ..evals.extraction import field_shape
from ..guardrails import content_guard
from ..ingestion.ingestion_service import ingest_document, content_type_from_filename
from ..ingestion.metadata_extractor import EXTRACTION_ERROR_KEY, extract_metadata
from ..cpu_pool import run_cpu
from ..models import DocumentStatus, RoutingStatus, compute_fulfilled_requirements

logger = logging.getLogger("classifier_pipeline.langgraph.nodes")


class IngestError(Exception):
    """Raised when a document's file can't be downloaded or its text can't be extracted at all."""


class RunStopped(Exception):
    """The document left the status this run expected (an officer's cancel, a recovery, a delete): stop quietly."""


# The statuses a document pipeline run owns: anything else was set by someone else.
_RUNNING = [DocumentStatus.PROCESSING, DocumentStatus.EXTRACTING]


# --- repository-writing helpers ---------------------------------------------


async def set_status(
    document_id: str, status: DocumentStatus, *, expected: list[DocumentStatus], document_type: str | None = None,
) -> None:
    """A compare-and-swap: raises RunStopped when the document is no longer in one of `expected`."""
    moved = await asyncio.to_thread(
        document_repository.update_status, document_id, status,
        document_type=document_type, expected_current_statuses=expected,
    )
    if not moved:
        raise RunStopped(f"Document {document_id} left {[s.value for s in expected]} before it could become {status.value}")


async def fail_document(document_id: str, reason: str, *, document_type: str | None = None) -> None:
    failed = await asyncio.to_thread(
        document_repository.update_status,
        document_id,
        DocumentStatus.FAILED,
        failure_reason=reason,
        document_type=document_type,
        expected_current_statuses=_RUNNING,
    )
    if not failed:
        logger.info("Document %s left its run before it could be marked failed (%s)", document_id, reason)
        return
    logger.warning("Document %s marked FAILED — manual review required: %s", document_id, reason)


async def persist_extracted_fields(document_id: str, document_type: str, extraction: dict) -> None:
    """
    Writes Extraction's output to both the jsonb blob on documents (what
    the frontend reads) and the normalised one-row-per-field table (for
    querying individual fields). Shared by the analyzed and failed paths,
    since a failed escalation shouldn't discard whatever Extraction found.
    """
    if extraction.get("output_error") and not extraction.get("extracted_fields"):
        return  # nothing usable came back; keep whatever was stored before rather than wiping it
    extracted_fields = extraction.get("extracted_fields", {})
    field_confidences = extraction.get("field_confidences", {})
    extraction_confidence = extraction.get("confidence", 0.0)
    # Recomputed here (not left as whatever classification recorded) since
    # this is the point individual_address is actually known — an NRIC only
    # turns out to also fulfil proof_of_address once Extraction has run.
    fulfilled_requirements = compute_fulfilled_requirements(document_type, extracted_fields)
    # Fields first: the documents update fires a live snapshot, which must already see the new fields.
    written = await asyncio.to_thread(
        extracted_fields_repository.replace_extracted_fields,
        document_id,
        extracted_fields,
        field_confidences=field_confidences,
        default_confidence=extraction_confidence,
        expected_statuses=_RUNNING,
    )
    if not written:
        # Cancelled or replaced since the run started: it mustn't overwrite the fields.
        raise RunStopped(f"Document {document_id} left its run; its fields were not written")
    await asyncio.to_thread(
        document_repository.update_extracted_fields,
        document_id,
        extraction_confidence,
        fulfilled_requirements,
    )


async def ingest_document_text(document_id: str, document) -> tuple[str, dict]:
    """
    The text the models will read, and the content guard's result for the document (see _guard_document).

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

    content_type = content_type_from_filename(document.filename)

    # exiftool is a subprocess: run it beside the text extraction, not inside the one PDF worker.
    extracted, metadata = await asyncio.gather(
        run_cpu(ingest_document, file_bytes, document.filename, content_type, with_metadata=False),
        asyncio.to_thread(extract_metadata, file_bytes, document.filename),
        return_exceptions=True,
    )
    if isinstance(extracted, BaseException):
        raise IngestError(f"Text extraction failed: {extracted}") from extracted
    ingestion_result = extracted
    if isinstance(metadata, BaseException):
        logger.error("Metadata extraction failed for %s: %s", document.filename, metadata)
        metadata = {EXTRACTION_ERROR_KEY: f"metadata could not be read ({type(metadata).__name__})"}

    if ingestion_result["needs_review"]:
        logger.warning(
            "Document %s: extraction flagged needs_review (method=%s)",
            document_id, ingestion_result["extraction_method"],
        )

    return await _guard_document(
        document_id, file_bytes, content_type, ingestion_result["raw_text"], metadata, document.filename,
    )


async def _guard_document(
    document_id: str, file_bytes: bytes, content_type: str, raw_text: str, metadata: dict | None,
    filename: str | None = None,
) -> tuple[str, dict]:
    """
    Layer 1 of the guardrails (guardrails/content_guard.py): records what the content guard found as the
    document's guard_result, for detection to report, and returns the text the models will read, with flagged
    hidden text taken out, plus the result. Never fails the ingest itself: a guard that could not run is saved
    with no checks run, which detection reads as unable to assess. Whether the result stops the document is
    the caller's call (guard_block_reason).
    """
    try:
        result, removals = await run_cpu(content_guard.scan, file_bytes, content_type, raw_text, metadata, filename)
    except Exception as exc:
        logger.exception("Document %s: content guard failed", document_id)
        result, removals = content_guard.not_run(f"{type(exc).__name__}: {exc}"), []
    # An officer's "process anyway" lives on the saved result, which this scan is about to replace: carry it over.
    if content_guard.blocking_reason(result):
        previous = await asyncio.to_thread(document_repository.get_guard_result, document_id)
        if previous and previous.get(content_guard.OVERRIDE_KEY):
            result[content_guard.OVERRIDE_KEY] = previous[content_guard.OVERRIDE_KEY]
    metrics.content_guard_results.labels(result["risk"] or "not_run").inc()
    # What was found, not just how much: each span's quote, where it was, why it counts as hidden, which check
    # caught it. Document text, like the rest of the trace. Prefixed, since record() also copies the fields to
    # the top of the trace, where a bare "error" or "risk" would overwrite another step's.
    trace.record(
        document_id, "content_guard", guard_risk=result["risk"], guard_checks_run=result["checks_run"],
        guard_spans=result["spans"], guard_spans_dropped=result["spans_dropped"],
        guard_hidden_spans_removed=len(removals), guard_error=result.get("error"),
    )
    # The file's metadata goes in the same write. Saved whether or not the text turns out usable: it doesn't
    # depend on the text.
    try:
        await asyncio.to_thread(
            document_repository.update_ingest_results, document_id, guard_result=result, metadata=metadata,
        )
    except Exception as exc:
        # Detection then reruns the guard on the file, and reports the metadata as not recorded.
        logger.warning("Document %s: guard_result and metadata not saved: %s", document_id, exc)
    return content_guard.clean_text(raw_text, removals), result


def guard_block_reason(guard: dict) -> str | None:
    """Why the content guard stops this document before classification and extraction, or None."""
    reason = content_guard.blocking_reason(guard) if settings.GUARD_BLOCK_INJECTION else None
    if reason:
        metrics.content_guard_blocks.inc()
    return reason


def _is_unrecognised(document_type: str | None) -> bool:
    return document_type in (None, "", "unrecognised")


def _is_below_threshold(confidence: float | None) -> bool:
    return confidence is None or confidence < settings.CLASSIFICATION_CONFIDENCE_THRESHOLD


# A document the content guard rated this risky is never accepted on the classifier's word alone. Low (instruction
# wording in visible text or the filename, under the score that blocks) included: the officer sees the warning.
_GUARD_REVIEW_RISKS = frozenset({"low", "medium", "high"})


async def record_classification_result(document_id: str, classification: dict, guard_risk: str | None = None) -> None:
    """
    Records the classification-stage result (type, confidence, outcome) and
    enforces the confidence threshold as a system-level gate — the agent's
    own needs_manual_review flag is OR'd in as an extra signal, and so is any
    content guard risk above none (hidden text, or instruction wording anywhere
    that didn't block the document), whatever the classifier was told.
    document_repository.record_classification resyncs the case's stage
    itself, since this is where a document's fulfilled_requirements (and so
    the case's coverage) first changes.

    Extraction always runs after this. A genuinely typeless response is
    normalised to "unrecognised" (general field list). A *named* but
    unconfident guess is kept as-is so Extraction runs against that type's
    own schema and the frontend can show what the classifier thinks it is;
    routing_status still records MANUAL_REVIEW for it.
    """
    document_type = classification.get("document_type")
    confidence = classification.get("confidence")
    unnamed = _is_unrecognised(document_type)
    accepted = (
        not unnamed
        and not _is_below_threshold(confidence)
        and not classification.get("needs_manual_review")
        and guard_risk not in _GUARD_REVIEW_RISKS
    )
    if unnamed:
        # Written back into the dict (not just a local) so the caller's later
        # `classification["document_type"]` reads see the normalised value.
        document_type = "unrecognised"
        classification["document_type"] = document_type

    outcome = RoutingStatus.ACCEPTED if accepted else RoutingStatus.MANUAL_REVIEW
    # Self-fulfilment only at this stage — individual_address isn't known
    # until Extraction runs, so proof_of_address cross-fulfilment gets
    # recomputed by persist_extracted_fields afterwards.
    await asyncio.to_thread(
        document_repository.record_classification,
        document_id,
        document_type=document_type,
        classification_confidence=confidence,
        routing_status=outcome.value,
        evidence=classification.get("evidence", []),
        fulfilled_requirements=compute_fulfilled_requirements(document_type, None),
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
        raw_text, guard = await ingest_document_text(document_id, document)
    except IngestError as exc:
        trace.record(document_id, "ingest", case_id=document.case_id, error=str(exc))
        return {"status": "failed", "failure_reason": str(exc)}

    # Instructions aimed at the AI: stop here, so no model reads the document (the graph routes to fail).
    blocked = guard_block_reason(guard)
    if blocked:
        trace.record(document_id, "ingest", case_id=document.case_id, filename=document.filename, blocked=blocked)
        return {"status": "failed", "failure_reason": blocked, "case_id": document.case_id,
                "filename": document.filename, "guard_risk": guard["risk"]}

    text_hash = text_cache.put(document_id, raw_text)
    trace.record(document_id, "ingest", case_id=document.case_id, filename=document.filename, text_available=bool(raw_text.strip()))
    update: dict = {
        "case_id": document.case_id,
        "filename": document.filename,
        "storage_path": document.storage_path,
        "text_hash": text_hash,
        "text_available": bool(raw_text.strip()),
        "guard_risk": guard["risk"],
    }
    if is_officer_set_type(document):
        # A recovery or retry of a document the officer typed: classifying again would overwrite their decision.
        update["officer_type"] = True
        update["classification"] = {
            "document_type": document.document_type, "confidence": 1.0, "evidence": document.evidence or [],
            "reasoning": "Set by the loan officer.", "needs_manual_review": False,
            "fulfilled_requirements": document.fulfilled_requirements or [],
        }
        trace.record(document_id, "classification_skipped", document_type=document.document_type)
    return update


def is_officer_set_type(document) -> bool:
    """set_type stores the officer's choice at confidence 1.0. A classifier rarely reaches 1.0, and then its type
    was certain, so keeping it is no loss."""
    return bool(document.document_type) and (document.classification_confidence or 0.0) >= 1.0


async def finalise_classification_node(state: DocumentGraphState) -> dict:
    # classify subgraph always sets this before finalise_classification_node
    # runs (see graph.py's edges) — never actually None/missing here.
    raw_classification = state.get("classification")
    assert raw_classification is not None
    classification = dict(raw_classification)
    await record_classification_result(state["document_id"], classification, guard_risk=state.get("guard_risk"))
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

    await set_status(document_id, DocumentStatus.EXTRACTING, expected=_RUNNING)
    # A low-confidence or review-flagged classification is extracted with the
    # generic schema, so the document's fields aren't tied to a guess. The
    # named type is still recorded for the officer to confirm.
    schema_type = (
        "unrecognised"
        if _is_below_threshold(classification.get("confidence")) or classification.get("needs_manual_review")
        else document_type
    )
    # Extracted with the generic schema because the classifier wasn't sure: say what it guessed, so extraction
    # can object. Only on the first pass: after a reclassification the classifier has already been asked again.
    guess = document_type if schema_type == "unrecognised" and document_type != "unrecognised" else None
    extraction = await extract.run_extraction(
        schema_type,
        raw_text,
        classified_as=guess if state.get("attempts", 0) == 0 else None,
        document_id=document_id,
        filename=state.get("filename"),
        storage_path=state.get("storage_path"),
    )

    # Persisted regardless of escalation — whatever fields Extraction
    # actually found are real data, worth keeping even if the document ends
    # up FAILED.
    await persist_extracted_fields(document_id, document_type, extraction)

    summary = {
        "confidence": extraction.get("confidence", 0.0),
        "field_count": len(extraction.get("extracted_fields") or {}),
        "dropped_unprinted_fields": extraction.get("dropped_unprinted_fields", []),
        "dropped_empty_fields": extraction.get("dropped_empty_fields", []),
        "dropped_period_totals": extraction.get("dropped_period_totals", []),
        "dropped_example_values": extraction.get("dropped_example_values", []),
        "escalation": extraction.get("escalation"),
        "output_error": extraction.get("output_error"),
        "output_status": extraction.get("output_status"),
        "retried": extraction.get("retried", False),
        # Why each field is low confidence, the second look's disagreements included: not an extracted_fields
        # column. Shown under the field on the review screen (document-details/fieldReasons.ts).
        "low_confidence_reasons": extraction.get("field_reasons") or {},
        "field_provenance": extraction.get("field_provenance") or {},
        "transaction_table": extraction.get("transaction_table"),
        "second_look": extraction.get("second_look"),
        "extraction_disagreements": extraction.get("extraction_disagreements") or [],
        "second_look_rejected_fields": extraction.get("second_look_rejected_fields") or [],
        "second_look_error": extraction.get("second_look_error"),
        # Field names only, for the run's sensibility checks (evals/extraction.py).
        "field_shape": field_shape(schema_type, extraction.get("extracted_fields")),
    }
    trace.record(document_id, "extraction", document_type=document_type, schema=schema_type, **summary)
    if extraction.get("output_error"):
        # The reply was unusable: that says nothing about the document's type, so it is not reclassified.
        return {
            "extraction": summary,
            "status": "failed",
            "failure_reason": f"Extraction output could not be used: {extraction['output_error']}",
        }
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
    await set_status(document_id, DocumentStatus.PROCESSING, expected=[DocumentStatus.EXTRACTING])
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
    if state.get("officer_type"):
        # Same rule as the officer's retype rerun (pipeline._rerun_extraction): their type isn't reclassified.
        document_type = (state.get("classification") or {}).get("document_type")
        return {
            "status": "failed",
            "failure_reason": f"Extraction could not process this as {document_type}: {escalation.get('reason')}",
        }
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
    await set_status(document_id, DocumentStatus.ANALYZED, expected=_RUNNING, document_type=document_type)
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
