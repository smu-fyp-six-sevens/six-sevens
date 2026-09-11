from __future__ import annotations

from unittest.mock import patch

from app import case_status
from app.models import CaseDocumentsStatus, CaseRecord, DocumentRecord, DocumentStatus


def _make_case(case_id: str = "case-1", greenlink_case_id: str = "GLDB-1") -> CaseRecord:
    return CaseRecord(
        id=case_id,
        greenlink_case_id=greenlink_case_id,
        status="open",
        submission_finalized=False,
        recommended_additional_documents=[],
        created_at="2026-08-26T00:00:00Z",
        updated_at="2026-08-26T00:00:00Z",
    )


def _make_document(
    document_id: str = "doc-1",
    case_id: str | None = "case-1",
    status: DocumentStatus = DocumentStatus.ANALYZED,
) -> DocumentRecord:
    return DocumentRecord(
        id=document_id,
        case_id=case_id,
        filename=f"{document_id}.pdf",
        storage_path=f"{case_id}/{document_id}.pdf",
        status=status,
        uploaded_at="2026-08-26T00:00:00Z",
    )


def test_get_case_summary_returns_none_when_case_missing() -> None:
    with patch("app.case_status.case_repository") as mock_case_repo:
        mock_case_repo.get_case.return_value = None

        assert case_status.get_case_summary("missing") is None


def test_get_case_summary_combines_case_and_documents_status() -> None:
    with (
        patch("app.case_status.case_repository") as mock_case_repo,
        patch("app.case_status.document_repository") as mock_doc_repo,
    ):
        mock_case_repo.get_case.return_value = _make_case("case-1")
        mock_doc_repo.list_documents.return_value = [
            _make_document(status=DocumentStatus.ANALYZED)
        ]

        summary = case_status.get_case_summary("case-1")

        assert summary is not None
        assert summary.id == "case-1"
        assert summary.documents_status == CaseDocumentsStatus.ANALYZED
        mock_doc_repo.list_documents.assert_called_once_with(case_id="case-1")


def test_list_case_summaries_groups_documents_by_case() -> None:
    with (
        patch("app.case_status.case_repository") as mock_case_repo,
        patch("app.case_status.document_repository") as mock_doc_repo,
    ):
        mock_case_repo.list_cases.return_value = [_make_case("case-1"), _make_case("case-2")]
        mock_doc_repo.list_documents.return_value = [
            _make_document("doc-1", case_id="case-1", status=DocumentStatus.ANALYZED),
            _make_document("doc-2", case_id="case-2", status=DocumentStatus.FAILED),
            # unassigned document (case_id=None) should never crash the grouping
            _make_document("doc-3", case_id=None, status=DocumentStatus.UPLOADED),
        ]

        summaries = case_status.list_case_summaries()

        by_id = {s.id: s for s in summaries}
        assert by_id["case-1"].documents_status == CaseDocumentsStatus.ANALYZED
        assert by_id["case-2"].documents_status == CaseDocumentsStatus.FAILED
        mock_doc_repo.list_documents.assert_called_once_with()


def test_list_case_summaries_case_with_no_documents_is_no_documents() -> None:
    with (
        patch("app.case_status.case_repository") as mock_case_repo,
        patch("app.case_status.document_repository") as mock_doc_repo,
    ):
        mock_case_repo.list_cases.return_value = [_make_case("case-1")]
        mock_doc_repo.list_documents.return_value = []

        summaries = case_status.list_case_summaries()

        assert len(summaries) == 1
        assert summaries[0].documents_status == CaseDocumentsStatus.NO_DOCUMENTS


def test_list_case_summaries_with_no_cases_returns_empty() -> None:
    with (
        patch("app.case_status.case_repository") as mock_case_repo,
        patch("app.case_status.document_repository") as mock_doc_repo,
    ):
        mock_case_repo.list_cases.return_value = []
        mock_doc_repo.list_documents.return_value = []

        assert case_status.list_case_summaries() == []
