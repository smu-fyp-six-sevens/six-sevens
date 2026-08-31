from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.case_status import compute_documents_status
from app.config import settings
from app.models import CaseDocumentsStatus, DocumentRecord, DocumentStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_document(
    document_id: str,
    status: DocumentStatus,
    *,
    uploaded_seconds_ago: float = 0,
    queued_seconds_ago: float | None = None,
    processing_seconds_ago: float | None = None,
) -> DocumentRecord:
    now = _now()
    return DocumentRecord(
        id=document_id,
        case_id="case-1",
        filename=f"{document_id}.pdf",
        storage_path=f"case-1/{document_id}.pdf",
        status=status,
        uploaded_at=now - timedelta(seconds=uploaded_seconds_ago),
        queued_at=(now - timedelta(seconds=queued_seconds_ago)) if queued_seconds_ago else None,
        processing_started_at=(
            (now - timedelta(seconds=processing_seconds_ago)) if processing_seconds_ago else None
        ),
    )


def test_no_documents_returns_no_documents_status() -> None:
    assert compute_documents_status([]) == CaseDocumentsStatus.NO_DOCUMENTS


def test_all_analyzed_returns_analyzed() -> None:
    documents = [_make_document(f"doc-{i}", DocumentStatus.ANALYZED) for i in range(3)]
    assert compute_documents_status(documents) == CaseDocumentsStatus.ANALYZED


def test_one_stuck_among_many_analyzed_returns_stuck() -> None:
    """The exact scenario from the ask: 9 analyzed + 1 stuck reads 'stuck'."""
    old_enough = settings.STUCK_THRESHOLD_SECONDS + 5
    documents = [_make_document(f"doc-{i}", DocumentStatus.ANALYZED) for i in range(9)]
    documents.append(
        _make_document("doc-stuck", DocumentStatus.QUEUED, queued_seconds_ago=old_enough)
    )

    assert compute_documents_status(documents) == CaseDocumentsStatus.STUCK


def test_recently_queued_document_is_not_stuck() -> None:
    documents = [_make_document("doc-1", DocumentStatus.QUEUED, queued_seconds_ago=1)]
    assert compute_documents_status(documents) == CaseDocumentsStatus.IN_PROGRESS


def test_failed_outranks_in_progress_but_not_stuck() -> None:
    documents = [
        _make_document("doc-1", DocumentStatus.FAILED),
        _make_document("doc-2", DocumentStatus.QUEUED, queued_seconds_ago=1),
        _make_document("doc-3", DocumentStatus.ANALYZED),
    ]
    assert compute_documents_status(documents) == CaseDocumentsStatus.FAILED


def test_stuck_outranks_failed() -> None:
    old_enough = settings.STUCK_THRESHOLD_SECONDS + 5
    documents = [
        _make_document("doc-1", DocumentStatus.FAILED),
        _make_document("doc-2", DocumentStatus.PROCESSING, processing_seconds_ago=old_enough),
    ]
    assert compute_documents_status(documents) == CaseDocumentsStatus.STUCK


def test_mixed_uploaded_queued_processing_returns_in_progress() -> None:
    documents = [
        _make_document("doc-1", DocumentStatus.UPLOADED),
        _make_document("doc-2", DocumentStatus.QUEUED, queued_seconds_ago=1),
        _make_document("doc-3", DocumentStatus.PROCESSING, processing_seconds_ago=1),
    ]
    assert compute_documents_status(documents) == CaseDocumentsStatus.IN_PROGRESS
