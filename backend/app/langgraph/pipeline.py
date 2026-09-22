"""
Public entry point for the langgraph experiment — mirrors
orchestrator.process_document's contract (always leaves the document at
ANALYZED or FAILED, and never raises) so queue_service can call it as a
drop-in replacement while phases 0-3 are evaluated. See
LANGGRAPH_MIGRATION_PLAN.md.
"""

from __future__ import annotations

import logging

from . import text_cache
from .graph import get_document_graph
from ..config import settings
from .. import orchestrator

logger = logging.getLogger("classifier_pipeline.langgraph.pipeline")


async def process_document(document_id: str) -> None:
    """
    Runs the langgraph document graph for one document. Never raises — same
    "always ends ANALYZED or FAILED" contract as orchestrator.process_document,
    so queue_service's worker loop doesn't need its own retry/fallback logic
    around this call either.
    """
    graph = get_document_graph()
    try:
        await graph.ainvoke(
            {"document_id": document_id, "attempts": 0},
            config={"recursion_limit": settings.LANGGRAPH_RECURSION_LIMIT},
        )
    except Exception as exc:  # noqa: BLE001 — last line of defence against a stuck document
        logger.exception("Document %s: unhandled error during langgraph orchestration", document_id)
        text_cache.discard(document_id)
        try:
            await orchestrator._fail(document_id, f"Unhandled langgraph orchestration error: {exc}")
        except Exception:
            logger.exception(
                "Document %s: failed to record FAILED status after unhandled langgraph error", document_id
            )
