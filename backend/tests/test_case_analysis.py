from __future__ import annotations

import pytest

from app.case_pipeline.readiness import is_ready_for_analysis
from app.models import DocumentRecord, DocumentStatus


def _document(document_id: str, status: DocumentStatus, document_type: str = "bank_statement") -> DocumentRecord:
    return DocumentRecord(
        id=document_id,
        case_id="case-1",
        filename=f"{document_id}.pdf",
        document_type=document_type,
        status=status,
        uploaded_at="2026-10-01T00:00:00Z",
    )


def test_a_case_with_no_documents_is_not_ready() -> None:
    assert is_ready_for_analysis([]) is False


def test_all_approved_documents_detected_is_ready() -> None:
    documents = [_document("a", DocumentStatus.DETECTED), _document("b", DocumentStatus.DETECTED)]

    assert is_ready_for_analysis(documents) is True


@pytest.mark.parametrize("status", [DocumentStatus.APPROVED, DocumentStatus.DETECTING])
def test_an_approved_document_still_being_scanned_blocks(status: DocumentStatus) -> None:
    documents = [_document("a", DocumentStatus.DETECTED), _document("b", status)]

    assert is_ready_for_analysis(documents) is False


@pytest.mark.parametrize(
    "status",
    [DocumentStatus.ANALYZED, DocumentStatus.REJECTED, DocumentStatus.CANCELLED, DocumentStatus.FAILED],
)
def test_documents_that_are_not_approved_do_not_block(status: DocumentStatus) -> None:
    documents = [_document("a", DocumentStatus.DETECTED), _document("b", status)]

    assert is_ready_for_analysis(documents) is True


def test_only_unapproved_documents_is_not_ready() -> None:
    assert is_ready_for_analysis([_document("a", DocumentStatus.ANALYZED)]) is False


def test_a_scanned_other_document_counts_as_approved() -> None:
    documents = [_document("a", DocumentStatus.DETECTED, "unrecognised")]

    assert is_ready_for_analysis(documents) is True
