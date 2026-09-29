from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.models import CaseDocumentsStatus, CaseRecord, CaseSummary


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
        recommended_additional_documents=[],
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
    mock_case_service.create_case.assert_called_once_with(None)


def test_create_case_endpoint_with_company_name() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.create_case.return_value = _make_case("new-case", company_name="Acme Pte Ltd")

        response = client.post("/cases", json={"company_name": "Acme Pte Ltd"})

    assert response.status_code == 201
    assert response.json()["company_name"] == "Acme Pte Ltd"
    mock_case_service.create_case.assert_called_once_with("Acme Pte Ltd")


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


def test_list_cases() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.list_case_summaries.return_value = [
            _make_summary("case-1", CaseDocumentsStatus.ANALYZED),
            _make_summary("case-2", CaseDocumentsStatus.STUCK),
        ]

        response = client.get("/cases")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[1]["documents_status"] == "stuck"


def test_list_cases_db_failure_returns_502() -> None:
    with patch("app.routers.cases.case_service") as mock_case_service:
        mock_case_service.list_case_summaries.side_effect = RuntimeError("connection refused")

        response = client.get("/cases")

    assert response.status_code == 502


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

        response = client.delete("/cases/case-1")

    assert response.status_code == 204
    assert response.content == b""
    mock_case_service.delete_case.assert_called_once_with("case-1")


def test_delete_case_not_found() -> None:
    with (
        patch("app.routers._dependencies.case_service") as mock_dep_case_service,
        patch("app.routers.cases.case_service") as mock_case_service,
    ):
        mock_dep_case_service.get_case.return_value = None

        response = client.delete("/cases/missing-id")

    assert response.status_code == 404
    mock_case_service.delete_case.assert_not_called()


def test_delete_case_db_failure_returns_502() -> None:
    with (
        patch("app.routers._dependencies.case_service") as mock_dep_case_service,
        patch("app.routers.cases.case_service") as mock_case_service,
    ):
        mock_dep_case_service.get_case.return_value = _make_case("case-1")
        mock_case_service.delete_case.side_effect = RuntimeError("connection refused")

        response = client.delete("/cases/case-1")

    assert response.status_code == 502
