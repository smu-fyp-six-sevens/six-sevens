from __future__ import annotations

from datetime import datetime, timezone

from . import document_repository
from .config import settings
from .models import IN_FLIGHT_STATUSES, DocumentRecord, DocumentStatus, StuckDocument

_STUCK_ELIGIBLE_STATUSES = IN_FLIGHT_STATUSES


def is_stuck(doc: DocumentRecord) -> bool:
    """
    True if `doc` is in UPLOADED, QUEUED, PROCESSING, or EXTRACTING longer than
    STUCK_THRESHOLD_SECONDS with no status update. UPLOADED counts because a
    document can be stored but fail to enqueue, leaving it stuck there
    indefinitely otherwise. Shared with case_service.py's aggregate rollup —
    "what counts as stuck" should only be defined once.
    """
    if doc.status not in _STUCK_ELIGIBLE_STATUSES:
        return False
    reference_time = doc.processing_started_at or doc.queued_at or doc.uploaded_at
    elapsed = (datetime.now(timezone.utc) - reference_time).total_seconds()
    return elapsed > settings.STUCK_THRESHOLD_SECONDS


def is_stuck_processing_for_retry(doc: DocumentRecord) -> bool:
    """
    True if `doc` has been in PROCESSING longer than settings.STUCK_THRESHOLD_SECONDS: whether
    routers/documents.py's retry_document can retry it without waiting for it to reach FAILED.
    """
    return doc.status == DocumentStatus.PROCESSING and is_stuck(doc)


def get_stuck_documents() -> list[StuckDocument]:
    """AC4: any stuck document is queryable here — so no accepted document
    can become invisible to monitoring."""
    # Narrowed to StuckDocument below, which has no extracted_fields of its
    # own — skip the extra query.
    candidates = document_repository.get_documents_in_status(
        list(_STUCK_ELIGIBLE_STATUSES), include_fields=False
    )

    now = datetime.now(timezone.utc)
    stuck: list[StuckDocument] = []

    for doc in candidates:
        if not is_stuck(doc):
            continue
        reference_time = doc.processing_started_at or doc.queued_at or doc.uploaded_at
        elapsed = (now - reference_time).total_seconds()
        stuck.append(
            StuckDocument(
                id=doc.id,
                case_id=doc.case_id,
                status=doc.status,
                seconds_in_current_status=elapsed,
            )
        )

    return stuck