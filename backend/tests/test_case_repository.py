from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app import case_repository


def _execute_result(data: list[dict]) -> MagicMock:
    result = MagicMock()
    result.data = data
    return result


def _row(**overrides) -> dict:
    row = {
        "id": "case-1",
        "greenlink_case_id": "GLDB-1",
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
        assert result.greenlink_case_id == "GLDB-1"
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


def test_set_submission_finalized_true_sets_finalized_at() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        case_repository.set_submission_finalized("case-1", True)

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload["submission_finalized"] is True
        assert update_payload["finalized_at"] is not None
        mock_client.table.return_value.update.return_value.eq.assert_called_once_with(
            "id", "case-1"
        )


def test_set_submission_finalized_false_clears_finalized_at() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        case_repository.set_submission_finalized("case-1", False)

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload == {"submission_finalized": False, "finalized_at": None}


def test_list_cases_returns_every_case() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        query = mock_client.table.return_value.select.return_value
        query.execute.return_value = _execute_result([_row()])

        cases = case_repository.list_cases()

        assert len(cases) == 1
        query.eq.assert_not_called()


def test_update_case_writes_company_name_and_touches_updated_at() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = (
            _execute_result([_row(company_name="Acme Pte Ltd")])
        )

        result = case_repository.update_case("case-1", "Acme Pte Ltd")

        assert result is not None
        assert result.company_name == "Acme Pte Ltd"
        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload["company_name"] == "Acme Pte Ltd"
        # Stamped server-side rather than trusted from the client.
        assert update_payload["updated_at"] is not None
        mock_client.table.return_value.update.return_value.eq.assert_called_once_with(
            "id", "case-1"
        )


def test_update_case_returns_none_when_no_row_matched() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = (
            _execute_result([])
        )

        assert case_repository.update_case("missing-id", "Acme Pte Ltd") is None


def test_delete_case_deletes_the_row() -> None:
    with patch("app.case_repository.service_supabase_client") as mock_service:
        mock_client = mock_service.return_value
        mock_client.table.return_value.delete.return_value.eq.return_value.execute.return_value = (
            _execute_result([_row()])
        )

        case_repository.delete_case("case-1")

    # What hangs off the case first (a foreign key can block the case delete), then the case itself.
    assert [c.args for c in mock_client.table.call_args_list] == [
        ("risk_scores",), ("case_analysis_runs",), ("flags",), ("case_notes",), ("cases",),
    ]
    assert [c.args for c in mock_client.table.return_value.delete.return_value.eq.call_args_list] == [
        ("case_id", "case-1"), ("case_id", "case-1"), ("case_id", "case-1"), ("case_id", "case-1"), ("id", "case-1"),
    ]
    assert mock_client.table.return_value.delete.return_value.eq.return_value.execute.call_count == 5


def test_delete_case_raises_when_no_row_was_removed() -> None:
    with patch("app.case_repository.service_supabase_client") as mock_service:
        mock_service.return_value.table.return_value.delete.return_value.eq.return_value.execute.return_value = (
            _execute_result([])
        )

        with pytest.raises(RuntimeError, match="was not deleted"):
            case_repository.delete_case("case-1")
