"""
Regression tests (SCRUM-296) for running the case analysis — "Test Cases -
Sprint 4.xlsx", Multi-Agent sheet:

  SCRUM-52  Trigger case analysis pipeline   MA-013 – MA-018
  SCRUM-53  Auto-trigger scoring agent       MA-019 – MA-023

Runs the real POST /cases/{id}/analysis/run endpoint, the real case-worker
handler it hands the run to, and the real case pipeline: the code-side
cross-document comparisons, scoring, the outlook and the run trace (the
"Case analysis trace (debug)" panel the tester read). Only the database and
Claude are faked: the runs table is in memory, the cross-document agent is
scripted to propose nothing further (so its findings are the code
comparisons'), and the evidence requests and summary fall back to their
no-Claude versions because conftest.py blocks every real Claude call.

The frontend half (the button, "Analysing…", the error messages) is in
frontend/e2e/specs/case-analysis.spec.ts.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.case_pipeline import trace as run_trace
from app.case_pipeline.models import CaseAnalysis
from app.case_pipeline.readiness import is_ready_for_analysis
from app.events import Event, types
from app.main import app
from app.models import CaseRecord, CaseRisk, DocumentRecord, DocumentStatus
from app.workers import cases as case_worker

client = TestClient(app)
CASE_ID = "c0010000-0000-0000-0000-000000000001"
_NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)


def _document(number: int, filename: str, document_type: str, fields: dict,
              status: DocumentStatus = DocumentStatus.DETECTED) -> DocumentRecord:
    return DocumentRecord(
        id=f"c0010000-0000-0000-0000-00000000010{number}", case_id=CASE_ID, filename=filename,
        document_type=document_type, status=status, uploaded_at=_NOW, extracted_fields=fields,
    )


def c001_documents(*, uen_on_noa: str = "201811234K") -> list[DocumentRecord]:
    """The C001 set the tester used: a company's certificate, NOA and bank statement."""
    return [
        _document(1, "C001_CertIncorp.pdf", "certificate_of_incorporation",
                  {"company_name": "GOLDEN HARVEST TRADING PTE. LTD.", "uen": "201811234K"}),
        _document(2, "C001_NOA.pdf", "notice_of_assessment",
                  {"company_name": "Golden Harvest Trading Pte Ltd", "uen": uen_on_noa}),
        _document(3, "C001_BankStatement_2026-06_to_2026-08.pdf", "bank_statement",
                  {"company_name": "GOLDEN HARVEST TRADING PTE LTD", "bank_name": "DBS Bank Ltd"}),
    ]


def _clean_reports(documents: list[DocumentRecord]) -> dict[str, dict]:
    return {
        d.id: {"verdict": "clean", "findings": [], "unable_to_assess": [], "nothing_to_check": [], "consulted": []}
        for d in documents if d.status == DocumentStatus.DETECTED
    }


class Runs:
    """Stands in for the case_analysis_runs table (case_pipeline/repository.py)."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.traces: dict[str, list[dict]] = {}

    def insert_run(self, run_id, case_id, fingerprint):
        self.rows[run_id] = {"run_id": run_id, "case_id": case_id, "status": "running", "fingerprint": fingerprint,
                             "started_at": datetime.now(timezone.utc), "finished_at": None, "error": None}

    def latest_run_state(self, case_id):
        rows = [r for r in self.rows.values() if r["case_id"] == case_id]
        newest = max(rows, key=lambda r: r["started_at"], default=None)
        return (newest["status"], newest["started_at"]) if newest else None

    def write_trace(self, run_id, events):
        self.traces[run_id] = events

    def finish_run(self, analysis: CaseAnalysis) -> bool:
        row = self.rows[analysis.run_id]
        row.update(status=analysis.status, finished_at=datetime.now(timezone.utc), error=analysis.error,
                   analysis=analysis)
        return True

    def trace(self, run_id) -> list[dict]:
        return self.traces.get(run_id, [])


class Case:
    """The case's documents and their stored detection reports, plus everything the run writes."""

    def __init__(self, documents: list[DocumentRecord]) -> None:
        self.documents = documents
        self.reports = _clean_reports(documents)
        self.saved_risks: list[CaseRisk] = []
        # The checklist items still missing. The C001 sample is only part of a case, so the tests about scanning and
        # runs treat its checklist as complete; the mandatory-checklist test sets this.
        self.missing_requirements: list[str] = []

    def list_documents(self, case_id=None, **kwargs):
        return [d for d in self.documents if d.case_id == case_id]

    def get_detection_states(self, document_ids, **kwargs):
        """(reports, ids whose detection crashed): no crashed runs here."""
        return {i: self.reports[i] for i in document_ids if i in self.reports}, set()


@pytest.fixture
def runs(monkeypatch) -> Runs:
    fake = Runs()
    for name in ("insert_run", "latest_run_state", "write_trace", "finish_run"):
        monkeypatch.setattr(f"app.case_pipeline.repository.{name}", getattr(fake, name))
    # A trace is saved on a background thread; here at once, so a test reads it as soon as the run has saved it.
    monkeypatch.setattr(run_trace.RunTrace, "save", lambda trace: fake.write_trace(trace.run_id, list(trace.events)))
    return fake


@pytest.fixture
def case(monkeypatch) -> Case:
    fake = Case(c001_documents())
    monkeypatch.setattr("app.case_pipeline.service.document_repository.list_documents", fake.list_documents)
    monkeypatch.setattr("app.case_pipeline.service.detection_results.get_states", fake.get_detection_states)
    monkeypatch.setattr("app.case_pipeline.service.findings_repository.sync_case", lambda case_id, findings: len(findings))
    monkeypatch.setattr(
        "app.case_pipeline.service.case_repository.get_case",
        lambda case_id: CaseRecord(id=case_id, greenlink_case_id="GLDB-130", created_at=_NOW, updated_at=_NOW),
    )
    monkeypatch.setattr(
        "app.case_pipeline.service.unapproved_requirements", lambda case, documents: fake.missing_requirements
    )
    monkeypatch.setattr("app.case_pipeline.service.push_case_snapshot", AsyncMock())
    monkeypatch.setattr("app.case_pipeline.service.risk_store.save", lambda case_id, risk: fake.saved_risks.append(risk))
    monkeypatch.setattr("app.case_pipeline.service.case_repository.set_recommended_additional_documents",
                        lambda case_id, additional: None)
    # The cross-document agent proposes nothing beyond what the code comparisons found.
    monkeypatch.setattr("app.case_pipeline.cross_document.graph.hypothesise", AsyncMock(return_value=([], 0.0)))
    return fake


@pytest.fixture
def existing_case():
    with patch("app.routers._dependencies.case_service") as lookup:
        lookup.get_case.return_value = CaseRecord(id=CASE_ID, greenlink_case_id="GLDB-130", created_at=_NOW,
                                                  updated_at=_NOW)
        yield


def run_analysis():
    return client.post(f"/cases/{CASE_ID}/analysis/run")


async def run_requested_analysis(published_events) -> Event:
    """Runs what the endpoint asked for, as the case-worker does; returns the worker's completion event."""
    [requested] = [e for e in published_events if e.type == types.CASE_ANALYSIS_REQUESTED]
    await case_worker.on_analysis_requested(requested)
    [completed] = [e for e in published_events if e.type == types.CASE_ANALYSIS_COMPLETED]
    return completed


def steps(trace: list[dict]) -> list[str]:
    return [event["step"] for event in trace if event["step"] != "span"]


# --- SCRUM-52 Trigger case analysis pipeline -------------------------------------------


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-52", "MA-013")
@pytest.mark.parametrize("status", [DocumentStatus.APPROVED, DocumentStatus.DETECTING])
def test_the_analysis_cannot_run_while_a_document_is_still_being_scanned(
    runs, case, existing_case, published_events, status
):
    still_scanning = case.documents[1]
    still_scanning.status = status  # approved and waiting for detection, or mid-detection
    del case.reports[still_scanning.id]

    assert is_ready_for_analysis(case.documents) is False
    response = run_analysis()

    assert response.status_code == 409
    assert response.json()["detail"] == "Every approved document must finish scanning before the case can be analysed."
    assert runs.rows == {}
    assert published_events == []


@pytest.mark.story("SCRUM-80")
def test_the_analysis_cannot_run_while_a_required_document_is_missing(runs, case, existing_case, published_events):
    case.missing_requirements = ["bank_statement", "id_document"]

    response = run_analysis()

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Every required document must be approved before the case can be analysed. "
        "Not yet approved: Corporate Bank Statement, Identity Document (NRIC/Passport)."
    )
    assert runs.rows == {}
    assert published_events == []


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-52", "MA-013")
def test_a_document_still_being_extracted_does_not_hold_the_analysis_back(case):
    # readiness.py's rule: only approved documents count, and one still being extracted can't have been approved
    # yet. MA-013's wording says extraction should hide the button too — see the Sprint 4 report's observations.
    case.documents.append(_document(4, "C001-P1_NRICPassport.pdf", "nric", {}, status=DocumentStatus.EXTRACTING))

    assert is_ready_for_analysis(case.documents) is True


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-52", "MA-014")
def test_the_analysis_is_ready_once_every_approved_document_has_been_scanned(case):
    assert is_ready_for_analysis(case.documents) is True


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-52", "MA-015")
async def test_running_the_analysis_starts_one_run_for_this_case_over_its_documents(
    runs, case, existing_case, published_events
):
    response = await asyncio.to_thread(run_analysis)

    assert response.status_code == 202
    started = response.json()
    assert (started["case_id"], started["status"]) == (CASE_ID, "running")
    assert list(runs.rows) == [started["run_id"]]
    [requested] = published_events
    assert (requested.type, requested.case_id, requested.detail["run_id"]) == (
        types.CASE_ANALYSIS_REQUESTED, CASE_ID, started["run_id"])

    completed = await run_requested_analysis(published_events)

    assert completed.outcome == "success"
    assert runs.rows[started["run_id"]]["status"] == "complete"
    trace = runs.trace(started["run_id"])
    [run_start] = [e for e in trace if e["step"] == "run_start"]
    assert run_start["case_id"] == CASE_ID
    assert [d["filename"] for d in run_start["documents"]] == [d.filename for d in case.documents]


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-52", "MA-016 / MA-017")
def test_a_second_request_while_the_analysis_runs_is_refused_and_starts_nothing(
    runs, case, existing_case, published_events
):
    first = run_analysis()
    second = run_analysis()
    third = run_analysis()

    assert first.status_code == 202
    assert (second.status_code, third.status_code) == (409, 409)
    assert second.json()["detail"] == "An analysis is already running for this case."
    assert len(runs.rows) == 1
    assert [e.type for e in published_events] == [types.CASE_ANALYSIS_REQUESTED]


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-52", "MA-016")
async def test_the_analysis_can_be_run_again_once_the_first_run_has_finished(
    runs, case, existing_case, published_events
):
    assert (await asyncio.to_thread(run_analysis)).status_code == 202
    await run_requested_analysis(published_events)

    again = await asyncio.to_thread(run_analysis)

    assert again.status_code == 202
    assert len(runs.rows) == 2


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-52", "MA-018")
async def test_a_failed_analysis_is_stored_as_failed_with_its_error(
    runs, case, existing_case, published_events, monkeypatch
):
    def scoring_breaks(*args, **kwargs):
        raise RuntimeError("scoring unavailable")

    monkeypatch.setattr("app.case_pipeline.service.compute_case_risk", scoring_breaks)
    run_id = (await asyncio.to_thread(run_analysis)).json()["run_id"]

    completed = await run_requested_analysis(published_events)

    row = runs.rows[run_id]
    assert row["status"] == "failed"
    assert row["error"] == "RuntimeError: scoring unavailable"
    assert completed.outcome == "failed"
    assert completed.detail["error"] == "RuntimeError: scoring unavailable"
    assert case.saved_risks == []  # a failed run never replaces the case's score


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-52", "MA-018")
async def test_a_run_that_could_not_be_handed_to_the_worker_is_failed_so_it_can_be_run_again(
    runs, case, existing_case, monkeypatch
):
    monkeypatch.setattr("app.case_pipeline.service.events.publish", AsyncMock(side_effect=RuntimeError("bus down")))

    response = await asyncio.to_thread(run_analysis)

    assert response.status_code == 502
    [row] = runs.rows.values()
    assert row["status"] == "failed"
    assert row["error"] == "The analysis could not be started. Run it again."


# --- SCRUM-53 Auto-trigger scoring agent -------------------------------------------------


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-53", "MA-019")
async def test_scoring_does_not_start_while_the_cross_document_check_is_still_running(
    runs, case, existing_case, published_events, monkeypatch
):
    from app.case_pipeline.cross_document.graph import run_cross_document_analysis

    checking, release = asyncio.Event(), asyncio.Event()

    async def slow_cross_document_check(context):
        checking.set()
        await release.wait()
        return await run_cross_document_analysis(context)

    monkeypatch.setattr("app.case_pipeline.service.run_cross_document_analysis", slow_cross_document_check)
    run_id = (await asyncio.to_thread(run_analysis)).json()["run_id"]
    worker = asyncio.create_task(run_requested_analysis(published_events))
    await checking.wait()

    # What the debug panel's refresh shows mid-run.
    row = runs.rows[run_id]
    assert (row["status"], row["finished_at"]) == ("running", None)
    assert "scoring" not in steps(runs.trace(run_id))
    assert case.saved_risks == []

    release.set()
    await worker
    assert runs.rows[run_id]["status"] == "complete"


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-53", "MA-020")
async def test_scoring_starts_after_the_cross_document_check_finishes(runs, case, existing_case, published_events):
    run_id = (await asyncio.to_thread(run_analysis)).json()["run_id"]
    await run_requested_analysis(published_events)

    trace = runs.trace(run_id)
    order = steps(trace)
    cross_document = [e for e in trace if e["step"] in ("deterministic", "hypotheses", "verify")]
    [scoring] = [e for e in trace if e["step"] == "scoring"]
    assert cross_document, order
    assert all(scoring["at"] >= e["at"] for e in cross_document)
    assert order.index("scoring") > max(order.index(e["step"]) for e in cross_document)
    assert order[-2:] == ["done", "run_audit"]


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-53", "MA-021")
async def test_the_risk_score_includes_the_mismatches_the_cross_document_check_found(
    runs, case, existing_case, published_events
):
    case.documents = c001_documents(uen_on_noa="201899999Z")  # the NOA names a different UEN
    case.reports = _clean_reports(case.documents)
    run_id = (await asyncio.to_thread(run_analysis)).json()["run_id"]
    await run_requested_analysis(published_events)

    [scoring] = [e for e in runs.trace(run_id) if e["step"] == "scoring"]
    cross_document = [f for f in scoring["findings"] if f["source"] == "cross_document"]
    assert cross_document
    assert any(f["category"] == "identity_entity_conflict" for f in cross_document)
    assert scoring["cross_document_findings"] == len(cross_document)
    # Every document is clean on its own: the whole score comes from the cross-document check.
    assert (scoring["level_without_cross_document"], scoring["score_without_cross_document"]) == ("Low", 0)
    assert scoring["score"] > 0 and scoring["level"] != "Low"
    analysis = runs.rows[run_id]["analysis"]
    assert analysis.risk.score == scoring["score"]
    assert case.saved_risks[-1].score == scoring["score"]


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-53", "MA-022 / MA-023")
async def test_scoring_does_not_start_when_the_cross_document_check_fails(
    runs, case, existing_case, published_events, monkeypatch
):
    monkeypatch.setattr("app.case_pipeline.service.run_cross_document_analysis",
                        AsyncMock(side_effect=RuntimeError("cross-document check failed")))
    run_id = (await asyncio.to_thread(run_analysis)).json()["run_id"]

    completed = await run_requested_analysis(published_events)

    order = steps(runs.trace(run_id))
    assert "scoring" not in order
    assert "error" in order
    row = runs.rows[run_id]
    # What the case page shows in "The analysis did not finish." (MA-023).
    assert (row["status"], row["error"]) == ("failed", "RuntimeError: cross-document check failed")
    assert row["analysis"].risk is None
    assert completed.outcome == "failed"
    assert case.saved_risks == []


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-53", "MA-023")
def test_the_failed_run_and_its_error_are_what_the_case_page_reads(case, existing_case):
    failed = CaseAnalysis(run_id="run-1", case_id=CASE_ID, status="failed", fingerprint="f", started_at=_NOW,
                          error="RuntimeError: cross-document check failed")
    with patch("app.case_pipeline.service.repository.get_latest", return_value=failed):
        response = client.get(f"/cases/{CASE_ID}/analysis")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error"] == "RuntimeError: cross-document check failed"
    assert response.json()["risk"] is None


async def test_a_run_requested_event_without_a_run_id_is_ignored(runs, case, published_events):
    await case_worker.on_analysis_requested(Event(type=types.CASE_ANALYSIS_REQUESTED, case_id=CASE_ID))

    assert runs.rows == {}
    assert published_events == []
