"""
The real chain behind an officer's last approval: POST /documents/{id}/approve -> the document row -> the case's stage
-> the detection worker -> the automatic case analysis. Every other test stubs the stage sync, start_if_due and the
finalised check (tests/conftest.py); this one is marked real_case_chain, so all three run for real. Only storage is
faked: the documents table in memory (tests/support/fake_supabase.py), and the case, field, report and run reads.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app import case_repository, detection_results, document_repository, extracted_fields_repository
from app.case_pipeline import repository as analysis_repository
from app.events import types
from app.fraud_detection.font_consistency import ExtractedField
from app.main import app
from app.models import CaseRecord, DocumentStatus
from app.workers import detection as detection_worker
from tests.support.fake_supabase import FakeSupabase

pytestmark = pytest.mark.real_case_chain

client = TestClient(app)
NOW = datetime(2026, 11, 13, 4, 0, tzinfo=timezone.utc)
CASE_ID = "case-1"


class _World:
    """The case, its documents and its analysis runs, as the code under test reads and writes them."""

    def __init__(self) -> None:
        self.documents = FakeSupabase(keys={"documents": ("id",)})
        self.case = CaseRecord(
            id=CASE_ID, greenlink_case_id="GLDB-1", status="pending_review", checklist_enforced=False,
            created_at=NOW, updated_at=NOW,
        )
        self.runs: list[SimpleNamespace] = []

    def add_document(self, document_id: str, status: DocumentStatus) -> None:
        self.documents.add("documents", {
            "id": document_id, "case_id": CASE_ID, "filename": f"{document_id}.pdf", "document_type": "payslip",
            "classification_confidence": 0.97, "status": status.value, "uploaded_at": NOW.isoformat(),
        })

    def status_of(self, document_id: str) -> str:
        return self.documents.rows("documents", id=document_id)[0]["status"]

    def set_case_status(self, case_id: str, expected: str, new: str) -> bool:
        if self.case.status != expected:
            return False
        self.case = self.case.model_copy(update={"status": new})
        return True

    def reports(self, document_ids, *, slim: bool = False):
        scanned = {d: {"verdict": "clean"} for d in document_ids if self.status_of(d) == DocumentStatus.DETECTED.value}
        return scanned, set()

    def insert_run(self, run_id: str, case_id: str, fingerprint: str) -> None:
        self.runs.append(SimpleNamespace(
            run_id=run_id, case_id=case_id, fingerprint=fingerprint, status="running",
            started_at=datetime.now(timezone.utc),
        ))

    def latest_run(self, case_id: str):
        return self.runs[-1] if self.runs else None


_PAYSLIP_FIELDS = [
    ExtractedField(field_name=name, field_type="string", field_value=value, confidence=0.95)
    for name, value in (("individual_name", "Jane Tan"), ("company_name", "Acme Pte Ltd"), ("net_pay", "1000"))
]


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    state = _World()
    monkeypatch.setattr(document_repository, "supabase_client", state.documents)
    monkeypatch.setattr(case_repository, "get_case", lambda case_id: state.case if case_id == CASE_ID else None)
    monkeypatch.setattr(case_repository, "set_status", state.set_case_status)
    monkeypatch.setattr(extracted_fields_repository, "get_extracted_fields", lambda document_id: list(_PAYSLIP_FIELDS))
    monkeypatch.setattr(extracted_fields_repository, "get_extracted_fields_dicts_for_documents", lambda ids: {})
    monkeypatch.setattr(detection_results, "get_states", state.reports)
    monkeypatch.setattr(analysis_repository, "insert_run", state.insert_run)
    monkeypatch.setattr(analysis_repository, "latest_run_brief", lambda case_id: (
        (run.status, run.started_at, run.fingerprint) if (run := state.latest_run(case_id)) else None
    ))
    monkeypatch.setattr(analysis_repository, "latest_run_state", lambda case_id: (
        (run.status, run.started_at) if (run := state.latest_run(case_id)) else None
    ))
    monkeypatch.setattr(analysis_repository, "get_latest_summary", state.latest_run)
    monkeypatch.setattr(analysis_repository, "count_runs_since", lambda case_id, since: len(state.runs))
    return state


async def _fake_detection(document_id: str) -> dict:
    """The detection graph's one write that matters here: the document reaches detected."""
    document_repository.update_status(
        document_id, DocumentStatus.DETECTED, expected_current_statuses=[DocumentStatus.DETECTING],
    )
    return {"verdict": "clean", "findings": []}


async def test_the_last_approval_scans_the_document_and_starts_the_case_analysis(world, published_events) -> None:
    world.add_document("doc-1", DocumentStatus.DETECTED)
    world.add_document("doc-2", DocumentStatus.ANALYZED)

    response = client.post("/documents/doc-2/approve")

    # The approval moved the case on: one document is approved and not yet scanned.
    assert response.status_code == 200
    assert world.status_of("doc-2") == DocumentStatus.APPROVED.value
    assert world.case.status == "fraud_detection"
    approved = [e for e in published_events if e.type == types.DOCUMENT_APPROVED]
    assert len(approved) == 1
    assert not [e for e in published_events if e.type == types.CASE_ANALYSIS_REQUESTED]

    with (
        patch.object(detection_worker, "run_detection", _fake_detection),
        patch.object(detection_worker, "push_case_snapshot", AsyncMock()),
        patch.object(detection_worker.audit, "ran_for", return_value=False),
    ):
        await detection_worker.on_requested(approved[0])

    # The last scan made the case's analysis due, and it started on its own, once.
    assert world.status_of("doc-2") == DocumentStatus.DETECTED.value
    requested = [e for e in published_events if e.type == types.CASE_ANALYSIS_REQUESTED]
    assert len(requested) == 1
    assert requested[0].case_id == CASE_ID and requested[0].detail["trigger"] == "automatic"
    assert [run.fingerprint for run in world.runs] == [requested[0].detail["fingerprint"]]
    assert world.case.status == "fraud_detection"  # the analysis is running: the case stays at Fraud Detection


async def test_a_finalised_case_refuses_the_approval(world, published_events) -> None:
    world.add_document("doc-1", DocumentStatus.ANALYZED)
    world.case = world.case.model_copy(update={"status": "finalised"})

    response = client.post("/documents/doc-1/approve")

    assert response.status_code == 409
    assert world.status_of("doc-1") == DocumentStatus.ANALYZED.value
    assert published_events == []
