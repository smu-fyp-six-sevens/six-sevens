from __future__ import annotations

from datetime import date

from unittest.mock import patch

from app import audit, case_service
from app.events import types
from app.models import (
    AdditionalDocument, AdditionalDocuments, CaseDocumentsStatus, CaseRecord, DocumentRecord, DocumentStatus,
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


def test_the_case_list_carries_each_case_processing_time() -> None:
    case = CaseRecord(id="case-1", greenlink_case_id="GLDB-1", created_at="2026-10-01T00:00:00Z", updated_at="2026-10-01T00:00:00Z")
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.risk_store") as store,
        patch("app.case_service.audit") as trail,
    ):
        case_repo.list_cases_page.return_value = ([case], 1)
        doc_repo.list_document_statuses.return_value = []
        store.latest_many.return_value = {}
        trail.processing_ms_many.return_value = {"case-1": 184_000}
        trail.created_by_many.return_value = {}

        page = case_service.list_cases_page(limit=25, offset=0, sort="newest", statuses=[], q=None)

    assert page.items[0].processing_ms == 184_000


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
        patch("app.case_service.audit") as trail,
    ):
        case_repo.list_cases_page.return_value = ([analysed, never], 2)
        doc_repo.list_document_statuses.return_value = []
        store.latest_many.return_value = {}
        trail.processing_ms_many.return_value = {}
        trail.created_by_many.return_value = {}

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
        patch("app.case_service.audit") as trail,
        patch("app.case_service.flags_repository") as flags,
    ):
        case_repo.list_cases_page.return_value = ([case], 1)
        doc_repo.list_document_statuses.return_value = [
            _document("doc-1", "payslip"), _document("doc-2", "bank_statement"), _document("doc-3", None),
        ]
        store.latest_many.return_value = {}
        trail.processing_ms_many.return_value = {}
        trail.created_by_many.return_value = {}
        flags.list_for_cases.return_value = [
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


def test_the_accuracy_filters_pick_cases_by_creation_date_and_documents_status() -> None:
    def case(case_id: str, created: str) -> CaseRecord:
        return CaseRecord(id=case_id, greenlink_case_id=case_id.upper(), created_at=created, updated_at=created)

    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
    ):
        case_repo.list_matching_cases.return_value = [
            case("new-ready", "2026-10-20T00:00:00Z"), case("new-empty", "2026-10-21T00:00:00Z"),
            case("old-ready", "2026-09-01T00:00:00Z"),
        ]
        doc_repo.list_document_statuses.return_value = [
            DocumentRecord(id="d1", case_id="new-ready", filename="a.pdf", status=DocumentStatus.ANALYZED, uploaded_at="2026-10-20T00:00:00Z"),
        ]

        recent = case_service.case_ids_matching(date(2026, 10, 1), [])
        recent_ready = case_service.case_ids_matching(date(2026, 10, 1), [CaseDocumentsStatus.ANALYZED])

    assert recent == {"new-ready", "new-empty"}
    assert recent_ready == {"new-ready"}


def test_a_case_creator_is_the_officer_behind_its_case_created_event() -> None:
    rows = [
        {"id": "e1", "case_id": "case-1", "actor": "greenlink-tester"},
        {"id": "e2", "case_id": "case-2", "actor": ""},
    ]
    with patch("app.audit.fetch_all", return_value=rows), patch("app.audit.service_supabase_client"):
        assert audit.created_by_many(["case-1", "case-2", "case-3"]) == {"case-1": "greenlink-tester"}
