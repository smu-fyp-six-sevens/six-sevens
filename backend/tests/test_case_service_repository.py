from __future__ import annotations

from unittest.mock import patch

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
    case_repo.list_cases_page.assert_called_once_with("acme", newest=True, limit=25, offset=25, statuses=[])
    doc_repo.list_document_statuses.assert_called_once_with(["case-1", "case-2"])
    case_repo.list_matching_cases.assert_not_called()


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
    case_repo.list_cases_page.assert_called_once_with(None, newest=False, limit=25, offset=0, statuses=[])
    doc_repo.list_document_statuses.assert_not_called()


def _three_cases() -> list[CaseRecord]:
    """Newest first, as list_matching_cases returns them."""
    return [
        _case_at("case-1", "2026-10-03T00:00:00Z"),
        _case_at("case-2", "2026-10-02T00:00:00Z"),
        _case_at("case-3", "2026-10-01T00:00:00Z"),
    ]


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
    )
    case_repo.list_matching_cases.assert_not_called()


def test_highest_risk_first_puts_unscored_last_and_the_newest_first_among_equals() -> None:
    cases = [*_three_cases(), _case_at("case-4", "2026-09-01T00:00:00Z")]
    risks = {
        "case-2": CaseRisk(state="scored", level="High", score=70),
        "case-4": CaseRisk(state="scored", level="High", score=75),
        "case-3": CaseRisk(state="scored", level="Low", score=10),
        "case-1": CaseRisk(state="pending"),
    }
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.risk_store") as store,
    ):
        case_repo.list_matching_cases.return_value = cases
        doc_repo.list_document_statuses.return_value = []
        store.latest_many.return_value = risks

        page = _page(sort="risk")

    assert [i.id for i in page.items] == ["case-2", "case-4", "case-3", "case-1"]
    assert page.total == 4


def _risk_cases():
    cases = [*_three_cases(), _case_at("case-4", "2026-09-01T00:00:00Z")]
    risks = {
        "case-1": CaseRisk(state="pending"),
        "case-2": CaseRisk(state="scored", level="High", score=70),
        "case-3": CaseRisk(state="scored", level="Low", score=10),
        "case-4": CaseRisk(state="scored", level="High", score=75),
    }
    return cases, risks


def test_lowest_risk_first_puts_unscored_first_and_the_newest_first_among_equals() -> None:
    cases, risks = _risk_cases()
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.risk_store") as store,
    ):
        case_repo.list_matching_cases.return_value = cases
        doc_repo.list_document_statuses.return_value = []
        store.latest_many.return_value = risks

        page = _page(sort="lowest_risk")

    assert [i.id for i in page.items] == ["case-1", "case-3", "case-2", "case-4"]


def test_a_risk_filter_keeps_only_those_levels_and_pages_the_matches() -> None:
    cases, risks = _risk_cases()
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.risk_store") as store,
    ):
        case_repo.list_matching_cases.return_value = cases
        doc_repo.list_document_statuses.return_value = []
        store.latest_many.return_value = risks

        high = _page(risks=["High"])
        high_or_low = _page(risks=["High", "Low"])
        second = _page(risks=["High", "Low"], offset=2, limit=2)
        unscored = _page(risks=["unscored"])

    assert [i.id for i in high.items] == ["case-2", "case-4"] and high.total == 2
    assert [i.id for i in high_or_low.items] == ["case-2", "case-3", "case-4"] and high_or_low.total == 3
    assert [i.id for i in second.items] == ["case-4"] and second.total == 3
    assert [i.id for i in unscored.items] == ["case-1"]
    case_repo.list_cases_page.assert_not_called()


def test_stage_risk_and_search_filters_combine_and_share_one_total() -> None:
    cases, risks = _risk_cases()
    # The database has already kept only the analysed cases: case-2 is not one.
    analysed = [case for case in cases if case.id != "case-2"]
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.risk_store") as store,
    ):
        case_repo.list_matching_cases.return_value = analysed
        doc_repo.list_document_statuses.return_value = []
        store.latest_many.return_value = risks

        page = _page(statuses=[CaseStage.CASE_ANALYSED], risks=["High", "Low"], q="gldb", sort="risk")
        second = _page(statuses=[CaseStage.CASE_ANALYSED], risks=["High", "Low"], q="gldb", sort="risk", offset=1, limit=1)

    # High before Low.
    assert [i.id for i in page.items] == ["case-4", "case-3"] and page.total == 2
    assert [i.id for i in second.items] == ["case-3"] and second.total == 2
    case_repo.list_matching_cases.assert_called_with("gldb", statuses=["case_analysed"])


def test_oldest_first_with_a_risk_filter_reverses_the_newest_first_list() -> None:
    cases, risks = _risk_cases()
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.risk_store") as store,
    ):
        case_repo.list_matching_cases.return_value = cases
        doc_repo.list_document_statuses.return_value = []
        store.latest_many.return_value = risks

        page = _page(sort="oldest", risks=["High", "Low"])

    assert [i.id for i in page.items] == ["case-4", "case-3", "case-2"]


def test_counts_roll_up_every_case_by_stage() -> None:
    with patch("app.case_service.case_repository") as case_repo:
        case_repo.list_case_statuses.return_value = ["case_analysed", "failed", "case_analysed"]

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

        result = case_service.create_case("Acme Pte Ltd", reviews_required=False)

        assert result == _make_case("case-1")
        mock_case_repo.create_case.assert_called_once_with(
            "Acme Pte Ltd", checklist_enforced=True, reviews_required=False,
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
    ):
        mock_doc_repo.list_documents.return_value = documents

        case_service.delete_case("case-1")

        mock_doc_repo.list_documents.assert_called_once_with(case_id="case-1", include_fields=False)
        mock_doc_repo.remove_documents.assert_called_once_with(documents)
        mock_case_repo.delete_case.assert_called_once_with("case-1")
