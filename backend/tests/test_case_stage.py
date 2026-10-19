"""SCRUM-80: each case moves through clear stages, and every change records the stage, the time and the person."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import auth, case_repository, case_service, case_stage, document_repository, realtime_listener
from app.case_broadcaster import CaseBroadcaster
from app.case_pipeline.models import CaseAnalysis
from app.case_service import is_case_finalised as real_is_case_finalised  # bound before conftest's stand-in
from app.case_stage import sync_case_stage as real_sync_case_stage  # bound before conftest's no-op
from app.main import app
from app.models import (
    AdditionalDocument, AdditionalDocuments, CaseRecord, CaseStage, CaseStageChange, CaseSummary, DocumentRecord,
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
        ([DocumentStatus.UPLOADED], CaseStage.DOCUMENTS_UPLOADED),
        ([DocumentStatus.QUEUED, DocumentStatus.ANALYZED], CaseStage.DOCUMENTS_UPLOADED),
        ([DocumentStatus.PROCESSING, DocumentStatus.EXTRACTING], CaseStage.DOCUMENTS_UPLOADED),
        ([DocumentStatus.EXTRACTING, DocumentStatus.ANALYZED], CaseStage.DOCUMENTS_CLASSIFIED),
        ([DocumentStatus.EXTRACTING, DocumentStatus.DETECTED], CaseStage.DOCUMENTS_CLASSIFIED),
        ([DocumentStatus.ANALYZED, DocumentStatus.ANALYZED], CaseStage.DOCUMENTS_EXTRACTED),
        ([DocumentStatus.ANALYZED, DocumentStatus.DETECTING], CaseStage.DOCUMENTS_EXTRACTED),
        ([DocumentStatus.FAILED, DocumentStatus.ANALYZED], CaseStage.DOCUMENTS_FAILED),
        ([DocumentStatus.FAILED, DocumentStatus.DETECTED], CaseStage.DOCUMENTS_FAILED),
        ([DocumentStatus.APPROVED, DocumentStatus.REJECTED], CaseStage.DOCUMENTS_APPROVED),
        ([DocumentStatus.REJECTED, DocumentStatus.REJECTED], CaseStage.DOCUMENTS_REJECTED),
        ([DocumentStatus.APPROVED, DocumentStatus.DETECTING], CaseStage.DETECTION_TRIGGERED),
        ([DocumentStatus.APPROVED, DocumentStatus.DETECTED], CaseStage.DETECTION_TRIGGERED),
        ([DocumentStatus.DETECTED, DocumentStatus.REJECTED], None),
    ],
)
def test_documents_put_the_case_at_its_earliest_unfinished_stage(statuses, expected) -> None:
    documents = [_document(status, f"doc-{i}") for i, status in enumerate(statuses)]

    assert case_stage.document_stage(documents) == expected


def test_a_document_sent_to_manual_review_puts_the_case_in_manual_review() -> None:
    sent_to_review = _document(DocumentStatus.ANALYZED, "doc-1")
    sent_to_review.routing_status = RoutingStatus.MANUAL_REVIEW

    assert case_stage.document_stage([sent_to_review, _document(DocumentStatus.ANALYZED, "doc-2")]) == (
        CaseStage.MANUAL_REVIEW
    )


@pytest.mark.parametrize(
    ("analysis", "expected"),
    [
        (None, CaseStage.READY_FOR_ANALYSIS),
        (_analysis("running"), CaseStage.READY_FOR_ANALYSIS),
        (_analysis("failed"), CaseStage.READY_FOR_ANALYSIS),
        (_analysis("complete", fingerprint="current"), CaseStage.CASE_ANALYSED),
        (_analysis("complete", fingerprint="before-the-documents-changed"), CaseStage.READY_FOR_ANALYSIS),
    ],
)
def test_once_every_document_is_scanned_the_analysis_decides(analysis, expected) -> None:
    with (
        patch("app.case_stage.analysis_repository.get_latest", return_value=analysis),
        patch("app.case_stage.langgraph_trace.get_detection_states", return_value=({}, set())),
        patch("app.case_stage.analysis_state.compute_fingerprint", return_value="current"),
    ):
        assert case_stage.analysis_stage("case-1", []) == expected


def test_a_current_analysis_that_asks_for_more_evidence_requires_additional_documents() -> None:
    analysis = _analysis("complete", fingerprint="current")
    analysis.additional_documents = AdditionalDocuments(items=[AdditionalDocument(
        title="Latest bank statement", why="Confirms the declared revenue", source="finding",
    )])
    with (
        patch("app.case_stage.analysis_repository.get_latest", return_value=analysis),
        patch("app.case_stage.langgraph_trace.get_detection_states", return_value=({}, set())),
        patch("app.case_stage.analysis_state.compute_fingerprint", return_value="current"),
    ):
        assert case_stage.analysis_stage("case-1", []) == CaseStage.ADDITIONAL_DOCUMENTS_REQUIRED


def test_an_approved_document_whose_detection_failed_puts_the_case_at_detection_failed() -> None:
    documents = [_document(DocumentStatus.APPROVED, "doc-1"), _document(DocumentStatus.DETECTED, "doc-2")]

    assert case_stage.document_stage(documents, failed_detection_ids=frozenset({"doc-1"})) == (
        CaseStage.DETECTION_FAILED
    )
    # Waiting for its turn, not failed: still the detection stage.
    assert case_stage.document_stage(documents) == CaseStage.DETECTION_TRIGGERED


def test_a_missing_required_document_shows_as_incomplete_until_detection_starts() -> None:
    assert case_stage.document_stage(
        [_document(DocumentStatus.APPROVED)], missing_requirements=("nric",)
    ) == CaseStage.DOCUMENTS_INCOMPLETE
    # Not a hard stop: once detection starts, the case moves on.
    assert case_stage.document_stage(
        [_document(DocumentStatus.DETECTING)], missing_requirements=("nric",)
    ) == CaseStage.DETECTION_TRIGGERED
    assert case_stage.document_stage([_document(DocumentStatus.DETECTED)], missing_requirements=("nric",)) is None
    # Earlier work still comes first, and a failed detection outranks it.
    assert case_stage.document_stage(
        [_document(DocumentStatus.ANALYZED)], missing_requirements=("nric",)
    ) == CaseStage.DOCUMENTS_EXTRACTED
    assert case_stage.document_stage(
        [_document(DocumentStatus.APPROVED)], failed_detection_ids=frozenset({"doc-1"}), missing_requirements=("nric",)
    ) == CaseStage.DETECTION_FAILED


def test_derive_stage_checks_detection_failures_and_the_checklist() -> None:
    with (
        patch("app.case_stage.document_repository.list_documents",
              return_value=[_document(DocumentStatus.APPROVED)]),
        patch("app.case_stage.langgraph_trace.get_detection_states", return_value=({}, {"doc-1"})) as states,
        patch("app.case_service.compute_requirement_coverage", return_value=([], ["nric"])),
    ):
        assert case_stage.derive_stage("case-1") == CaseStage.DETECTION_FAILED

    states.assert_called_once_with(["doc-1"], slim=True)


# --- Automatic changes ------------------------------------------------------------------------


def test_a_derived_change_is_saved_and_recorded_as_system() -> None:
    with (
        patch("app.case_stage.case_repository") as repo,
        patch("app.case_stage.derive_stage", return_value=CaseStage.MANUAL_REVIEW),
    ):
        repo.get_case.return_value = _case("documents_uploaded")
        repo.set_status.return_value = True
        repo.add_status_change.return_value = _change("manual_review", "documents_uploaded")

        change = real_sync_case_stage("case-1")

    repo.set_status.assert_called_once_with("case-1", "documents_uploaded", "manual_review")
    repo.add_status_change.assert_called_once_with("case-1", "documents_uploaded", "manual_review", "System")
    assert change is not None and change.to_status == "manual_review"


def test_nothing_is_recorded_when_the_stage_has_not_changed() -> None:
    with (
        patch("app.case_stage.case_repository") as repo,
        patch("app.case_stage.derive_stage", return_value=CaseStage.DETECTION_TRIGGERED),
    ):
        repo.get_case.return_value = _case("detection_triggered")

        assert real_sync_case_stage("case-1") is None

    repo.set_status.assert_not_called()
    repo.add_status_change.assert_not_called()


def test_a_change_another_process_already_made_is_not_recorded_twice() -> None:
    with (
        patch("app.case_stage.case_repository") as repo,
        patch("app.case_stage.derive_stage", return_value=CaseStage.DETECTION_TRIGGERED),
    ):
        repo.get_case.return_value = _case("manual_review")
        repo.set_status.return_value = False  # the compare-and-swap lost: the case moved meanwhile

        assert real_sync_case_stage("case-1") is None

    repo.add_status_change.assert_not_called()


def test_a_finalised_case_is_not_moved_by_document_changes() -> None:
    with (
        patch("app.case_stage.case_repository") as repo,
        patch("app.case_stage.derive_stage") as derive,
    ):
        repo.get_case.return_value = _case("finalised")

        assert real_sync_case_stage("case-1") is None

    derive.assert_not_called()
    repo.set_status.assert_not_called()


# --- Officer changes ------------------------------------------------------------------------


def test_a_case_still_owed_additional_documents_can_be_finalised() -> None:
    with patch("app.case_stage.case_repository") as repo:
        repo.get_case.return_value = _case("additional_documents_required")
        repo.set_status.return_value = True

        case_stage.finalise("case-1", "officer-a")

    repo.add_status_change.assert_called_once_with("case-1", "additional_documents_required", "finalised", "officer-a")


def test_an_officer_finalises_an_analysed_case_under_their_username() -> None:
    with patch("app.case_stage.case_repository") as repo:
        repo.get_case.return_value = _case("case_analysed")
        repo.set_status.return_value = True

        case_stage.finalise("case-1", "officer-a")

    repo.set_status.assert_called_once_with("case-1", "case_analysed", "finalised")
    repo.add_status_change.assert_called_once_with("case-1", "case_analysed", "finalised", "officer-a")


@pytest.mark.parametrize("status", ["open", "manual_review", "detection_triggered", "finalised"])
def test_only_an_analysed_case_can_be_finalised(status) -> None:
    with patch("app.case_stage.case_repository") as repo:
        repo.get_case.return_value = _case(status)

        with pytest.raises(case_stage.StageChangeNotAllowed):
            case_stage.finalise("case-1", "officer-a")

    repo.add_status_change.assert_not_called()


def test_reopening_returns_the_case_to_its_current_stage_under_the_officers_username() -> None:
    with (
        patch("app.case_stage.case_repository") as repo,
        patch("app.case_stage.derive_stage", return_value=CaseStage.CASE_ANALYSED),
    ):
        repo.get_case.return_value = _case("finalised")
        repo.set_status.return_value = True

        case_stage.reopen("case-1", "officer-a")

    repo.add_status_change.assert_called_once_with("case-1", "finalised", "case_analysed", "officer-a")


def test_only_a_finalised_case_can_be_reopened() -> None:
    with patch("app.case_stage.case_repository") as repo:
        repo.get_case.return_value = _case("case_analysed")

        with pytest.raises(case_stage.StageChangeNotAllowed):
            case_stage.reopen("case-1", "officer-a")


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

        assert case_repository.set_status("case-1", "detection_triggered", "documents_approved") is False

    update.assert_called_once_with({"status": "documents_approved"})
    update.return_value.eq.assert_called_once_with("id", "case-1")
    update.return_value.eq.return_value.eq.assert_called_once_with("status", "detection_triggered")


def test_the_case_summary_carries_its_stage_history() -> None:
    history = [_change("open", changed_by="officer-a"), _change("documents_uploaded", "open")]
    with (
        patch("app.case_service.case_repository") as repo,
        patch("app.case_service.document_repository.list_documents", return_value=[]),
        patch("app.case_service._load_analyses", return_value={}),
        patch("app.case_service._load_detection_states", return_value=({}, set())),
    ):
        repo.get_case.return_value = _case("documents_uploaded")
        repo.list_status_history.return_value = history

        summary = case_service.get_case_summary("case-1")

    assert summary is not None
    assert summary.status == "documents_uploaded"
    assert [c.to_status for c in summary.status_history] == ["open", "documents_uploaded"]


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


def test_finalise_endpoint_records_the_signed_in_username(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth.settings, "AUTH_SECRET_KEY", "test-signing-key")
    token = auth.create_token("officer-a")
    with (
        patch("app.routers._dependencies.case_service.get_case", return_value=_case("case_analysed")),
        patch("app.routers.cases.case_service.finalise_case", return_value=_summary("finalised")) as finalise,
    ):
        response = client.post("/cases/case-1/finalise", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["status"] == "finalised"
    finalise.assert_called_once_with("case-1", "officer-a")


def test_finalising_a_case_that_is_not_analysed_is_a_conflict() -> None:
    with (
        patch("app.routers._dependencies.case_service.get_case", return_value=_case("detection_triggered")),
        patch(
            "app.routers.cases.case_service.finalise_case",
            side_effect=case_stage.StageChangeNotAllowed("Only an analysed case can be finalised."),
        ),
    ):
        response = client.post("/cases/case-1/finalise")

    assert response.status_code == 409
    assert "analysed" in response.json()["detail"]


def test_reopen_endpoint_returns_the_updated_case() -> None:
    with (
        patch("app.routers._dependencies.case_service.get_case", return_value=_case("finalised")),
        patch("app.routers.cases.case_service.reopen_case", return_value=_summary("case_analysed")) as reopen,
    ):
        response = client.post("/cases/case-1/reopen")

    assert response.status_code == 200
    assert response.json()["status"] == "case_analysed"
    reopen.assert_called_once()


def test_a_token_from_before_usernames_falls_back_to_the_shared_username(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth.settings, "AUTH_SECRET_KEY", "test-signing-key")
    monkeypatch.setattr(auth.settings, "AUTH_USERNAME", "demo")

    assert auth.current_username(f"Bearer {auth.create_token()}") == "demo"
    assert auth.current_username(f"Bearer {auth.create_token('officer-a')}") == "officer-a"
    assert auth.current_username(None) == "demo"


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
        realtime_listener._on_change({"data": {"table": "cases", "record": {"id": "case-1", "status": "detection_triggered"}}})
        await asyncio.sleep(0.1)
    realtime_listener._pending.clear()

    assert viewer.get_nowait() == {"type": "snapshot"}


def test_startup_brings_every_case_up_to_date_and_skips_one_that_fails() -> None:
    def sync(case_id):
        if case_id == "case-2":
            raise RuntimeError("boom")
        return _change("case_analysed") if case_id == "case-1" else None

    with (
        patch("app.case_stage.case_repository.list_cases", return_value=[_case(), _case(), _case()]) as list_cases,
        patch("app.case_stage.sync_case_stage", side_effect=sync) as synced,
    ):
        list_cases.return_value[1].id, list_cases.return_value[2].id = "case-2", "case-3"

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
        patch("app.routers.documents.document_repository.approve_document") as approve,
    ):
        response = client.post("/documents/doc-1/approve")

    assert response.status_code == 409
    assert response.json()["detail"] == "This case is finalised. Reopen it to make changes."
    approve.assert_not_called()


def test_nothing_can_be_uploaded_to_a_finalised_case(finalised_case) -> None:
    response = client.post(
        "/documents/upload-batch", data={"case_id": "case-1"}, files=[("files", ("a.pdf", b"%PDF-1.4", "application/pdf"))]
    )

    assert response.status_code == 409


@pytest.mark.parametrize(("method", "path"), [
    ("patch", "/cases/case-1"), ("delete", "/cases/case-1"), ("post", "/cases/case-1/analysis/run"),
])
def test_a_finalised_case_cannot_be_edited_deleted_or_reanalysed(method, path) -> None:
    with patch("app.routers._dependencies.case_service.get_case", return_value=_case("finalised")):
        response = client.request(method.upper(), path, json={"company_name": "Acme"})

    assert response.status_code == 409


def test_bulk_delete_skips_documents_of_a_finalised_case(finalised_case) -> None:
    other = _document(DocumentStatus.ANALYZED, "doc-2")
    other.case_id = "case-2"
    with (
        patch("app.routers.documents.document_repository.get_documents_by_ids",
              return_value=[_document(DocumentStatus.ANALYZED), other]),
        patch("app.routers.documents.document_repository.remove_documents") as remove,
        patch("app.routers.documents._resync_case"),
    ):
        response = client.post("/documents/bulk-delete", json={"document_ids": ["doc-1", "doc-2"]})

    assert response.json()["deleted"] == ["doc-2"]
    assert response.json()["failed"][0]["document_id"] == "doc-1"
    assert [d.id for d in remove.call_args[0][0]] == ["doc-2"]


def test_only_a_finalised_case_is_locked() -> None:
    for status, locked in [("case_analysed", False), ("finalised", True)]:
        with patch("app.case_service.case_repository.get_case", return_value=_case(status)):
            assert real_is_case_finalised("case-1") is locked
