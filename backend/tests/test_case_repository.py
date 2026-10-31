from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from postgrest import CountMethod

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
        "checklist_enforced": False,
        "reviews_required": True,
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
        # Set once, at creation.
        assert inserted_row["checklist_enforced"] is False
        assert inserted_row["reviews_required"] is True
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


def test_a_page_of_cases_is_ordered_ranged_and_counted_by_the_database() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        query = mock_client.table.return_value.select.return_value
        result = _execute_result([_row(id="case-26"), _row(id="case-27")])
        result.count = 51
        query.order.return_value.order.return_value.range.return_value.execute.return_value = result

        cases, total = case_repository.list_cases_page(None, newest=True, limit=25, offset=25)

    assert [c.id for c in cases] == ["case-26", "case-27"]
    assert total == 51
    mock_client.table.return_value.select.assert_called_once_with("*", count=CountMethod.exact)
    query.order.assert_called_once_with("created_at", desc=True)
    query.order.return_value.order.assert_called_once_with("id")  # a tie-break, so pages never overlap
    query.order.return_value.order.return_value.range.assert_called_once_with(25, 49)
    query.or_.assert_not_called()


def test_a_search_matches_the_number_or_the_name_and_quotes_what_it_was_given() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        query = mock_client.table.return_value.select.return_value
        result = _execute_result([])
        result.count = None
        query.or_.return_value.order.return_value.order.return_value.range.return_value.execute.return_value = result

        cases, total = case_repository.list_cases_page('Acme, (SG) "x"', newest=False, limit=10, offset=0)

    assert cases == [] and total == 0
    query.or_.assert_called_once_with(
        'company_name.ilike."*Acme, (SG) \\"x\\"*",greenlink_case_id.ilike."*Acme, (SG) \\"x\\"*"'
    )
    query.or_.return_value.order.assert_called_once_with("created_at", desc=False)


def test_every_matching_case_comes_back_newest_first() -> None:
    rows = [
        _row(id="case-1", created_at="2026-10-01T00:00:00Z"),
        _row(id="case-2", created_at="2026-10-03T00:00:00Z"),
        _row(id="case-3", created_at="2026-10-02T00:00:00Z"),
    ]
    with patch("app.case_repository.fetch_all", return_value=rows) as fetch:
        cases = case_repository.list_matching_cases(None)

    assert [c.id for c in cases] == ["case-2", "case-3", "case-1"]
    assert fetch.call_args.args[1] == "id"  # paged by a unique key


def test_case_ids_are_read_a_page_at_a_time() -> None:
    with patch("app.case_repository.fetch_all", return_value=[{"id": "a"}, {"id": "b"}]):
        assert case_repository.list_case_ids() == ["a", "b"]


def test_a_case_is_found_by_its_number_or_else_its_id() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        select = mock_client.table.return_value.select.return_value
        select.eq.return_value.limit.return_value.execute.side_effect = [
            _execute_result([]), _execute_result([_row(id="case-7")]),
        ]

        found = case_repository.get_case_by_number("case-7")

    assert found is not None and found.id == "case-7"
    assert [call.args for call in select.eq.call_args_list] == [("greenlink_case_id", "case-7"), ("id", "case-7")]


def test_an_unknown_number_is_none() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        select = mock_client.table.return_value.select.return_value
        select.eq.return_value.limit.return_value.execute.return_value = _execute_result([])

        assert case_repository.get_case_by_number("GLDB-404") is None


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


def test_delete_case_deletes_only_the_case_row_which_cascades() -> None:
    with patch("app.case_repository.service_supabase_client") as mock_service:
        mock_client = mock_service.return_value
        mock_client.table.return_value.delete.return_value.eq.return_value.execute.return_value = (
            _execute_result([_row()])
        )

        case_repository.delete_case("case-1")

    # Every table keyed by the case cascades from it (backend/sql/schema.sql).
    assert [c.args for c in mock_client.table.call_args_list] == [("cases",)]
    mock_client.table.return_value.delete.return_value.eq.assert_called_once_with("id", "case-1")


def test_delete_case_raises_when_no_row_was_removed() -> None:
    with patch("app.case_repository.service_supabase_client") as mock_service:
        mock_service.return_value.table.return_value.delete.return_value.eq.return_value.execute.return_value = (
            _execute_result([])
        )

        with pytest.raises(RuntimeError, match="was not deleted"):
            case_repository.delete_case("case-1")
