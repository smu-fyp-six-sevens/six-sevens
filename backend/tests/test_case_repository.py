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


def test_the_risk_filter_and_order_come_before_the_date_order() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        query = mock_client.table.return_value.select.return_value
        ranked = query.in_.return_value
        result = _execute_result([])
        result.count = 0
        ranked.order.return_value.order.return_value.order.return_value.range.return_value.execute.return_value = result

        case_repository.list_cases_page(None, newest=True, limit=25, offset=0, risk_ranks=[3, 4], highest_risk_first=True)

    query.in_.assert_called_once_with("risk_rank", [3, 4])
    ranked.order.assert_called_once_with("risk_rank", desc=True)
    ranked.order.return_value.order.assert_called_once_with("created_at", desc=True)


def test_stage_counts_come_from_the_database_view() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        mock_client.table.return_value.select.return_value.execute.return_value = _execute_result(
            [{"status": "open", "cases": 2}, {"status": "finalised", "cases": 5}]
        )

        assert case_repository.count_cases_by_status() == {"open": 2, "finalised": 5}

    mock_client.table.assert_called_once_with("case_stage_counts")


def test_unfinalised_case_ids_are_read_a_page_at_a_time() -> None:
    with (
        patch("app.case_repository.supabase_client") as mock_client,
        patch("app.case_repository.fetch_all", side_effect=lambda build, order: (build(), [{"id": "a"}, {"id": "b"}])[1]),
    ):
        assert case_repository.list_unfinalised_case_ids() == ["a", "b"]

    mock_client.table.return_value.select.return_value.neq.assert_called_once_with("status", "finalised")


def test_a_request_reads_its_case_once_and_again_after_a_write() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        select = mock_client.table.return_value.select.return_value.eq.return_value
        select.execute.return_value = _execute_result([_row(id="case-1")])
        with case_repository.request_scope():
            case_repository.get_case("case-1")
            case_repository.get_case("case-1")
            case_repository.set_status("case-1", "open", "processing_documents")
            case_repository.get_case("case-1")
        case_repository.get_case("case-1")  # outside a request: always read

    assert select.execute.call_count == 3


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
