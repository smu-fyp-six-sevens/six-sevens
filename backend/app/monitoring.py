from __future__ import annotations

from datetime import datetime, timezone

from . import document_repository
from .config import settings
from .models import DocumentStatus, StuckDocument


def get_stuck_documents() -> list[StuckDocument]:
    """
    AC4: any document in UPLOADED, QUEUED, or PROCESSING longer than
    STUCK_THRESHOLD_SECONDS with no status update is queryable here —
    so no accepted document can become invisible to monitoring. UPLOADED is
    included because a document can be stored but fail to enqueue, leaving
    it stuck there indefinitely otherwise.
    """
    candidates = document_repository.get_documents_in_status(
        [DocumentStatus.UPLOADED, DocumentStatus.QUEUED, DocumentStatus.PROCESSING]
    )

    now = datetime.now(timezone.utc)
    stuck: list[StuckDocument] = []

    for doc in candidates:
        reference_time = doc.processing_started_at or doc.queued_at or doc.uploaded_at
        elapsed = (now - reference_time).total_seconds()
        if elapsed > settings.STUCK_THRESHOLD_SECONDS:
            stuck.append(
                StuckDocument(
                    id=doc.id,
                    case_id=doc.case_id,
                    status=doc.status,
                    seconds_in_current_status=elapsed,
                )
            )

    return stuck