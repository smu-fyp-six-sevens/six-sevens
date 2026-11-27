from __future__ import annotations

from datetime import datetime, timedelta, timezone

from unittest.mock import patch

from app import audit, case_service
from app.events import types
from app.models import (
    AdditionalDocument, AdditionalDocuments, CaseRecord, CaseStage, DocumentRecord, DocumentStatus,
)


def _row(case_id: str, action: str, ms: int, at: str = "2026-10-01T00:00:00Z") -> dict:
    return {"id": f"{case_id}-{action}-{at}-{ms}", "case_id": case_id, "action": action, "duration_ms": ms, "occurred_at": at}


def test_a_case_total_is_the_time_its_runs_had_it_in_work() -> None:
    rows = [
        # Two documents extracted side by side (0-60 s and 0-80 s): 80 s, counted once.
        _row("case-1", types.DOCUMENT_ANALYZED, 60_000, at="2026-10-01T00:01:00Z"),
        _row("case-1", types.DOCUMENT_ANALYZED, 80_000, at="2026-10-01T00:01:20Z"),
        # The officer approves 9 minutes later: not counted. Detection (600-630 s), then the analysis (630-720 s).
        _row("case-1", types.DOCUMENT_DETECTED, 30_000, at="2026-10-01T00:10:30Z"),
        _row("case-1", types.CASE_ANALYSIS_COMPLETED, 90_000, at="2026-10-01T00:12:00Z"),
        # A later round (another upload, a rerun): its own 20 s, not the day between.
        _row("case-1", types.CASE_ANALYSIS_COMPLETED, 20_000, at="2026-10-02T00:00:00Z"),
        # Extracted but never analysed: no total yet.
        _row("case-2", types.DOCUMENT_ANALYZED, 50_000),
        # Analysed, but its documents ran before timings were recorded: no partial total.
        _row("case-3", types.CASE_ANALYSIS_COMPLETED, 22_000),
    ]
    with patch("app.audit.fetch_all", return_value=rows), patch("app.audit.service_supabase_client"):
        totals = audit.processing_ms_many(["case-1", "case-2", "case-3"])

    assert totals == {"case-1": 80_000 + 120_000 + 20_000}


def _run(end_s: int, ms: int) -> tuple[datetime, int]:
    return datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(seconds=end_s), ms


def test_busy_ms_counts_overlapping_runs_once() -> None:
    # 0-60 s and 20-80 s overlap: one 80 s stretch.
    assert audit.busy_ms([_run(60, 60_000), _run(80, 60_000)]) == 80_000


def test_busy_ms_joins_runs_that_touch() -> None:
    assert audit.busy_ms([_run(30, 30_000), _run(90, 60_000)]) == 90_000


def test_busy_ms_does_not_count_the_gap_between_runs() -> None:
    assert audit.busy_ms([_run(10, 10_000), _run(100, 10_000)]) == 20_000


def test_busy_ms_of_one_run_is_its_duration_and_of_none_is_zero() -> None:
    assert audit.busy_ms([_run(50, 12_345)]) == 12_345
    assert audit.busy_ms([]) == 0


def test_the_case_list_carries_the_processing_time_and_creator_stored_on_the_case() -> None:
    case = CaseRecord(
        id="case-1", greenlink_case_id="GLDB-1", created_at="2026-10-01T00:00:00Z", updated_at="2026-10-01T00:00:00Z",
        processing_ms=184_000, created_by="greenlink-tester",
    )
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.risk_store") as store,
    ):
        case_repo.list_cases_page.return_value = ([case], 1)
        doc_repo.list_document_statuses.return_value = []
        store.latest_many.return_value = {}

        page = case_service.list_cases_page(limit=25, offset=0, sort="newest", statuses=[], q=None)

    assert page.items[0].processing_ms == 184_000 and page.items[0].created_by == "greenlink-tester"
    # Only the list returns them: the case itself doesn't.
    assert "processing_ms" not in case.model_dump() and "created_by" not in case.model_dump()


async def test_a_timed_run_refreshes_the_case_processing_time() -> None:
    from app.events import Event

    event = Event(type=types.DOCUMENT_DETECTED, case_id="11111111-1111-1111-1111-111111111111", duration_ms=30_000)
    with (
        patch("app.audit.store"),
        patch("app.audit.processing_ms_many", return_value={"11111111-1111-1111-1111-111111111111": 120_000}),
        patch("app.audit.case_repository") as case_repo,
    ):
        await audit.on_event(event)
        await audit.on_event(Event(type=types.CASE_RENAMED, case_id="11111111-1111-1111-1111-111111111111"))

    case_repo.set_processing_ms.assert_called_once_with("11111111-1111-1111-1111-111111111111", 120_000)


def test_the_case_list_carries_the_titles_its_analysis_recommends() -> None:
    stamp = "2026-10-01T00:00:00Z"
    analysed = CaseRecord(
        id="case-1", greenlink_case_id="GLDB-1", created_at=stamp, updated_at=stamp,
        additional_documents=AdditionalDocuments(items=[
            AdditionalDocument(title="NRIC / passport", why="Identity mismatch", source="finding"),
            AdditionalDocument(title="Recent proof of address", why="Checklist", source="checklist"),
        ]),
    )
    never = CaseRecord(id="case-2", greenlink_case_id="GLDB-2", created_at=stamp, updated_at=stamp)
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.risk_store") as store,
    ):
        case_repo.list_cases_page.return_value = ([analysed, never], 2)
        doc_repo.list_document_statuses.return_value = []
        store.latest_many.return_value = {}

        page = case_service.list_cases_page(limit=25, offset=0, sort="newest", statuses=[], q=None)

    assert [item.recommended_documents for item in page.items] == [["NRIC / passport", "Recent proof of address"], None]


def _document(document_id: str, document_type: str | None) -> DocumentRecord:
    return DocumentRecord(
        id=document_id, case_id="case-1", filename=f"{document_id}.pdf", document_type=document_type,
        status=DocumentStatus.ANALYZED, uploaded_at="2026-10-01T00:00:00Z",
    )


def test_the_case_list_carries_its_document_types_and_flags_grouped_by_check() -> None:
    case = CaseRecord(id="case-1", greenlink_case_id="GLDB-1", created_at="2026-10-01T00:00:00Z", updated_at="2026-10-01T00:00:00Z")
    flag = lambda doc, check, severity: {"id": f"{doc}-{check}-{severity}", "case_id": "case-1", "document_id": doc, "check_name": check, "severity": severity}  # noqa: E731
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.risk_store") as store,
        patch("app.case_service.findings_repository") as flags,
    ):
        case_repo.list_cases_page.return_value = ([case], 1)
        doc_repo.list_document_statuses.return_value = [
            _document("doc-1", "payslip"), _document("doc-2", "bank_statement"), _document("doc-3", None),
        ]
        store.latest_many.return_value = {}
        flags.list_flags_for_cases.return_value = [
            flag("doc-1", "spelling_tool", "low"),
            flag("doc-2", "numerical_checks_tool", "medium"), flag("doc-1", "numerical_checks_tool", "high"),
            flag("deleted-doc", "metadata_tool", "high"),  # its document is gone: not counted
        ]

        page = case_service.list_cases_page(limit=25, offset=0, sort="newest", statuses=[], q=None)

    item = page.items[0]
    assert item.document_types == ["bank_statement", "payslip"]
    assert [(f.check, f.severity, f.count, f.document_types) for f in item.flags] == [
        ("numerical_checks_tool", "high", 2, ["bank_statement", "payslip"]),
        ("spelling_tool", "low", 1, ["payslip"]),
    ]


def test_the_accuracy_filters_join_each_review_to_its_case_in_the_database() -> None:
    from app import findings_repository

    with (
        patch("app.findings_repository.service_supabase_client") as client,
        patch("app.findings_repository.fetch_all", side_effect=lambda build, order: (build(), [])[1]),
    ):
        findings_repository.list_reviews(datetime(2026, 10, 1), [CaseStage.CASE_ANALYSED.value])

    select = client.return_value.table.return_value.select
    assert "cases!inner(id)" in select.call_args.args[0]
    reviewed = select.return_value.not_.is_.return_value
    # A time without a zone is UTC.
    reviewed.gte.assert_called_once_with("cases.created_at", "2026-10-01T00:00:00.000000Z")
    reviewed.gte.return_value.in_.assert_called_once_with("cases.status", ["case_analysed"])


def test_the_accuracy_window_is_the_officers_days_not_utcs() -> None:
    # 1 to 3 Nov in Singapore: a case created at 7am on 1 Nov there (23:00 UTC on 31 Oct) is in it, as the
    # dashboard's case counts have it.
    from app import findings_repository

    singapore = timezone(timedelta(hours=8))
    with (
        patch("app.findings_repository.service_supabase_client") as client,
        patch("app.findings_repository.fetch_all", side_effect=lambda build, order: (build(), [])[1]),
    ):
        findings_repository.list_reviews(datetime(2026, 11, 1, tzinfo=singapore), [], datetime(2026, 11, 4, tzinfo=singapore))

    reviewed = client.return_value.table.return_value.select.return_value.not_.is_.return_value
    reviewed.gte.assert_called_once_with("cases.created_at", "2026-10-31T16:00:00.000000Z")
    reviewed.gte.return_value.lt.assert_called_once_with("cases.created_at", "2026-11-03T16:00:00.000000Z")



def test_a_case_cost_is_added_up_by_the_database_over_every_costed_run_type() -> None:
    # The sums come from case_cost_totals; a case with no costed run is absent from its answer.
    answer = [{"case_id": "case-1", "cost_usd": 0.15}, {"case_id": "case-2", "cost_usd": 0.5}]
    with patch("app.audit.service_supabase_client") as client:
        client.return_value.rpc.return_value.execute.return_value.data = answer
        totals = audit.cost_usd_many(["case-1", "case-2", "case-3"])

    assert totals == {"case-1": 0.15, "case-2": 0.5}
    name, params = client.return_value.rpc.call_args.args
    assert name == "case_cost_totals"
    assert params["p_case_ids"] == ["case-1", "case-2", "case-3"] and set(params["p_actions"]) == set(audit._COSTED)


def test_a_long_list_of_cases_is_asked_in_pieces() -> None:
    ids = [f"case-{n}" for n in range(1200)]
    with patch("app.audit.service_supabase_client") as client:
        client.return_value.rpc.return_value.execute.return_value.data = []
        assert audit.cost_usd_many(ids) == {}

    assert [len(call.args[1]["p_case_ids"]) for call in client.return_value.rpc.call_args_list] == [500, 500, 200]
