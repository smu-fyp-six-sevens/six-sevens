from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from tests.conftest import TEST_USER

from fastapi.testclient import TestClient

from app.case_service import CaseNotDeletable
from app.main import app
from app.models import (
    CaseCounts,
    CaseDocumentsStatus,
    CaseListItem,
    CaseListPage,
    CaseRecord,
    CaseSearchResult,
    CaseStage,
    CaseSummary,
    DocumentRecord,
    DocumentStatus,
)


client = TestClient(app)


def _make_case(
    case_id: str = "case-1",
    company_name: str | None = None,
    greenlink_case_id: str = "GLDB-1",
) -> CaseRecord:
    return CaseRecord(
        id=case_id,
        greenlink_case_id=greenlink_case_id,
        company_name=company_name,
        status="open",
        submission_finalized=False,
        created_at="2026-08-26T00:00:00Z",
        updated_at="2026-08-26T00:00:00Z",
    )


def _make_summary(
    case_id: str = "case-1",
    documents_status: CaseDocumentsStatus = CaseDocumentsStatus.NO_DOCUMENTS,
) -> CaseSummary:
    return CaseSummary(**_make_case(case_id).model_dump(), documents_status=documents_status)


def test_create_case() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.create_case.return_value = _make_case("new-case")

        response = client.post("/cases")

    assert response.status_code == 201
    assert response.json()["id"] == "new-case"
    # The signed-in officer is recorded as the creator.
    mock_case_service.create_case.assert_called_once_with(None, reviews_required=True, created_by=TEST_USER)


def test_create_case_endpoint_with_company_name() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.create_case.return_value = _make_case("new-case", company_name="Acme Pte Ltd")

        response = client.post("/cases", json={"company_name": "Acme Pte Ltd"})

    assert response.status_code == 201
    assert response.json()["company_name"] == "Acme Pte Ltd"
    mock_case_service.create_case.assert_called_once_with("Acme Pte Ltd", reviews_required=True, created_by=TEST_USER)


def test_get_case_not_found() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.get_case_summary.return_value = None

        response = client.get("/cases/missing-id")

    assert response.status_code == 404


def test_get_case_returns_summary_with_documents_status() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.get_case_summary.return_value = _make_summary(
            "case-1", CaseDocumentsStatus.STUCK
        )

        response = client.get("/cases/case-1")

    assert response.status_code == 200
    assert response.json()["documents_status"] == "stuck"


def test_create_case_db_failure_returns_502() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.create_case.side_effect = RuntimeError("connection refused")

        response = client.post("/cases")

    assert response.status_code == 502


def test_get_case_db_failure_returns_502() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.get_case_summary.side_effect = RuntimeError("connection refused")

        response = client.get("/cases/case-1")

    assert response.status_code == 502


def _make_item(case_id: str, documents_status: CaseDocumentsStatus) -> CaseListItem:
    return CaseListItem(**_make_case(case_id).model_dump(), documents_status=documents_status)


def test_list_cases() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.list_cases_page.return_value = CaseListPage(
            items=[_make_item("case-1", CaseDocumentsStatus.ANALYZED), _make_item("case-2", CaseDocumentsStatus.STUCK)],
            total=40, limit=25, offset=0,
        )

        response = client.get("/cases")

    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["limit"], body["offset"]) == (40, 25, 0)
    assert body["items"][1]["documents_status"] == "stuck"
    # Nothing asked for: the first 25, newest first, no filter.
    mock_case_service.list_cases_page.assert_called_once_with(
        limit=25, offset=0, sort="newest", statuses=[], risks=[], q=None,
    )


def test_list_cases_passes_the_page_sort_filter_and_search_through() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.list_cases_page.return_value = CaseListPage(items=[], total=0, limit=10, offset=20)

        response = client.get(
            "/cases?limit=10&offset=20&sort=risk&status=failed&status=pending_review&risk=High&risk=unscored&q=acme"
        )

    assert response.status_code == 200
    mock_case_service.list_cases_page.assert_called_once_with(
        limit=10, offset=20, sort="risk",
        statuses=[CaseStage.FAILED, CaseStage.PENDING_REVIEW], risks=["High", "unscored"], q="acme",
    )


def test_list_cases_accepts_the_lowest_risk_order_and_rejects_an_unknown_risk_level() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.list_cases_page.return_value = CaseListPage(items=[], total=0, limit=25, offset=0)

        accepted = client.get("/cases?sort=lowest_risk")
        rejected = client.get("/cases?risk=Severe")

    assert (accepted.status_code, rejected.status_code) == (200, 422)


def test_list_cases_rejects_a_page_outside_its_limits() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        responses = [
            client.get("/cases?limit=0"),
            client.get("/cases?limit=101"),
            client.get("/cases?offset=-1"),
            client.get("/cases?sort=cheapest"),
            client.get("/cases?status=stuck"),
            client.get("/cases?q=" + "x" * 101),
        ]

    assert [r.status_code for r in responses] == [422] * 6
    mock_case_service.list_cases_page.assert_not_called()


def test_list_cases_db_failure_returns_502() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.list_cases_page.side_effect = RuntimeError("connection refused")

        response = client.get("/cases")

    assert response.status_code == 502


def test_case_counts() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.case_counts.return_value = CaseCounts(total=3, by_status={"analyzed": 2, "stuck": 1})

        response = client.get("/cases/counts")

    # "counts" is a path of its own, not a case id.
    assert response.status_code == 200
    assert response.json() == {"total": 3, "by_status": {"analyzed": 2, "stuck": 1}}
    mock_case_service.get_case_summary.assert_not_called()


def test_search_cases() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.search_cases.return_value = CaseSearchResult(items=[_make_case("case-1")], total=12)

        response = client.get("/cases/search?q=acme&limit=5")

    assert response.status_code == 200
    assert response.json()["total"] == 12
    assert response.json()["items"][0]["id"] == "case-1"
    mock_case_service.search_cases.assert_called_once_with("acme", 5)


def test_search_cases_defaults_to_eight_and_rejects_too_many() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.search_cases.return_value = CaseSearchResult(items=[], total=0)

        assert client.get("/cases/search").status_code == 200
        assert client.get("/cases/search?limit=51").status_code == 422

    mock_case_service.search_cases.assert_called_once_with(None, 8)


def test_get_case_by_number() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.get_case_item.return_value = _make_item("case-1", CaseDocumentsStatus.ANALYZED)

        response = client.get("/cases/by-number/GLDB-1")

    assert response.status_code == 200
    assert response.json()["id"] == "case-1"
    mock_case_service.get_case_item.assert_called_once_with("GLDB-1")


def test_get_case_by_number_unknown_is_404() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.get_case_item.return_value = None

        response = client.get("/cases/by-number/GLDB-404")

    assert response.status_code == 404


# --- PATCH /cases/{case_id} (officer renaming a case) -------------------
#
# update_case/delete_case both sit behind Depends(get_case_or_404) (see
# routers/_dependencies.py), which calls case_service.get_case itself — so
# these tests patch app.routers._dependencies.case_service for the
# existence check, separately from app.routers.cases.case_service for the
# handler's own writes.


def test_update_case_saves_company_name_and_returns_fresh_summary() -> None:
    with (
        patch("app.routers._dependencies.case_service") as mock_dep_case_service,
        patch("app.routers.cases.case_service") as mock_case_service,
    ):
        mock_dep_case_service.get_case.return_value = _make_case("case-1")
        mock_case_service.get_case_summary.return_value = _make_summary("case-1")

        response = client.patch(
            "/cases/case-1", json={"company_name": "Meridian Warehousing Pte Ltd"}
        )

    assert response.status_code == 200
    assert response.json()["id"] == "case-1"
    mock_case_service.update_case.assert_called_once_with("case-1", "Meridian Warehousing Pte Ltd")
    # The summary is re-read rather than patched in memory, so the response
    # reflects whatever the write actually landed.
    mock_case_service.get_case_summary.assert_called_once_with("case-1")


def test_update_case_trims_surrounding_whitespace() -> None:
    with (
        patch("app.routers._dependencies.case_service") as mock_dep_case_service,
        patch("app.routers.cases.case_service") as mock_case_service,
    ):
        mock_dep_case_service.get_case.return_value = _make_case("case-1")
        mock_case_service.get_case_summary.return_value = _make_summary("case-1")

        response = client.patch("/cases/case-1", json={"company_name": "  Acme Pte Ltd  "})

    assert response.status_code == 200
    mock_case_service.update_case.assert_called_once_with("case-1", "Acme Pte Ltd")


def test_update_case_rejects_blank_company_name() -> None:
    # UpdateCaseRequest's own validator rejects this before the write would
    # ever happen, so nothing is written regardless of dependency order.
    with (
        patch("app.routers._dependencies.case_service") as mock_dep_case_service,
        patch("app.routers.cases.case_service") as mock_case_service,
    ):
        mock_dep_case_service.get_case.return_value = _make_case("case-1")

        response = client.patch("/cases/case-1", json={"company_name": "   "})

    assert response.status_code == 422
    mock_case_service.update_case.assert_not_called()


def test_update_case_not_found() -> None:
    with (
        patch("app.routers._dependencies.case_service") as mock_dep_case_service,
        patch("app.routers.cases.case_service") as mock_case_service,
    ):
        mock_dep_case_service.get_case.return_value = None

        response = client.patch("/cases/missing-id", json={"company_name": "Acme Pte Ltd"})

    assert response.status_code == 404
    mock_case_service.update_case.assert_not_called()


def test_update_case_fetch_failure_returns_502() -> None:
    with (
        patch("app.routers._dependencies.case_service") as mock_dep_case_service,
        patch("app.routers.cases.case_service") as mock_case_service,
    ):
        mock_dep_case_service.get_case.side_effect = RuntimeError("connection refused")

        response = client.patch("/cases/case-1", json={"company_name": "Acme Pte Ltd"})

    assert response.status_code == 502
    mock_case_service.update_case.assert_not_called()


def test_update_case_write_failure_returns_502() -> None:
    with (
        patch("app.routers._dependencies.case_service") as mock_dep_case_service,
        patch("app.routers.cases.case_service") as mock_case_service,
    ):
        mock_dep_case_service.get_case.return_value = _make_case("case-1")
        mock_case_service.update_case.side_effect = RuntimeError("db down")

        response = client.patch("/cases/case-1", json={"company_name": "Acme Pte Ltd"})

    assert response.status_code == 502


def test_update_case_missing_summary_after_write_returns_404() -> None:
    # The row was there a moment ago (the pre-check passed) but the summary
    # re-read came back empty — e.g. deleted concurrently.
    with (
        patch("app.routers._dependencies.case_service") as mock_dep_case_service,
        patch("app.routers.cases.case_service") as mock_case_service,
    ):
        mock_dep_case_service.get_case.return_value = _make_case("case-1")
        mock_case_service.get_case_summary.return_value = None

        response = client.patch("/cases/case-1", json={"company_name": "Acme Pte Ltd"})

    assert response.status_code == 404


# --- DELETE /cases/{case_id} ---------------------------------------------
#
# The documents-then-case cascade now lives entirely inside
# case_service.delete_case (see test_case_service.py for that behaviour) —
# the router itself is a thin wrapper behind Depends(get_case_or_404), so
# these only assert the wrapper's own responsibilities: 404 gating and
# delegating to case_service.delete_case.


def test_delete_case_calls_case_service_delete_case() -> None:
    with (
        patch("app.routers._dependencies.case_service") as mock_dep_case_service,
        patch("app.routers.cases.case_service") as mock_case_service,
    ):
        mock_dep_case_service.get_case.return_value = _make_case("case-1")
        mock_case_service.delete_case.return_value = []

        response = client.delete("/cases/case-1")

    assert response.status_code == 204
    assert response.content == b""
    mock_case_service.delete_case.assert_called_once_with("case-1")


def test_deleting_a_case_puts_each_of_its_documents_on_the_record_too(published_events) -> None:
    documents = [
        DocumentRecord(id=f"doc-{n}", case_id="case-1", filename=f"file-{n}.pdf", status=DocumentStatus.DETECTED,
                       uploaded_at=datetime.now(timezone.utc))
        for n in (1, 2)
    ]
    with (
        patch("app.routers._dependencies.case_service") as mock_dep_case_service,
        patch("app.routers.cases.case_service") as mock_case_service,
    ):
        mock_dep_case_service.get_case.return_value = _make_case("case-1")
        mock_case_service.delete_case.return_value = documents

        assert client.delete("/cases/case-1").status_code == 204

    deleted = [e for e in published_events if e.type == "document.deleted"]
    assert sorted((e.document_id, e.detail["filename"]) for e in deleted) == [
        ("doc-1", "file-1.pdf"), ("doc-2", "file-2.pdf"),
    ]
    assert [e.detail["documents"] for e in published_events if e.type == "case.deleted"] == [2]


def test_delete_case_not_found() -> None:
    with (
        patch("app.routers._dependencies.case_service") as mock_dep_case_service,
        patch("app.routers.cases.case_service") as mock_case_service,
    ):
        mock_dep_case_service.get_case.return_value = None

        response = client.delete("/cases/missing-id")

    assert response.status_code == 404
    mock_case_service.delete_case.assert_not_called()


def test_delete_case_refused_while_something_runs_is_409() -> None:
    with (
        patch("app.routers._dependencies.case_service") as mock_dep_case_service,
        patch("app.routers.cases.case_service") as mock_case_service,
    ):
        mock_dep_case_service.get_case.return_value = _make_case("case-1")
        mock_case_service.delete_case.side_effect = CaseNotDeletable("A document is still being scanned.")

        response = client.delete("/cases/case-1")

    assert response.status_code == 409
    assert response.json()["detail"] == "A document is still being scanned."


def test_delete_case_db_failure_returns_502() -> None:
    with (
        patch("app.routers._dependencies.case_service") as mock_dep_case_service,
        patch("app.routers.cases.case_service") as mock_case_service,
    ):
        mock_dep_case_service.get_case.return_value = _make_case("case-1")
        mock_case_service.delete_case.side_effect = RuntimeError("connection refused")

        response = client.delete("/cases/case-1")

    assert response.status_code == 502
