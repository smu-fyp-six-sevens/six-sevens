from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.models import CaseDocumentsStatus, CaseRecord, CaseSummary


client = TestClient(app)


def _make_case(case_id: str = "case-1", company_name: str | None = None) -> CaseRecord:
    return CaseRecord(
        id=case_id,
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
    with patch("app.routers.cases.case_repository") as mock_repo:
        mock_repo.create_case.return_value = _make_case("new-case")

        response = client.post("/cases")

    assert response.status_code == 201
    assert response.json()["id"] == "new-case"
    mock_repo.create_case.assert_called_once_with(None)


def test_create_case_with_company_name() -> None:
    with patch("app.routers.cases.case_repository") as mock_repo:
        mock_repo.create_case.return_value = _make_case("new-case", company_name="Acme Pte Ltd")

        response = client.post("/cases", json={"company_name": "Acme Pte Ltd"})

    assert response.status_code == 201
    assert response.json()["company_name"] == "Acme Pte Ltd"
    mock_repo.create_case.assert_called_once_with("Acme Pte Ltd")


def test_get_case_not_found() -> None:
    with patch("app.routers.cases.case_status") as mock_case_status:
        mock_case_status.get_case_summary.return_value = None

        response = client.get("/cases/missing-id")

    assert response.status_code == 404


def test_get_case_returns_summary_with_documents_status() -> None:
    with patch("app.routers.cases.case_status") as mock_case_status:
        mock_case_status.get_case_summary.return_value = _make_summary(
            "case-1", CaseDocumentsStatus.STUCK
        )

        response = client.get("/cases/case-1")

    assert response.status_code == 200
    assert response.json()["documents_status"] == "stuck"


def test_create_case_db_failure_returns_502() -> None:
    with patch("app.routers.cases.case_repository") as mock_repo:
        mock_repo.create_case.side_effect = RuntimeError("connection refused")

        response = client.post("/cases")

    assert response.status_code == 502


def test_get_case_db_failure_returns_502() -> None:
    with patch("app.routers.cases.case_status") as mock_case_status:
        mock_case_status.get_case_summary.side_effect = RuntimeError("connection refused")

        response = client.get("/cases/case-1")

    assert response.status_code == 502


def test_list_cases() -> None:
    with patch("app.routers.cases.case_status") as mock_case_status:
        mock_case_status.list_case_summaries.return_value = [
            _make_summary("case-1", CaseDocumentsStatus.ANALYZED),
            _make_summary("case-2", CaseDocumentsStatus.STUCK),
        ]

        response = client.get("/cases")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[1]["documents_status"] == "stuck"


def test_list_cases_db_failure_returns_502() -> None:
    with patch("app.routers.cases.case_status") as mock_case_status:
        mock_case_status.list_case_summaries.side_effect = RuntimeError("connection refused")

        response = client.get("/cases")

    assert response.status_code == 502
