from __future__ import annotations

from datetime import datetime, timezone

from .config import settings
from .models import DocumentRecord, DocumentStatus

# The sweeper's limits (sweeper.py), so the case queue, an officer's retry and the recovery agree on what's stuck.
_WAITING = frozenset({DocumentStatus.UPLOADED, DocumentStatus.QUEUED})
_RUNNING = frozenset({DocumentStatus.PROCESSING, DocumentStatus.EXTRACTING})


def is_stuck(doc: DocumentRecord) -> bool:
    """Waiting past STUCK_WAITING_SECONDS (its event was lost) or running past STUCK_RUNNING_SECONDS (its run died)."""
    if doc.status in _WAITING:
        limit = settings.STUCK_WAITING_SECONDS
    elif doc.status in _RUNNING:
        limit = settings.STUCK_RUNNING_SECONDS
    else:
        return False
    reference_time = doc.processing_started_at or doc.queued_at or doc.uploaded_at
    return (datetime.now(timezone.utc) - reference_time).total_seconds() > limit


def is_stuck_processing_for_retry(doc: DocumentRecord) -> bool:
    """Whether retry_document may re-queue a PROCESSING document without waiting for it to fail."""
    return doc.status == DocumentStatus.PROCESSING and is_stuck(doc)
