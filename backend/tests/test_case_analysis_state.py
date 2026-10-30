from __future__ import annotations

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


def test_deleting_a_failed_document_the_score_waits_on_makes_the_analysis_out_of_date() -> None:
    # A bank statement that failed processing: never approved, but the risk score counts it ("5/6 scanned").
    failed = _document("doc-failed", DocumentStatus.FAILED)
    with_failed = compute_fingerprint([*SCANNED, failed], REPORTS)
    assert with_failed != compute_fingerprint(SCANNED, REPORTS)
    # Retrying it (a new status) changes it too.
    assert with_failed != compute_fingerprint([*SCANNED, _document("doc-failed", DocumentStatus.QUEUED)], REPORTS)


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
    # The scanned ones and the ones the score ignores aren't listed; the rest say what they're waiting for.
    assert waiting == {
        "doc-processing": "processing", "doc-to-approve": "needs_approval",
        "doc-scanning": "scanning", "doc-failed": "failed",
    }
    assert waiting_documents(SCANNED, REPORTS) == []
