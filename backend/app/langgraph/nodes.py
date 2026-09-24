"""
Deterministic nodes (Phases 1 + 4) — ingest, finalise classification,
extract, persist, fail, and the bounded classify<->extract reclassification
loop (record_escalation_node — mirrors orchestrator.MAX_RECLASSIFICATION_ATTEMPTS).
Each reuses the matching helper already in orchestrator.py / the
repositories rather than re-implementing repository writes, so both the
Dify path and this one keep behaving identically until one of them is
retired (see LANGGRAPH_MIGRATION_PLAN.md).
"""

from __future__ import annotations

import asyncio
import logging

from . import extract, text_cache, trace
from .state import DocumentGraphState
from .. import case_status, document_repository, orchestrator
from ..config import settings
from ..models import DocumentStatus

logger = logging.getLogger("classifier_pipeline.langgraph.nodes")


async def ingest_node(state: DocumentGraphState) -> dict:
    document_id = state["document_id"]
    trace.start(document_id)
    document = await asyncio.to_thread(document_repository.get_document, document_id)
    if document is None:
        trace.record(document_id, "ingest", error=f"Document {document_id} not found")
        return {"status": "failed", "failure_reason": f"Document {document_id} not found"}

    try:
        raw_text = await orchestrator._ingest_document_text(document_id, document)
    except orchestrator.OrchestrationError as exc:
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
    classification = dict(state["classification"])
    await orchestrator._finalise_classification(state["document_id"], state.get("case_id"), classification)
    # _finalise_classification may normalise document_type (e.g. unnamed ->
    # "unrecognised") in place — write the normalised dict back to state so
    # extract_node runs against the same type that was actually persisted.
    trace.record(
        state["document_id"],
        "classification_finalised",
        document_type=classification.get("document_type"),
        confidence=classification.get("confidence"),
        needs_manual_review=classification.get("needs_manual_review"),
        # Shown in the langgraph debug trace only — not persisted to Supabase
        # for now (see orchestrator._finalise_classification).
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
    document_type = state["classification"]["document_type"]
    raw_text = text_cache.get(document_id) or ""

    await orchestrator._set_status(document_id, DocumentStatus.EXTRACTING)
    extraction = await extract.run_extraction(document_type, raw_text)

    # Persisted regardless of escalation — whatever fields Extraction
    # actually found are real data, worth keeping even if the document ends
    # up FAILED (see orchestrator._persist_extracted_fields's own docstring).
    await orchestrator._persist_extracted_fields(document_id, document_type, extraction)

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
    Phase 4: Extraction judged the classified type wrong and there's still a
    reclassify attempt left (see graph.py's decide_after_extract). Persisted
    before the reclassify runs (not just logged) so the frontend can show
    "classification was re-run" regardless of what it eventually resolves
    to — mirrors orchestrator._process_document's escalation loop exactly.
    """
    document_id = state["document_id"]
    escalation = (state.get("extraction") or {}).get("escalation") or {}
    attempts = state.get("attempts", 0) + 1

    await asyncio.to_thread(document_repository.record_escalation, document_id, escalation.get("reason", ""))
    await orchestrator._set_status(document_id, DocumentStatus.PROCESSING)
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
    them, escalation or not) — only the terminal status/reason is set here,
    mirroring orchestrator._process_document's own final message.
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
    document_type = state["classification"]["document_type"]
    await asyncio.to_thread(
        document_repository.update_status, document_id, DocumentStatus.ANALYZED, document_type=document_type
    )
    if state.get("case_id") is not None:
        await asyncio.to_thread(case_status.sync_submission_finalized, state["case_id"])
    text_cache.discard(document_id)
    trace.record(document_id, "done", final_status="analyzed")
    logger.info("Document %s: langgraph classification+extraction complete", document_id)
    return {"status": "analyzed"}


async def fail_node(state: DocumentGraphState) -> dict:
    document_id = state["document_id"]
    reason = state.get("failure_reason") or "Unknown failure in langgraph pipeline"
    document_type = (state.get("classification") or {}).get("document_type")
    await orchestrator._fail(document_id, reason, document_type=document_type)
    text_cache.discard(document_id)
    trace.record(document_id, "done", final_status="failed", failure_reason=reason)
    return {"status": "failed"}
