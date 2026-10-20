from __future__ import annotations

from unittest.mock import patch

from app import case_service
from app.models import CaseDocumentsStatus, CaseRecord, DocumentRecord, DocumentStatus


def _make_case(case_id: str = "case-1", greenlink_case_id: str = "GLDB-1") -> CaseRecord:
    return CaseRecord(
        id=case_id,
        greenlink_case_id=greenlink_case_id,
        status="open",
        submission_finalized=False,
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
    with patch("app.case_service.case_repository") as mock_case_repo:
        mock_case_repo.get_case.return_value = None

        assert case_service.get_case_summary("missing") is None


def test_get_case_summary_combines_case_and_documents_status() -> None:
    with (
        patch("app.case_service.case_repository") as mock_case_repo,
        patch("app.case_service.document_repository") as mock_doc_repo,
    ):
        mock_case_repo.get_case.return_value = _make_case("case-1")
        mock_doc_repo.list_documents.return_value = [
            _make_document(status=DocumentStatus.ANALYZED)
        ]

        summary = case_service.get_case_summary("case-1")

        assert summary is not None
        assert summary.id == "case-1"
        assert summary.documents_status == CaseDocumentsStatus.ANALYZED
        mock_doc_repo.list_documents.assert_called_once_with(case_id="case-1")


def test_list_case_summaries_groups_documents_by_case() -> None:
    with (
        patch("app.case_service.case_repository") as mock_case_repo,
        patch("app.case_service.document_repository") as mock_doc_repo,
    ):
        mock_case_repo.list_cases.return_value = [_make_case("case-1"), _make_case("case-2")]
        mock_doc_repo.list_documents.return_value = [
            _make_document("doc-1", case_id="case-1", status=DocumentStatus.ANALYZED),
            _make_document("doc-2", case_id="case-2", status=DocumentStatus.FAILED),
            # unassigned document (case_id=None) should never crash the grouping
            _make_document("doc-3", case_id=None, status=DocumentStatus.UPLOADED),
        ]

        summaries = case_service.list_case_summaries()

        by_id = {s.id: s for s in summaries}
        assert by_id["case-1"].documents_status == CaseDocumentsStatus.ANALYZED
        assert by_id["case-2"].documents_status == CaseDocumentsStatus.FAILED
        mock_doc_repo.list_documents.assert_called_once_with(include_fields=False)


def test_list_case_summaries_case_with_no_documents_is_no_documents() -> None:
    with (
        patch("app.case_service.case_repository") as mock_case_repo,
        patch("app.case_service.document_repository") as mock_doc_repo,
    ):
        mock_case_repo.list_cases.return_value = [_make_case("case-1")]
        mock_doc_repo.list_documents.return_value = []

        summaries = case_service.list_case_summaries()

        assert len(summaries) == 1
        assert summaries[0].documents_status == CaseDocumentsStatus.NO_DOCUMENTS


def test_list_case_summaries_with_no_cases_returns_empty() -> None:
    with (
        patch("app.case_service.case_repository") as mock_case_repo,
        patch("app.case_service.document_repository") as mock_doc_repo,
    ):
        mock_case_repo.list_cases.return_value = []
        mock_doc_repo.list_documents.return_value = []

        assert case_service.list_case_summaries() == []


def test_get_case_delegates_to_case_repository() -> None:
    with patch("app.case_service.case_repository") as mock_case_repo:
        mock_case_repo.get_case.return_value = _make_case("case-1")

        result = case_service.get_case("case-1")

        assert result == _make_case("case-1")
        mock_case_repo.get_case.assert_called_once_with("case-1")


def test_create_case_delegates_to_case_repository() -> None:
    with patch("app.case_service.case_repository") as mock_case_repo:
        mock_case_repo.create_case.return_value = _make_case("case-1")

        result = case_service.create_case("Acme Pte Ltd")

        assert result == _make_case("case-1")
        mock_case_repo.create_case.assert_called_once_with("Acme Pte Ltd")


def test_update_case_delegates_to_case_repository() -> None:
    with patch("app.case_service.case_repository") as mock_case_repo:
        mock_case_repo.update_case.return_value = _make_case("case-1")

        result = case_service.update_case("case-1", "New Name Pte Ltd")

        assert result == _make_case("case-1")
        mock_case_repo.update_case.assert_called_once_with("case-1", "New Name Pte Ltd")


def test_delete_case_removes_its_documents_then_the_case() -> None:
    documents = [_make_document("doc-1"), _make_document("doc-2")]
    with (
        patch("app.case_service.case_repository") as mock_case_repo,
        patch("app.case_service.document_repository") as mock_doc_repo,
    ):
        mock_doc_repo.list_documents.return_value = documents

        case_service.delete_case("case-1")

        mock_doc_repo.list_documents.assert_called_once_with(case_id="case-1", include_fields=False)
        mock_doc_repo.remove_documents.assert_called_once_with(documents)
        mock_case_repo.delete_case.assert_called_once_with("case-1")
