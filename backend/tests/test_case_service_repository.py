from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app import case_service
from app.models import CaseDocumentsStatus, CaseRecord, CaseRisk, CaseStage, DocumentRecord, DocumentStatus


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


def _case_at(case_id: str, created_at: str, number: str | None = None, name: str | None = None) -> CaseRecord:
    return CaseRecord(
        id=case_id, greenlink_case_id=number or f"GLDB-{case_id[-1]}", company_name=name,
        created_at=created_at, updated_at=created_at,
    )


def _page(**overrides):
    args = {"limit": 25, "offset": 0, "sort": "newest", "statuses": [], "q": None}
    return case_service.list_cases_page(**{**args, **overrides})


def test_the_default_page_reads_only_its_own_cases_and_their_documents() -> None:
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.risk_store") as store,
    ):
        case_repo.list_cases_page.return_value = ([_make_case("case-1"), _make_case("case-2")], 40)
        doc_repo.list_document_statuses.return_value = [
            _make_document("doc-1", case_id="case-1", status=DocumentStatus.ANALYZED),
            _make_document("doc-2", case_id="case-2", status=DocumentStatus.FAILED),
            _make_document("doc-3", case_id=None, status=DocumentStatus.UPLOADED),  # never crashes the grouping
        ]
        store.latest_many.return_value = {}

        page = _page(offset=25, q="  acme  ")

    assert [(i.id, i.documents_status) for i in page.items] == [
        ("case-1", CaseDocumentsStatus.ANALYZED), ("case-2", CaseDocumentsStatus.FAILED),
    ]
    assert (page.total, page.limit, page.offset) == (40, 25, 25)
    case_repo.list_cases_page.assert_called_once_with(
        "acme", newest=True, limit=25, offset=25, statuses=[], risk_ranks=None, highest_risk_first=None,
    )
    doc_repo.list_document_statuses.assert_called_once_with(["case-1", "case-2"])


def test_a_blank_search_is_no_search_and_oldest_first_reaches_the_database() -> None:
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.risk_store") as store,
    ):
        case_repo.list_cases_page.return_value = ([], 0)
        doc_repo.list_document_statuses.return_value = []
        store.latest_many.return_value = {}

        page = _page(sort="oldest", q="   ")

    assert page.items == [] and page.total == 0
    case_repo.list_cases_page.assert_called_once_with(
        None, newest=False, limit=25, offset=0, statuses=[], risk_ranks=None, highest_risk_first=None,
    )
    doc_repo.list_document_statuses.assert_not_called()


def test_a_stage_filter_pages_in_the_database() -> None:
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.risk_store") as store,
    ):
        case_repo.list_cases_page.return_value = ([_make_case("case-2")], 3)
        doc_repo.list_document_statuses.return_value = []
        store.latest_many.return_value = {}

        page = _page(statuses=[CaseStage.FAILED, CaseStage.PENDING_REVIEW], offset=1, limit=1, sort="oldest")

    assert [i.id for i in page.items] == ["case-2"] and page.total == 3
    case_repo.list_cases_page.assert_called_once_with(
        None, newest=False, limit=1, offset=1, statuses=["failed", "pending_review"],
        risk_ranks=None, highest_risk_first=None,
    )


@pytest.mark.parametrize(("overrides", "expected"), [
    # Unscored ranks 0, below Low; among equal ranks the newest come first.
    ({"sort": "risk"}, {"newest": True, "risk_ranks": None, "highest_risk_first": True}),
    ({"sort": "lowest_risk"}, {"newest": True, "risk_ranks": None, "highest_risk_first": False}),
    ({"risks": ["High", "unscored", "Low"]}, {"newest": True, "risk_ranks": [0, 1, 3], "highest_risk_first": None}),
    ({"sort": "oldest", "risks": ["Critical"]}, {"newest": False, "risk_ranks": [4], "highest_risk_first": None}),
])
def test_the_risk_order_and_filter_page_in_the_database(overrides, expected) -> None:
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.risk_store") as store,
    ):
        case_repo.list_cases_page.return_value = ([_make_case("case-2")], 7)
        doc_repo.list_document_statuses.return_value = []
        store.latest_many.return_value = {"case-2": CaseRisk(state="scored", level="High", score=70)}

        page = _page(**overrides, statuses=[CaseStage.CASE_ANALYSED], q="gldb", offset=1, limit=1)

    assert [i.id for i in page.items] == ["case-2"] and page.items[0].risk is not None and page.total == 7
    case_repo.list_cases_page.assert_called_once_with(
        "gldb", limit=1, offset=1, statuses=["case_analysed"], **expected,
    )


def test_counts_roll_up_every_case_by_stage() -> None:
    with patch("app.case_service.case_repository") as case_repo:
        case_repo.count_cases_by_status.return_value = {"case_analysed": 2, "failed": 1}

        counts = case_service.case_counts()

    assert counts.total == 3
    assert counts.by_status == {"case_analysed": 2, "failed": 1}


def test_search_lists_the_best_match_first() -> None:
    other = _case_at("case-1", "2026-10-03T00:00:00Z", number="GLDB-10", name="Acme Pte Ltd")
    exact = _case_at("case-2", "2026-10-02T00:00:00Z", number="GLDB-1", name="Zed Holdings")
    contains = _case_at("case-3", "2026-10-01T00:00:00Z", number="GLDB-7", name="Old gldb-1 Trading")
    with patch("app.case_service.case_repository") as case_repo:
        case_repo.list_cases_page.return_value = ([other, exact, contains], 3)

        result = case_service.search_cases(" GLDB-1 ", 8)

    assert [c.id for c in result.items] == ["case-2", "case-1", "case-3"]
    assert result.total == 3
    case_repo.list_cases_page.assert_called_once_with("GLDB-1", newest=True, limit=8, offset=0)
    case_repo.find_case_numbered.assert_not_called()


def test_search_fetches_the_best_matches_when_more_cases_match_than_fit() -> None:
    newest = _case_at("case-9", "2026-10-09T00:00:00Z", number="GLDB-19", name="Newest Co")
    exact = _case_at("case-1", "2026-10-01T00:00:00Z", number="GLDB-1", name="Oldest Co")
    prefix = _case_at("case-2", "2026-10-02T00:00:00Z", number="GLDB-12", name="Prefix Co")
    with patch("app.case_service.case_repository") as case_repo:
        case_repo.list_cases_page.return_value = ([newest], 30)
        case_repo.find_case_numbered.return_value = exact
        case_repo.find_cases_starting_with.return_value = [newest, prefix]

        result = case_service.search_cases("gldb-1", 2)

    assert [c.id for c in result.items] == ["case-1", "case-9"]  # exact first, no duplicates, cut to the limit
    assert result.total == 30


def test_search_without_a_query_is_the_newest_cases() -> None:
    with patch("app.case_service.case_repository") as case_repo:
        case_repo.list_cases_page.return_value = ([_make_case("case-1")], 1)

        result = case_service.search_cases(None, 20)

    case_repo.list_cases_page.assert_called_once_with(None, newest=True, limit=20, offset=0)
    assert [c.id for c in result.items] == ["case-1"]


def test_get_case_item_is_one_queue_row_or_none() -> None:
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.risk_store") as store,
    ):
        case_repo.get_case_by_number.side_effect = [_make_case("case-1"), None]
        doc_repo.list_document_statuses.return_value = [_make_document("d1", case_id="case-1")]
        store.latest_many.return_value = {}

        item = case_service.get_case_item("GLDB-1")
        missing = case_service.get_case_item("GLDB-404")

    assert item is not None and item.documents_status == CaseDocumentsStatus.ANALYZED
    assert missing is None


def test_get_case_delegates_to_case_repository() -> None:
    with patch("app.case_service.case_repository") as mock_case_repo:
        mock_case_repo.get_case.return_value = _make_case("case-1")

        result = case_service.get_case("case-1")

        assert result == _make_case("case-1")
        mock_case_repo.get_case.assert_called_once_with("case-1")


def test_create_case_fixes_the_checklist_rule_and_reviews_at_creation() -> None:
    with (
        patch("app.case_service.case_repository") as mock_case_repo,
        patch.object(case_service.settings, "REQUIRE_COMPLETE_CHECKLIST", True),
    ):
        mock_case_repo.create_case.return_value = _make_case("case-1")

        result = case_service.create_case("Acme Pte Ltd", reviews_required=False, created_by="greenlink-tester")

        assert result == _make_case("case-1")
        mock_case_repo.create_case.assert_called_once_with(
            "Acme Pte Ltd", checklist_enforced=True, reviews_required=False, created_by="greenlink-tester",
        )


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
        patch("app.case_service.analysis_repository") as mock_runs,
    ):
        mock_doc_repo.list_documents.return_value = documents
        mock_runs.latest_run_state.return_value = None

        assert case_service.delete_case("case-1") == documents

        mock_doc_repo.list_documents.assert_called_once_with(case_id="case-1", include_fields=False)
        mock_doc_repo.remove_documents.assert_called_once_with(documents, case_going=True)
        mock_case_repo.delete_case.assert_called_once_with("case-1")


# --- A case can't be deleted mid-run (PENDING_TESTS row 42) -------------------------------------------------------
# A delete mid-run would leave the run spending Claude calls on rows that no longer exist. delete_case refuses on its
# own, not only through the route, so no other caller can get round it.

def _typed(document_id: str, status: DocumentStatus) -> DocumentRecord:
    return _make_document(document_id, status=status).model_copy(update={"document_type": "payslip"})


_NOW = datetime.now(timezone.utc)
_LIVE_RUN = ("running", _NOW)
_LOST_RUN = ("running", _NOW - timedelta(hours=2))  # past the time limit: its worker was lost


def _delete(documents, *, reports=None, failed=None, latest_run=None):
    with (
        patch("app.case_service.case_repository") as cases,
        patch("app.case_service.document_repository") as docs,
        patch("app.case_service.analysis_repository") as runs,
        patch("app.case_service.detection_results") as detection,
    ):
        docs.list_documents.return_value = documents
        detection.get_states.return_value = (reports or {}, failed or set())
        runs.latest_run_state.return_value = latest_run
        try:
            case_service.delete_case("case-1")
            refused = None
        except case_service.CaseNotDeletable as exc:
            refused = str(exc)
        return refused, docs.remove_documents.called, cases.delete_case.called


@pytest.mark.parametrize(
    ("documents", "kwargs", "reason"),
    [
        ([_typed("doc-1", DocumentStatus.PROCESSING)], {}, "still being processed"),
        ([_typed("doc-1", DocumentStatus.DETECTING)], {}, "still being scanned"),
        ([_typed("doc-1", DocumentStatus.APPROVED)], {}, "waiting to be scanned"),
        ([_typed("doc-1", DocumentStatus.DETECTED)], {"latest_run": _LIVE_RUN}, "analysis is still running"),
    ],
    ids=["a document still processing", "a document being scanned",
         "an approved document with no report and no error", "a live analysis run"],
)
def test_a_case_with_something_running_is_not_deleted(documents, kwargs, reason) -> None:
    refused, removed_documents, removed_case = _delete(documents, **kwargs)

    assert refused is not None and reason in refused
    assert not removed_documents and not removed_case


@pytest.mark.parametrize(
    ("documents", "kwargs"),
    [
        ([_typed("doc-1", DocumentStatus.APPROVED)], {"failed": {"doc-1"}}),
        ([_typed("doc-1", DocumentStatus.APPROVED)], {"reports": {"doc-1": {"findings": []}}}),
        ([_make_document("doc-1", status=DocumentStatus.APPROVED)], {}),
        ([_typed("doc-1", DocumentStatus.DETECTED)], {"latest_run": _LOST_RUN}),
        ([_typed("doc-1", DocumentStatus.DETECTED)], {"latest_run": ("complete", _NOW)}),
    ],
    ids=["an approved document whose scan failed", "an approved document with a report",
         "an approved document with no type (never scanned)", "an analysis run past its time limit",
         "a finished analysis run"],
)
def test_a_case_with_nothing_running_is_deleted(documents, kwargs) -> None:
    refused, removed_documents, removed_case = _delete(documents, **kwargs)

    assert refused is None
    assert removed_documents and removed_case
