from __future__ import annotations

from unittest.mock import MagicMock, patch

from app import case_repository


def _execute_result(data: list[dict]) -> MagicMock:
    result = MagicMock()
    result.data = data
    return result


def _row(**overrides) -> dict:
    row = {
        "id": "case-1",
        "company_name": None,
        "status": "open",
        "submission_finalized": False,
        "finalized_at": None,
        "recommended_additional_documents": [],
        "created_at": "2026-08-26T00:00:00Z",
        "updated_at": "2026-08-26T00:00:00Z",
    }
    row.update(overrides)
    return row


def test_create_case_inserts_expected_row_and_returns_record() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        mock_client.table.return_value.insert.return_value.execute.return_value = _execute_result(
            [_row()]
        )

        result = case_repository.create_case()

        assert result.id == "case-1"
        assert result.company_name is None
        mock_client.table.assert_called_with("cases")
        inserted_row = mock_client.table.return_value.insert.call_args[0][0]
        assert inserted_row["company_name"] is None
        assert inserted_row["status"] == "open"
        assert inserted_row["submission_finalized"] is False
        assert inserted_row["recommended_additional_documents"] == []


def test_create_case_with_company_name() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        mock_client.table.return_value.insert.return_value.execute.return_value = _execute_result(
            [_row(company_name="Acme Pte Ltd")]
        )

        result = case_repository.create_case(company_name="Acme Pte Ltd")

        assert result.company_name == "Acme Pte Ltd"
        inserted_row = mock_client.table.return_value.insert.call_args[0][0]
        assert inserted_row["company_name"] == "Acme Pte Ltd"


def test_get_case_returns_none_when_missing() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = (
            _execute_result([])
        )

        assert case_repository.get_case("missing") is None


def test_get_case_returns_record_when_found() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = (
            _execute_result([_row()])
        )

        result = case_repository.get_case("case-1")

        assert result is not None
        assert result.id == "case-1"
        mock_client.table.return_value.select.return_value.eq.assert_called_once_with(
            "id", "case-1"
        )


def test_list_cases_returns_all_rows() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        mock_client.table.return_value.select.return_value.execute.return_value = (
            _execute_result([_row(id="case-1"), _row(id="case-2")])
        )

        results = case_repository.list_cases()

        assert len(results) == 2
        assert {c.id for c in results} == {"case-1", "case-2"}
