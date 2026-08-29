from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from .. import document_repository
from ..models import DocumentStatus, MonitoringIssues, StuckDocument
from ..monitoring import get_stuck_documents
from ..queue_service import document_queue

router = APIRouter(prefix="/documents", tags=["monitoring"])
logger = logging.getLogger(__name__)


@router.get("/monitoring/stuck", response_model=list[StuckDocument])
def list_stuck_documents() -> list[StuckDocument]:
    """AC4: surfaces documents stuck in uploaded/queued/processing past the threshold."""
    try:
        return get_stuck_documents()
    except Exception as exc:
        logger.exception("Failed to compute stuck documents")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to compute stuck documents. Check Supabase table access.",
        ) from exc


@router.get("/monitoring/issues", response_model=MonitoringIssues)
def list_monitoring_issues() -> MonitoringIssues:
    """
    Single place to check what needs attention: stuck documents, documents
    that exhausted retries (status=failed), and pipeline-level errors that
    have no document row to attach to (e.g. a dequeued document whose row
    vanished) — instead of having to know to filter for each separately.
    """
    try:
        stuck = get_stuck_documents()
        failed = document_repository.get_documents_in_status([DocumentStatus.FAILED])
    except Exception as exc:
        logger.exception("Failed to compute monitoring issues")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to compute monitoring issues. Check Supabase table access.",
        ) from exc

    return MonitoringIssues(
        stuck=stuck,
        failed=failed,
        recent_errors=document_queue.get_recent_errors(),
    )