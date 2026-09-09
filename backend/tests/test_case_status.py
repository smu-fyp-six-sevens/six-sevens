from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.case_status import compute_documents_status, compute_requirement_coverage
from app.config import settings
from app.models import CaseDocumentsStatus, ClassificationOutcome, DocumentRecord, DocumentStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_document(
    document_id: str,
    status: DocumentStatus,
    *,
    uploaded_seconds_ago: float = 0,
    queued_seconds_ago: float | None = None,
    processing_seconds_ago: float | None = None,
    classification_outcome: ClassificationOutcome | None = None,
    fulfilled_requirements: list[str] | None = None,
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
        classification_outcome=classification_outcome,
        fulfilled_requirements=fulfilled_requirements or [],
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


def test_coverage_with_no_documents_is_all_missing() -> None:
    fulfilled, missing = compute_requirement_coverage([])
    assert fulfilled == []
    assert missing  # the full checklist


def test_single_document_can_fulfil_multiple_requirements() -> None:
    """The story's own example: an NRIC also covers Proof of Address."""
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            fulfilled_requirements=["id_document", "proof_of_address"],
        )
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "id_document" in fulfilled
    assert "proof_of_address" in fulfilled
    assert "id_document" not in missing
    assert "proof_of_address" not in missing


def test_manual_review_document_does_not_count_as_fulfilled() -> None:
    """An unconfirmed classification shouldn't retire a checklist item."""
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.MANUAL_REVIEW,
            fulfilled_requirements=["bank_statement"],
        )
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "bank_statement" not in fulfilled
    assert "bank_statement" in missing


def test_passport_does_not_fulfil_proof_of_address() -> None:
    """A passport is an id_document but carries no address, unlike an NRIC."""
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            fulfilled_requirements=["id_document"],
        )
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "id_document" in fulfilled
    assert "proof_of_address" in missing
