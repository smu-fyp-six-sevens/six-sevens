"""
Public entry points for the document pipeline: process_document (the full
classify -> extract graph) and rerun_extraction (extraction only, after an
officer corrects the document type). Both always leave the document at
ANALYZED or FAILED and never raise, so queue_service's worker loop and the
router's background task don't need their own retry/fallback logic.
"""

from __future__ import annotations

import asyncio
import logging

from . import extract, nodes, text_cache
from .graph import get_document_graph
from ..config import settings
from .. import case_status, document_repository, telemetry
from ..models import DocumentStatus

logger = logging.getLogger("classifier_pipeline.langgraph.pipeline")


async def process_document(document_id: str) -> None:
    """
    Runs the langgraph document graph for one document. Any exception the
    graph doesn't already translate into a FAILED status (e.g. an unexpected
    Supabase write error) is caught here so the document never gets stuck at
    a non-terminal status, which would leave the frontend polling forever.
    """
    graph = get_document_graph()
    span = telemetry.get_tracer().start_as_current_span("langgraph.process_document")
    with span as current_span:
        current_span.set_attribute("document_id", document_id)
        try:
            await graph.ainvoke(
                {"document_id": document_id, "attempts": 0},
                config={"recursion_limit": settings.LANGGRAPH_RECURSION_LIMIT},
            )
        except Exception as exc:  # noqa: BLE001 — last line of defence against a stuck document
            logger.exception("Document %s: unhandled error during langgraph orchestration", document_id)
            text_cache.discard(document_id)
            try:
                await nodes.fail_document(document_id, f"Unhandled langgraph orchestration error: {exc}")
            except Exception:
                logger.exception(
                    "Document %s: failed to record FAILED status after unhandled langgraph error", document_id
                )


async def rerun_extraction(document_id: str) -> None:
    """
    Re-runs Extraction only, against the document's already-stored
    document_type — called from routers/documents.py's PATCH
    .../document-type handler, only when the officer actually changed the
    type (a confirmed-unchanged type has nothing new for Extraction to
    re-read; see that handler).
    """
    with telemetry.get_tracer().start_as_current_span("langgraph.rerun_extraction") as span:
        span.set_attribute("document_id", document_id)
        try:
            await _rerun_extraction(document_id)
        except Exception as exc:  # noqa: BLE001 — last line of defence against a stuck document
            logger.exception("Document %s: unhandled error during langgraph extraction rerun", document_id)
            text_cache.discard(document_id)
            try:
                await nodes.fail_document(document_id, f"Unhandled langgraph orchestration error: {exc}")
            except Exception:
                logger.exception(
                    "Document %s: failed to record FAILED status after unhandled langgraph error", document_id
                )


async def _rerun_extraction(document_id: str) -> None:
    document = await asyncio.to_thread(document_repository.get_document, document_id)
    if document is None:
        logger.error("Document %s not found; cannot rerun extraction", document_id)
        return

    document_type = document.document_type or "unrecognised"

    try:
        raw_text = await nodes.ingest_document_text(document_id, document)
    except nodes.IngestError as exc:
        await nodes.fail_document(document_id, str(exc), document_type=document_type)
        return
    finally:
        text_cache.discard(document_id)

    if not raw_text.strip():
        await nodes.fail_document(
            document_id, "Extraction produced no usable text", document_type=document_type
        )
        return

    extraction = await extract.run_extraction(document_type, raw_text)
    # Persisted regardless of escalation, same as the main graph's extract
    # node — whatever fields Extraction found are real data worth keeping.
    await nodes.persist_extracted_fields(document_id, document_type, extraction)

    if extraction.get("escalation"):
        await nodes.fail_document(
            document_id,
            f"Extraction could not process this as {document_type}: "
            f"{extraction['escalation'].get('reason')}",
            document_type=document_type,
        )
        return

    # failure_reason="" clears a stale reason left by whatever FAILED run
    # this rerun is recovering from (update_status skips None, not "").
    await asyncio.to_thread(
        document_repository.update_status,
        document_id,
        DocumentStatus.ANALYZED,
        document_type=document_type,
        failure_reason="",
    )
    if document.case_id is not None:
        await asyncio.to_thread(case_status.sync_submission_finalized, document.case_id)
    logger.info("Document %s: langgraph extraction rerun complete as %s", document_id, document_type)
