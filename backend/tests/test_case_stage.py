"""SCRUM-80: each case moves through clear stages, and every change records the stage, the time and the person."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import case_repository, case_service, case_stage, document_repository, realtime_listener
from app.case_broadcaster import CaseBroadcaster
from app.case_pipeline.models import CaseAnalysis
from app.case_service import is_case_finalised as real_is_case_finalised  # bound before conftest's stand-in
from app.case_stage import sync_case_stage as real_sync_case_stage  # bound before conftest's no-op
from app.main import app
from tests.conftest import TEST_USER
from app.models import (
    AdditionalDocument, AdditionalDocuments, CaseFailure, CaseRecord, CaseRisk, CaseStage, ContributingFinding, FindingReview, CaseStageChange, CaseSummary, DocumentRecord,
    DocumentStatus, RoutingStatus,
)

client = TestClient(app)


def _case(status: str = "open") -> CaseRecord:
    return CaseRecord(
        id="case-1", greenlink_case_id="GLDB-1", status=status,
        created_at="2026-10-19T00:00:00Z", updated_at="2026-10-19T00:00:00Z",
    )


def _document(status: DocumentStatus, doc_id: str = "doc-1") -> DocumentRecord:
    return DocumentRecord(
        id=doc_id, case_id="case-1", filename=f"{doc_id}.pdf", status=status,
        uploaded_at=datetime.now(timezone.utc),
    )


def _change(to_status: str, from_status: str | None = None, changed_by: str = "System") -> CaseStageChange:
    return CaseStageChange(
        id="change-1", case_id="case-1", from_status=from_status, to_status=to_status,
        changed_by=changed_by, changed_at=datetime.now(timezone.utc),
    )


def _analysis(status: str, fingerprint: str = "fp") -> CaseAnalysis:
    return CaseAnalysis(
        run_id="run-1", case_id="case-1", status=status, fingerprint=fingerprint,
        started_at=datetime.now(timezone.utc),
    )


# --- Which stage the case is at ------------------------------------------------------------


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], CaseStage.OPEN),
        ([DocumentStatus.CANCELLED], CaseStage.OPEN),
        ([DocumentStatus.UPLOADED], CaseStage.PROCESSING_DOCUMENTS),
        ([DocumentStatus.QUEUED, DocumentStatus.ANALYZED], CaseStage.PROCESSING_DOCUMENTS),
        ([DocumentStatus.PROCESSING, DocumentStatus.DETECTED], CaseStage.PROCESSING_DOCUMENTS),
        ([DocumentStatus.EXTRACTING, DocumentStatus.FAILED], CaseStage.PROCESSING_DOCUMENTS),
        ([DocumentStatus.FAILED, DocumentStatus.ANALYZED], CaseStage.FAILED),
        ([DocumentStatus.FAILED, DocumentStatus.DETECTED], CaseStage.FAILED),
        ([DocumentStatus.ANALYZED, DocumentStatus.ANALYZED], CaseStage.PENDING_REVIEW),
        ([DocumentStatus.ANALYZED, DocumentStatus.DETECTING], CaseStage.PENDING_REVIEW),
        ([DocumentStatus.REJECTED, DocumentStatus.REJECTED], CaseStage.DOCUMENTS_INCOMPLETE),
        ([DocumentStatus.REJECTED, DocumentStatus.CANCELLED], CaseStage.DOCUMENTS_INCOMPLETE),
        ([DocumentStatus.APPROVED, DocumentStatus.REJECTED], CaseStage.FRAUD_DETECTION),
        ([DocumentStatus.DETECTING, DocumentStatus.DETECTED], CaseStage.FRAUD_DETECTION),
        ([DocumentStatus.DETECTED, DocumentStatus.REJECTED], None),
    ],
)
def test_the_document_furthest_behind_decides_the_cases_stage(statuses, expected) -> None:
    documents = [_document(status, f"doc-{i}") for i, status in enumerate(statuses)]

    result = case_stage.document_stage(documents, missing_requirements=())

    assert (result.stage if result else None) == expected


def test_a_document_sent_to_manual_review_puts_the_case_in_pending_review() -> None:
    sent_to_review = _document(DocumentStatus.ANALYZED, "doc-1")
    sent_to_review.routing_status = RoutingStatus.MANUAL_REVIEW

    result = case_stage.document_stage([sent_to_review, _document(DocumentStatus.DETECTED, "doc-2")], missing_requirements=())

    assert result == case_stage.StageResult(CaseStage.PENDING_REVIEW)


def test_a_failure_says_which_step_failed() -> None:
    processing = case_stage.document_stage([_document(DocumentStatus.FAILED)], missing_requirements=())
    documents = [_document(DocumentStatus.APPROVED, "doc-1"), _document(DocumentStatus.DETECTED, "doc-2")]
    detection = case_stage.document_stage(documents, failed_detection_ids=frozenset({"doc-1"}), missing_requirements=())

    assert processing == case_stage.StageResult(CaseStage.FAILED, CaseFailure.DOCUMENT_PROCESSING)
    assert detection == case_stage.StageResult(CaseStage.FAILED, CaseFailure.FRAUD_DETECTION)
    # Waiting for its turn, not failed: still being scanned.
    assert case_stage.document_stage(documents, missing_requirements=()) == case_stage.StageResult(CaseStage.FRAUD_DETECTION)


@pytest.mark.parametrize("statuses", [
    [DocumentStatus.DETECTED],  # everything on file is scanned
    [DocumentStatus.APPROVED, DocumentStatus.DETECTING],  # still being scanned
])
def test_a_missing_required_document_makes_the_case_incomplete(statuses) -> None:
    documents = [_document(status, f"doc-{i}") for i, status in enumerate(statuses)]

    result = case_stage.document_stage(documents, missing_requirements=["bank_statement"])

    assert result == case_stage.StageResult(CaseStage.DOCUMENTS_INCOMPLETE)


def test_a_document_waiting_for_review_outranks_a_missing_required_document() -> None:
    documents = [_document(DocumentStatus.ANALYZED, "doc-1"), _document(DocumentStatus.DETECTED, "doc-2")]

    result = case_stage.document_stage(documents, missing_requirements=["bank_statement"])

    assert result == case_stage.StageResult(CaseStage.PENDING_REVIEW)


def test_derive_reads_the_missing_requirements_from_the_checklist() -> None:
    with (
        patch("app.case_stage.document_repository.list_documents",
              return_value=[_document(DocumentStatus.DETECTED)]),
        patch("app.case_stage.compute_requirement_coverage", return_value=(["payslip"], ["bank_statement"])),
    ):
        assert case_stage.derive(_case()) == case_stage.StageResult(CaseStage.DOCUMENTS_INCOMPLETE)


def test_a_failure_outranks_a_document_waiting_for_review() -> None:
    documents = [_document(DocumentStatus.ANALYZED, "doc-1"), _document(DocumentStatus.FAILED, "doc-2")]

    assert case_stage.document_stage(documents, missing_requirements=()).stage == CaseStage.FAILED


@pytest.mark.parametrize(
    ("documents", "failed_ids", "expected"),
    [
        ([_document(DocumentStatus.FAILED)], set(), CaseFailure.DOCUMENT_PROCESSING),
        ([_document(DocumentStatus.APPROVED)], {"doc-1"}, CaseFailure.FRAUD_DETECTION),
        ([_document(DocumentStatus.DETECTED)], set(), CaseFailure.CASE_ANALYSIS),
    ],
)
def test_failure_of_names_the_earliest_failed_step(documents, failed_ids, expected) -> None:
    assert case_stage.failure_of(documents, failed_ids) == expected


@pytest.mark.parametrize(
    ("analysis", "expected"),
    [
        (None, case_stage.StageResult(CaseStage.READY_FOR_ANALYSIS)),
        (_analysis("running"), case_stage.StageResult(CaseStage.READY_FOR_ANALYSIS)),
        (_analysis("failed", fingerprint="current"), case_stage.StageResult(CaseStage.FAILED, CaseFailure.CASE_ANALYSIS)),
        (_analysis("failed", fingerprint="before-the-documents-changed"), case_stage.StageResult(CaseStage.READY_FOR_ANALYSIS)),
        (_analysis("complete", fingerprint="current"), case_stage.StageResult(CaseStage.CASE_ANALYSED)),
        (_analysis("complete", fingerprint="before-the-documents-changed"), case_stage.StageResult(CaseStage.READY_FOR_ANALYSIS)),
    ],
)
def test_once_every_document_is_scanned_the_analysis_decides(analysis, expected) -> None:
    with (
        patch("app.case_stage.analysis_repository.get_latest_summary", return_value=analysis),
        patch("app.case_stage.langgraph_trace.get_detection_states", return_value=({}, set())),
        patch("app.case_stage.analysis_state.compute_fingerprint", return_value="current"),
    ):
        assert case_stage.analysis_stage(_case(), []) == expected


def test_a_running_analysis_past_its_time_limit_has_failed() -> None:
    lost = _analysis("running")
    lost.started_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    with patch("app.case_stage.analysis_repository.get_latest_summary", return_value=lost):
        assert case_stage.analysis_stage(_case(), []) == case_stage.StageResult(CaseStage.FAILED, CaseFailure.CASE_ANALYSIS)


def test_an_analysis_asking_for_more_evidence_is_evaluated_like_any_other() -> None:
    case = _case()
    case.additional_documents = AdditionalDocuments(items=[AdditionalDocument(
        title="Latest bank statement", why="Confirms the declared revenue", source="finding",
    )])
    with (
        patch("app.case_stage.analysis_repository.get_latest_summary", return_value=_analysis("complete", "current")),
        patch("app.case_stage.langgraph_trace.get_detection_states", return_value=({}, set())),
        patch("app.case_stage.analysis_state.compute_fingerprint", return_value="current"),
    ):
        assert case_stage.analysis_stage(case, []).stage == CaseStage.CASE_ANALYSED


def test_an_analysis_is_pending_evaluation_until_every_finding_has_a_verdict() -> None:
    analysis = _analysis("complete", "current")
    analysis.risk = CaseRisk(state="scored", level="High", score=70, contributing_findings=[_finding("f1"), _finding("f2")])
    with (
        patch("app.case_stage.analysis_repository.get_latest_summary", return_value=analysis),
        patch("app.case_stage.langgraph_trace.get_detection_states", return_value=({}, set())),
        patch("app.case_stage.analysis_state.compute_fingerprint", return_value="current"),
        patch("app.case_stage.finding_reviews_repository.list_for_case") as reviews,
    ):
        reviews.return_value = [_verdict("f1")]
        assert case_stage.analysis_stage(_case(), []).stage == CaseStage.PENDING_EVALUATION

        reviews.return_value = [_verdict("f1"), _verdict("f2", verdict="wrong")]
        assert case_stage.analysis_stage(_case(), []).stage == CaseStage.CASE_ANALYSED


def test_derive_checks_detection_failures_of_the_waiting_documents() -> None:
    with (
        patch("app.case_stage.document_repository.list_documents",
              return_value=[_document(DocumentStatus.APPROVED)]),
        patch("app.case_stage.langgraph_trace.get_detection_states", return_value=({}, {"doc-1"})) as states,
    ):
        assert case_stage.derive(_case()) == case_stage.StageResult(CaseStage.FAILED, CaseFailure.FRAUD_DETECTION)

    states.assert_called_once_with(["doc-1"], slim=True)


# --- Automatic changes ------------------------------------------------------------------------


def test_a_derived_change_is_saved_and_recorded_as_system() -> None:
    with (
        patch("app.case_stage.case_repository") as repo,
        patch("app.case_stage.derive", return_value=case_stage.StageResult(CaseStage.PENDING_REVIEW)),
    ):
        repo.get_case.return_value = _case("processing_documents")
        repo.set_status.return_value = True
        repo.add_status_change.return_value = _change("pending_review", "processing_documents")

        change = real_sync_case_stage("case-1")

    repo.set_status.assert_called_once_with("case-1", "processing_documents", "pending_review")
    repo.add_status_change.assert_called_once_with("case-1", "processing_documents", "pending_review", "System")
    assert change is not None and change.to_status == "pending_review"


def test_nothing_is_recorded_when_the_stage_has_not_changed() -> None:
    with (
        patch("app.case_stage.case_repository") as repo,
        patch("app.case_stage.derive", return_value=case_stage.StageResult(CaseStage.FRAUD_DETECTION)),
    ):
        repo.get_case.return_value = _case("fraud_detection")

        assert real_sync_case_stage("case-1") is None

    repo.set_status.assert_not_called()
    repo.add_status_change.assert_not_called()


def test_a_change_another_process_already_made_is_not_recorded_twice() -> None:
    with (
        patch("app.case_stage.case_repository") as repo,
        patch("app.case_stage.derive", return_value=case_stage.StageResult(CaseStage.FRAUD_DETECTION)),
    ):
        repo.get_case.return_value = _case("pending_review")
        repo.set_status.return_value = False  # the compare-and-swap lost: the case moved meanwhile

        assert real_sync_case_stage("case-1") is None

    repo.add_status_change.assert_not_called()


def test_a_finalised_case_is_not_moved_by_document_changes() -> None:
    with (
        patch("app.case_stage.case_repository") as repo,
        patch("app.case_stage.derive") as derive,
    ):
        repo.get_case.return_value = _case("finalised")

        assert real_sync_case_stage("case-1") is None

    derive.assert_not_called()
    repo.set_status.assert_not_called()


# --- Officer changes ------------------------------------------------------------------------


def test_a_case_still_owed_additional_documents_cannot_be_finalised() -> None:
    with patch("app.case_stage.case_repository") as repo:
        repo.get_case.return_value = _case("additional_documents_required")

        with pytest.raises(case_stage.StageChangeNotAllowed):
            case_stage.finalise("case-1", "officer-a")

    repo.add_status_change.assert_not_called()


def test_an_officer_finalises_an_evaluated_case_under_their_username() -> None:
    with patch("app.case_stage.case_repository") as repo:
        repo.get_case.return_value = _case("case_analysed")
        repo.set_status.return_value = True

        case_stage.finalise("case-1", "officer-a")

    repo.set_status.assert_called_once_with("case-1", "case_analysed", "finalised")
    repo.add_status_change.assert_called_once_with("case-1", "case_analysed", "finalised", "officer-a")


def test_a_case_with_findings_still_to_evaluate_cannot_be_finalised() -> None:
    with patch("app.case_stage.case_repository") as repo:
        repo.get_case.return_value = _case("pending_evaluation")

        with pytest.raises(case_stage.StageChangeNotAllowed, match="Every finding needs a verdict"):
            case_stage.finalise("case-1", "officer-a")

    repo.add_status_change.assert_not_called()


def _finding(finding_id: str, summary: str = "Totals differ") -> ContributingFinding:
    return ContributingFinding(
        finding_id=finding_id, document_id="doc-1", filename="a.pdf", severity="medium", summary=summary, rationale="Seen on the statement", points=10,
    )


def _verdict(finding_id: str, summary: str = "Totals differ", verdict: str | None = "correct") -> FindingReview:
    now = datetime.now(timezone.utc)
    return FindingReview(
        id=f"r-{finding_id}", case_id="case-1", scope="document", kind="verdict", finding_id=finding_id,
        check_name="numerical_validation", summary=summary, verdict=verdict, reviewer="officer-a",
        created_at=now, updated_at=now,
    )


def test_only_a_verdict_on_the_same_finding_counts_as_evaluated() -> None:
    findings = [_finding("f1"), _finding("f2"), _finding("f3", summary="Font mismatch")]
    reviews = [
        _verdict("f1"),                                # evaluated
        _verdict("f3", summary="An older summary"),    # given on a different finding of the same id: doesn't count
    ]

    assert case_stage.count_unreviewed(findings, reviews) == 2
    assert case_stage.count_unreviewed([], reviews) == 0


def test_saving_a_verdict_brings_the_case_stage_up_to_date() -> None:
    with (
        patch("app.routers.finding_reviews.case_stage.sync_case_stage") as sync,
        patch("app.routers.finding_reviews.push_case_snapshot"),
    ):
        import asyncio

        from app.routers.finding_reviews import _record_saved

        asyncio.run(_record_saved("case-1", [("document", "doc-1", "correct")]))

    sync.assert_called_once_with("case-1")


@pytest.mark.parametrize("status", ["open", "pending_review", "fraud_detection", "ready_for_analysis", "pending_evaluation", "failed", "finalised"])
def test_only_an_analysed_case_can_be_finalised(status) -> None:
    with patch("app.case_stage.case_repository") as repo:
        repo.get_case.return_value = _case(status)

        with pytest.raises(case_stage.StageChangeNotAllowed):
            case_stage.finalise("case-1", "officer-a")

    repo.add_status_change.assert_not_called()


def test_creating_a_case_records_it_opening_under_the_creator() -> None:
    with (
        patch("app.case_service.case_repository") as repo,
        patch("app.case_stage.case_repository") as stage_repo,
    ):
        repo.create_case.return_value = _case()

        case_service.create_case("Acme Pte Ltd", "officer-a")

    stage_repo.add_status_change.assert_called_once_with("case-1", None, "open", "officer-a")


# --- What is saved (the acceptance criterion) -------------------------------------------------


@pytest.mark.story("SCRUM-80", "AC1")
def test_a_saved_stage_change_records_the_stage_the_time_and_the_person() -> None:
    """Given a case changes stage, when the change is saved, then the stage, the time and the person are recorded."""
    before = datetime.now(timezone.utc)
    with patch("app.case_repository.supabase_client") as mock_client:
        insert = mock_client.table.return_value.insert
        insert.return_value.execute.side_effect = lambda: MagicMock(data=[insert.call_args[0][0]])

        change = case_repository.add_status_change("case-1", "case_analysed", "finalised", "officer-a")

    mock_client.table.assert_called_with("case_status_history")
    row = insert.call_args[0][0]
    assert row["case_id"] == "case-1"
    assert (row["from_status"], row["to_status"]) == ("case_analysed", "finalised")
    assert row["changed_by"] == "officer-a"
    assert before <= datetime.fromisoformat(row["changed_at"]) <= datetime.now(timezone.utc)
    assert change.to_status == "finalised" and change.changed_by == "officer-a"


def test_the_stage_only_moves_from_the_stage_it_was_read_at() -> None:
    with patch("app.case_repository.supabase_client") as mock_client:
        update = mock_client.table.return_value.update
        update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

        assert case_repository.set_status("case-1", "fraud_detection", "ready_for_analysis") is False

    update.assert_called_once_with({"status": "ready_for_analysis"})
    update.return_value.eq.assert_called_once_with("id", "case-1")
    update.return_value.eq.return_value.eq.assert_called_once_with("status", "fraud_detection")


def test_the_case_summary_carries_its_stage_history() -> None:
    history = [_change("open", changed_by="officer-a"), _change("processing_documents", "open")]
    with (
        patch("app.case_service.case_repository") as repo,
        patch("app.case_service.document_repository.list_documents", return_value=[]),
        patch("app.case_service._load_analysis", return_value=None),
        patch("app.case_service._load_detection_states", return_value=({}, set())),
    ):
        repo.get_case.return_value = _case("processing_documents")
        repo.list_status_history.return_value = history

        summary = case_service.get_case_summary("case-1")

    assert summary is not None
    assert summary.status == "processing_documents"
    assert summary.failure is None
    assert [c.to_status for c in summary.status_history] == ["open", "processing_documents"]


def test_a_failed_case_summary_says_which_step_failed() -> None:
    with (
        patch("app.case_service.case_repository") as repo,
        patch("app.case_service.document_repository.list_documents",
              return_value=[_document(DocumentStatus.APPROVED)]),
        patch("app.case_service._load_analysis", return_value=None),
        patch("app.case_service._load_detection_states", return_value=({}, {"doc-1"})),
    ):
        repo.get_case.return_value = _case("failed")
        repo.list_status_history.return_value = []

        summary = case_service.get_case_summary("case-1")

    assert summary is not None and summary.failure == CaseFailure.FRAUD_DETECTION


# --- Hooks: what moves the stage ---------------------------------------------------------------


def test_a_document_status_change_updates_its_cases_stage() -> None:
    with (
        patch("app.document_repository.supabase_client") as mock_client,
        patch("app.case_stage.sync_case_stage") as sync,
    ):
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "doc-1", "case_id": "case-1"}]
        )

        document_repository.mark_detected("doc-1")

    sync.assert_called_once_with("case-1")


# --- API ---------------------------------------------------------------------------------------


def _summary(status: str) -> CaseSummary:
    return CaseSummary(**_case(status).model_dump(), documents_status="analyzed")


def test_finalise_endpoint_records_the_signed_in_username(published_events) -> None:
    with (
        patch("app.routers._dependencies.case_service.get_case", return_value=_case("case_analysed")),
        patch("app.routers.cases.case_service.finalise_case", return_value=_summary("finalised")) as finalise,
    ):
        response = client.post("/cases/case-1/finalise")

    assert response.status_code == 200
    assert response.json()["status"] == "finalised"
    finalise.assert_called_once_with("case-1", TEST_USER)
    assert [e.type for e in published_events] == ["case.finalised"]


def test_finalising_a_case_that_is_not_analysed_is_a_conflict() -> None:
    with (
        patch("app.routers._dependencies.case_service.get_case", return_value=_case("fraud_detection")),
        patch(
            "app.routers.cases.case_service.finalise_case",
            side_effect=case_stage.StageChangeNotAllowed("Only an analysed case can be finalised."),
        ),
    ):
        response = client.post("/cases/case-1/finalise")

    assert response.status_code == 409
    assert "analysed" in response.json()["detail"]


def test_finalising_is_permanent_there_is_no_reopen() -> None:
    with patch("app.routers._dependencies.case_service.get_case", return_value=_case("finalised")):
        response = client.post("/cases/case-1/reopen")

    assert response.status_code in (404, 405)


# --- Live updates ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_change_to_the_case_row_pushes_a_fresh_snapshot() -> None:
    import asyncio

    live = CaseBroadcaster()
    viewer = live.subscribe("case-1")
    with (
        patch.object(realtime_listener, "broadcaster", live),
        patch.object(realtime_listener, "DEBOUNCE_SECONDS", 0.01),
        patch("app.realtime_listener.case_service.build_case_snapshot", return_value={"type": "snapshot"}),
    ):
        realtime_listener._on_change({"data": {"table": "cases", "record": {"id": "case-1", "status": "fraud_detection"}}})
        await asyncio.sleep(0.1)
    realtime_listener._pending.clear()

    assert viewer.get_nowait() == {"type": "snapshot"}


def test_startup_brings_every_case_up_to_date_and_skips_one_that_fails() -> None:
    def sync(case_id):
        if case_id == "case-2":
            raise RuntimeError("boom")
        return _change("case_analysed") if case_id == "case-1" else None

    with (
        patch("app.case_stage.case_repository.list_case_ids", return_value=["case-1", "case-2", "case-3"]),
        patch("app.case_stage.sync_case_stage", side_effect=sync) as synced,
    ):
        assert case_stage.sync_all_cases() == 1

    assert [c.args[0] for c in synced.call_args_list] == ["case-1", "case-2", "case-3"]



# --- A finalised case takes no changes -----------------------------------------------------------


@pytest.fixture
def finalised_case(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(case_service, "is_case_finalised", lambda case_id: case_id == "case-1")


def test_a_document_in_a_finalised_case_cannot_be_approved(finalised_case) -> None:
    with (
        patch("app.routers._dependencies.document_repository.get_document",
              return_value=_document(DocumentStatus.ANALYZED)),
        patch("app.document_service.document_repository.approve_document") as approve,
    ):
        response = client.post("/documents/doc-1/approve")

    assert response.status_code == 409
    assert response.json()["detail"] == "This case is finalised and view-only, so it can't be changed."
    approve.assert_not_called()


def test_nothing_can_be_uploaded_to_a_finalised_case(finalised_case) -> None:
    response = client.post(
        "/documents/upload-batch", data={"case_id": "case-1"}, files=[("files", ("a.pdf", b"%PDF-1.4", "application/pdf"))]
    )

    assert response.status_code == 409


@pytest.mark.parametrize(("method", "path"), [
    ("patch", "/cases/case-1"), ("delete", "/cases/case-1"), ("post", "/cases/case-1/analysis/run"),
    ("post", "/cases/case-1/notes"),
    ("patch", "/cases/case-1/notes/00000000-0000-0000-0000-000000000001"),
    ("delete", "/cases/case-1/notes/00000000-0000-0000-0000-000000000001"),
    ("delete", "/cases/case-1/finding-reviews/00000000-0000-0000-0000-000000000001"),
])
def test_a_finalised_case_cannot_be_edited_deleted_reanalysed_or_noted(method, path) -> None:
    with patch("app.routers._dependencies.case_service.get_case", return_value=_case("finalised")):
        response = client.request(method.upper(), path, json={"company_name": "Acme", "body": "A note"})

    assert response.status_code == 409


def test_bulk_delete_skips_documents_of_a_finalised_case(finalised_case) -> None:
    other = _document(DocumentStatus.ANALYZED, "doc-2")
    other.case_id = "case-2"
    with (
        patch("app.document_service.document_repository.get_documents_by_ids",
              return_value=[_document(DocumentStatus.ANALYZED), other]),
        patch("app.document_service.document_repository.remove_documents") as remove,
        patch("app.document_service._resync_case"),
    ):
        response = client.post("/documents/bulk-delete", json={"document_ids": ["doc-1", "doc-2"]})

    assert response.json()["deleted"] == ["doc-2"]
    assert response.json()["failed"][0]["document_id"] == "doc-1"
    assert [d.id for d in remove.call_args[0][0]] == ["doc-2"]


def test_only_a_finalised_case_is_locked() -> None:
    for status, locked in [("case_analysed", False), ("finalised", True)]:
        with patch("app.case_service.case_repository.get_case", return_value=_case(status)):
            assert real_is_case_finalised("case-1") is locked
