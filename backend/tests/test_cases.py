from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.models import CaseRecord


client = TestClient(app)


def _make_case(case_id: str = "case-1") -> CaseRecord:
    return CaseRecord(
        id=case_id,
        status="open",
        submission_finalized=False,
        recommended_additional_documents=[],
        created_at="2026-08-26T00:00:00Z",
        updated_at="2026-08-26T00:00:00Z",
    )


def test_create_case() -> None:
    with patch("app.routers.cases.case_repository") as mock_repo:
        mock_repo.create_case.return_value = _make_case("new-case")

        response = client.post("/cases")

    assert response.status_code == 201
    assert response.json()["id"] == "new-case"


def test_get_case_not_found() -> None:
    with patch("app.routers.cases.case_repository") as mock_repo:
        mock_repo.get_case.return_value = None

        response = client.get("/cases/missing-id")

    assert response.status_code == 404
