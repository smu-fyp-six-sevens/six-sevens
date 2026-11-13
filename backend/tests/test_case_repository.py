"""
The cases table (case_repository.py), on an in-memory table (tests/support/fake_supabase.py): rows are seeded, the
repository is called, and the test asserts what is stored and what comes back. The search test stays on a mock:
it checks the exact filter text the database is given, which the fake does not run.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app import case_repository
from tests.support.fake_supabase import FakeSupabase


@pytest.fixture
def db(monkeypatch) -> FakeSupabase:
    fake = FakeSupabase(defaults={"cases": {"greenlink_case_id": "GLDB-1", "updated_at": "2026-08-26T00:00:00Z"}})
    monkeypatch.setattr(case_repository, "supabase_client", fake)
    monkeypatch.setattr(case_repository, "service_supabase_client", lambda: fake)
    return fake


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
        "risk_rank": 0,
        "created_at": "2026-08-26T00:00:00Z",
        "updated_at": "2026-08-26T00:00:00Z",
    }
    row.update(overrides)
    return row


def _stored(db: FakeSupabase, case_id: str = "case-1") -> dict:
    [row] = db.rows("cases", id=case_id)
    return row


def test_create_case_stores_an_open_case_with_its_settings_fixed_at_creation(db) -> None:
    result = case_repository.create_case()

    stored = _stored(db, result.id)
    assert (stored["company_name"], stored["status"]) == (None, "open")
    # Set once, at creation.
    assert (stored["checklist_enforced"], stored["reviews_required"]) == (False, True)
    assert stored["recommended_additional_documents"] == []
    assert (result.greenlink_case_id, result.company_name) == ("GLDB-1", None)


def test_create_case_with_company_name(db) -> None:
    result = case_repository.create_case(company_name="Acme Pte Ltd")

    assert result.company_name == "Acme Pte Ltd"
    assert _stored(db, result.id)["company_name"] == "Acme Pte Ltd"


def test_get_case_returns_none_when_missing(db) -> None:
    assert case_repository.get_case("missing") is None


def test_get_case_returns_record_when_found(db) -> None:
    db.add("cases", _row())
    db.add("cases", _row(id="case-2", greenlink_case_id="GLDB-2"))

    result = case_repository.get_case("case-2")

    assert result is not None and (result.id, result.greenlink_case_id) == ("case-2", "GLDB-2")


def test_a_page_of_cases_is_newest_first_with_the_total_counted_by_the_database(db) -> None:
    for number in range(1, 8):
        db.add("cases", _row(id=f"case-{number}", created_at=f"2026-08-{10 + number}T00:00:00Z"))

    cases, total = case_repository.list_cases_page(None, newest=True, limit=3, offset=2)

    assert [c.id for c in cases] == ["case-5", "case-4", "case-3"]
    assert total == 7


def test_the_oldest_first_order_and_the_stage_filter(db) -> None:
    db.add("cases", _row(id="case-1", status="open", created_at="2026-08-11T00:00:00Z"))
    db.add("cases", _row(id="case-2", status="finalised", created_at="2026-08-12T00:00:00Z"))
    db.add("cases", _row(id="case-3", status="open", created_at="2026-08-13T00:00:00Z"))

    cases, total = case_repository.list_cases_page(None, newest=False, limit=10, offset=0, statuses=["open"])

    assert [c.id for c in cases] == ["case-1", "case-3"] and total == 2


def test_the_risk_filter_and_order_come_before_the_date_order(db) -> None:
    db.add("cases", _row(id="case-low", risk_rank=1, created_at="2026-08-15T00:00:00Z"))
    db.add("cases", _row(id="case-high-old", risk_rank=3, created_at="2026-08-11T00:00:00Z"))
    db.add("cases", _row(id="case-critical", risk_rank=4, created_at="2026-08-12T00:00:00Z"))
    db.add("cases", _row(id="case-high-new", risk_rank=3, created_at="2026-08-14T00:00:00Z"))

    cases, total = case_repository.list_cases_page(
        None, newest=True, limit=25, offset=0, risk_ranks=[3, 4], highest_risk_first=True,
    )

    # Highest risk first, then newest within the same risk; the low-risk case is filtered out.
    assert [c.id for c in cases] == ["case-critical", "case-high-new", "case-high-old"] and total == 3


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


def test_stage_counts_come_from_the_database_view(db) -> None:
    db.add("case_stage_counts", {"status": "open", "cases": 2})
    db.add("case_stage_counts", {"status": "finalised", "cases": 5})

    assert case_repository.count_cases_by_status() == {"open": 2, "finalised": 5}


def test_unfinalised_case_ids_leave_out_the_finalised(db) -> None:
    db.add("cases", _row(id="a", status="open"))
    db.add("cases", _row(id="b", status="fraud_detection"))
    db.add("cases", _row(id="c", status="finalised"))

    assert sorted(case_repository.list_unfinalised_case_ids()) == ["a", "b"]


def test_a_request_reads_its_case_once_and_again_after_a_write(db) -> None:
    db.add("cases", _row(id="case-1"))
    reads: list[str] = []
    real_table = db.table

    def counted(name: str):
        reads.append(name)
        return real_table(name)

    db.table = counted  # type: ignore[method-assign]
    with case_repository.request_scope():
        case_repository.get_case("case-1")
        case_repository.get_case("case-1")  # served from this request's copy
        assert len(reads) == 1
        case_repository.set_status("case-1", "open", "processing_documents")
        reads_after_write = len(reads)
        case_repository.get_case("case-1")  # the write dropped the copy, so this one is read
        assert len(reads) == reads_after_write + 1
    case_repository.get_case("case-1")  # outside a request: always read

    assert _stored(db)["status"] == "processing_documents"


def test_a_case_is_found_by_its_number_or_else_its_id(db) -> None:
    db.add("cases", _row(id="case-7", greenlink_case_id="GLDB-7"))

    by_number = case_repository.get_case_by_number("GLDB-7")
    by_id = case_repository.get_case_by_number("case-7")

    assert by_number is not None and by_number.id == "case-7"
    assert by_id is not None and by_id.id == "case-7"


def test_an_exact_number_lookup_has_no_wildcards() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        query = mock_client.table.return_value.select.return_value
        query.or_.return_value.limit.return_value.execute.return_value = _execute_result([])

        assert case_repository.find_case_numbered("gldb-1%") is None

    query.or_.assert_called_once_with('greenlink_case_id.eq."GLDB-1%",id.eq."gldb-1%"')


def test_an_unknown_number_is_none(db) -> None:
    db.add("cases", _row())

    assert case_repository.get_case_by_number("GLDB-404") is None


def test_update_case_writes_company_name_and_touches_updated_at(db) -> None:
    db.add("cases", _row())

    result = case_repository.update_case("case-1", "Acme Pte Ltd")

    assert result is not None and result.company_name == "Acme Pte Ltd"
    stored = _stored(db)
    assert stored["company_name"] == "Acme Pte Ltd"
    # Stamped server-side rather than trusted from the client.
    assert stored["updated_at"] != "2026-08-26T00:00:00Z"


def test_update_case_returns_none_when_no_row_matched(db) -> None:
    assert case_repository.update_case("missing-id", "Acme Pte Ltd") is None


def test_delete_case_deletes_only_the_case_row_which_cascades(db) -> None:
    db.add("cases", _row(id="case-1"))
    db.add("cases", _row(id="case-2"))

    case_repository.delete_case("case-1")

    # Every table keyed by the case cascades from it (backend/sql/schema.sql).
    assert [row["id"] for row in db.rows("cases")] == ["case-2"]


def test_delete_case_raises_when_no_row_was_removed(db) -> None:
    with pytest.raises(RuntimeError, match="was not deleted"):
        case_repository.delete_case("case-1")
