from __future__ import annotations

from datetime import date

from unittest.mock import patch

from app import audit, case_service
from app.events import types
from app.models import (
    AdditionalDocument, AdditionalDocuments, CaseRecord, CaseStage, DocumentRecord, DocumentStatus,
)


def _row(case_id: str, action: str, ms: int, at: str = "2026-10-01T00:00:00Z") -> dict:
    return {"id": f"{case_id}-{action}-{at}-{ms}", "case_id": case_id, "action": action, "duration_ms": ms, "occurred_at": at}


def test_a_case_total_is_the_average_document_runs_plus_its_latest_analysis() -> None:
    rows = [
        _row("case-1", types.DOCUMENT_ANALYZED, 60_000), _row("case-1", types.DOCUMENT_ANALYZED, 80_000),
        _row("case-1", types.DOCUMENT_DETECTED, 30_000),
        _row("case-1", types.CASE_ANALYSIS_COMPLETED, 99_000, at="2026-10-01T00:00:00Z"),
        _row("case-1", types.CASE_ANALYSIS_COMPLETED, 20_000, at="2026-10-02T00:00:00Z"),
        # Extracted but never analysed: no total yet.
        _row("case-2", types.DOCUMENT_ANALYZED, 50_000),
        # Analysed, but its documents ran before timings were recorded: no partial total.
        _row("case-3", types.CASE_ANALYSIS_COMPLETED, 22_000),
    ]
    with patch("app.audit.fetch_all", return_value=rows), patch("app.audit.service_supabase_client"):
        totals = audit.processing_ms_many(["case-1", "case-2", "case-3"])

    assert totals == {"case-1": 70_000 + 30_000 + 20_000}


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
        findings_repository.list_reviews(date(2026, 10, 1), [CaseStage.CASE_ANALYSED.value])

    select = client.return_value.table.return_value.select
    assert "cases!inner(id)" in select.call_args.args[0]
    reviewed = select.return_value.not_.is_.return_value
    reviewed.gte.assert_called_once_with("cases.created_at", "2026-10-01")
    reviewed.gte.return_value.in_.assert_called_once_with("cases.status", ["case_analysed"])

