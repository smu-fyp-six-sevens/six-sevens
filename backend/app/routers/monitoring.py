##this document and the whole monitoring will need to be reevaluated, will need a retry endpoint to be developed later for this to even have a point

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from .. import document_repository
from ._dependencies import bad_gateway_on_error
from ..models import DocumentRecord, DocumentStatus, ProcessingErrorEntry, StuckDocument
from ..monitoring import get_stuck_documents
from ..queue_service import document_queue

router = APIRouter(prefix="/documents", tags=["monitoring"])
logger = logging.getLogger(__name__)


class MonitoringIssues(BaseModel):
    stuck: list[StuckDocument]
    failed: list[DocumentRecord]
    recent_errors: list[ProcessingErrorEntry]


@router.get("/monitoring/stuck", response_model=list[StuckDocument])
def list_stuck_documents() -> list[StuckDocument]:
    """AC4: surfaces documents stuck in uploaded/queued/processing past the threshold."""
    with bad_gateway_on_error(
        "Failed to compute stuck documents. Check Supabase table access.",
        "Failed to compute stuck documents",
    ):
        return get_stuck_documents()


@router.get("/monitoring/issues", response_model=MonitoringIssues)
def list_monitoring_issues() -> MonitoringIssues:
    """
    Single place to check what needs attention: stuck documents, documents
    that exhausted retries (status=failed), and pipeline-level errors that
    have no document row to attach to (e.g. a dequeued document whose row
    vanished) — instead of having to know to filter for each separately.
    """
    with bad_gateway_on_error(
        "Failed to compute monitoring issues. Check Supabase table access.",
        "Failed to compute monitoring issues",
    ):
        stuck = get_stuck_documents()
        failed = document_repository.get_documents_in_status([DocumentStatus.FAILED])

    return MonitoringIssues(
        stuck=stuck,
        failed=failed,
        recent_errors=document_queue.get_recent_errors(),
    )