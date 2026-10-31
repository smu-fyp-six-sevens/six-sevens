from __future__ import annotations

import pytest

from app.case_pipeline import repository

from datetime import datetime

from app.case_pipeline.state import compute_fingerprint
from app.models import DocumentRecord, DocumentStatus

_TIMESTAMP = datetime.fromisoformat("2026-10-27T14:00:00+00:00")


def _document(document_id: str, status: DocumentStatus, document_type: str | None = "bank_statement") -> DocumentRecord:
    return DocumentRecord(
        id=document_id, case_id="case-1", filename=f"{document_id}.pdf", document_type=document_type,
        storage_path=None, status=status, uploaded_at=_TIMESTAMP,
    )


SCANNED = [_document(f"doc-{n}", DocumentStatus.DETECTED) for n in range(5)]
REPORTS = {d.id: {"findings": []} for d in SCANNED}


def test_only_approved_documents_move_the_fingerprint() -> None:
    # The analysis reads approved documents only: one still processing, waiting for approval or failed doesn't
    # make it out of date, and approving one (or its scan finishing) does.
    unapproved = [
        _document("doc-failed", DocumentStatus.FAILED), _document("doc-new", DocumentStatus.ANALYZED),
        _document("doc-queued", DocumentStatus.QUEUED),
    ]
    assert compute_fingerprint([*SCANNED, *unapproved], REPORTS) == compute_fingerprint(SCANNED, REPORTS)
    approved = compute_fingerprint([*SCANNED, _document("doc-new", DocumentStatus.APPROVED)], REPORTS)
    assert approved != compute_fingerprint(SCANNED, REPORTS)
    assert approved != compute_fingerprint([*SCANNED, _document("doc-new", DocumentStatus.DETECTED)], REPORTS)


def test_documents_the_score_ignores_leave_the_fingerprint_alone() -> None:
    # Rejected, cancelled, and a failed upload with no type (detection never runs on it) don't hold the score up,
    # so adding or deleting them doesn't make the analysis out of date, and a case without any keeps its old hash.
    ignored = [
        _document("doc-rejected", DocumentStatus.REJECTED),
        _document("doc-cancelled", DocumentStatus.CANCELLED),
        _document("doc-untyped", DocumentStatus.FAILED, document_type=None),
    ]
    assert compute_fingerprint([*SCANNED, *ignored], REPORTS) == compute_fingerprint(SCANNED, REPORTS)


def test_waiting_documents_say_what_each_unscanned_document_needs() -> None:
    from app.case_pipeline.state import waiting_documents

    documents = [
        *SCANNED,
        _document("doc-processing", DocumentStatus.EXTRACTING),
        _document("doc-to-approve", DocumentStatus.ANALYZED),
        _document("doc-scanning", DocumentStatus.DETECTING),
        _document("doc-failed", DocumentStatus.FAILED),
        _document("doc-rejected", DocumentStatus.REJECTED),
        _document("doc-untyped", DocumentStatus.FAILED, document_type=None),
    ]
    waiting = {w.document_id: w.reason for w in waiting_documents(documents, REPORTS)}
    # Only approved documents count, so only the one still being scanned holds the score back.
    assert waiting == {"doc-scanning": "scanning"}
    assert waiting_documents(SCANNED, REPORTS) == []


@pytest.mark.parametrize("confidence, expected", [(None, 0.0), (0.72, 0.72)])
def test_a_run_reads_from_a_list_query_with_or_without_a_confidence(confidence, expected) -> None:
    # A list query always returns the key: None while a run is going, and for a run from before it was recorded.
    row = {
        "run_id": "run-1", "case_id": "case-1", "status": "running", "fingerprint": "f",
        "started_at": "2026-10-31T00:00:00+00:00", "confidence": confidence, "findings": None, "risk": None,
        "supporting_documents": None,
    }

    assert repository._to_analysis(row).confidence == expected
