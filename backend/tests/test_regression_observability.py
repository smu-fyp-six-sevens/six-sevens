"""
Regression tests (SCRUM-323) for what a detection run leaves behind — "Test Cases - Sprint 5.xlsx":
  Multi-Agent sheet, Agent Observability (SCRUM-55): MA-028, MA-029, MA-031, MA-032, MA-039
  Agentic Evaluation sheet, Record Agent Confidence Scores (SCRUM-283): AE-016

The real detection graph runs with Sprint 3's harness on the C028 credit report (a restyled field and
Acrobat/"FraudTests" metadata), and the tests read the run's LangGraph trace, the record GET
/documents/{id}/langgraph-trace returns. Only Claude's replies are scripted. For MA-039 the trace is saved through
the real write path (trace._persist and its single writer thread) into an in-memory langgraph_trace table, the
process's memory is cleared as a restart would, and the trace is read back with trace.load.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.events import Event, types
from app.langgraph import trace
from app.langgraph.detection.graph import run_detection
from app.main import app
from app.models import DocumentStatus
from app.workers import detection as detection_worker
from tests.support import detection_harness
from tests.support.detection_documents import credit_report_with_font_and_metadata_tampering
from tests.support.detection_harness import DIGITAL, LAYOUT, Review, Specialist

client = TestClient(app)

ai = detection_harness.ai
store = detection_harness.store
_clear_detection_memory = detection_harness._clear_detection_memory

# Captured at import, before the harness's store fixture swaps it for a no-op.
REAL_PERSIST = trace._persist


def steps(document_id: str, name: str | None = None) -> list[dict]:
    return [s for s in trace.get(document_id)["steps"] if name is None or s["step"] == name]


def specialist_steps(document_id: str) -> dict[str, dict]:
    return {s["specialist"]: s for s in steps(document_id, "detection_specialist")}


def script_layout_and_digital(ai, **digital) -> None:
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool", "layout_anomalies_tool"], confidence=0.8)
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool"], confidence=0.9, **digital)


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-55", "MA-028")
async def test_a_successful_run_stores_each_specialists_tool_calls_in_order_with_their_times(ai, store):
    document_id = credit_report_with_font_and_metadata_tampering(store)
    script_layout_and_digital(ai)

    await run_detection(document_id)

    by_specialist = specialist_steps(document_id)
    # The guard check every run starts with, then the specialist's own calls, in the order it made them.
    assert by_specialist[LAYOUT]["tools_called"] == ["hidden_text_tool", "font_consistency_tool", "layout_anomalies_tool"]
    assert by_specialist[DIGITAL]["tools_called"] == ["prompt_injection_tool", "metadata_tool"]
    offsets = [s["at"] for s in steps(document_id)]
    assert offsets == sorted(offsets)
    order = [s["step"] for s in steps(document_id)]
    assert order.index("detection_start") < order.index("detection_plan") < order.index("detection_specialist")
    assert order.index("detection_done") > max(i for i, s in enumerate(order) if s == "detection_specialist")


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-55", "MA-029")
async def test_a_cancelled_run_keeps_the_tool_calls_made_before_it_stopped_and_the_error(ai, store, monkeypatch):
    # The detection-worker runs it, as on an approval, and the officer cancels it while the lead reviews the
    # specialists' reports: every specialist has made its tool calls by then.
    document_id = credit_report_with_font_and_metadata_tampering(store)
    script_layout_and_digital(ai)
    monkeypatch.setattr(detection_worker, "detection_results", store)
    monkeypatch.setattr(detection_worker.audit, "find", lambda *args, **kwargs: [])  # no cancel from elsewhere
    reviewing, never = asyncio.Event(), asyncio.Event()
    ainvoke = detection_harness.FakeClaude.ainvoke

    async def lead_review_waits(self, messages):
        if "Your specialists have reported" in detection_harness._text(list(messages)[0].content):
            reviewing.set()
            await never.wait()
        return await ainvoke(self, messages)

    monkeypatch.setattr(detection_harness.FakeClaude, "ainvoke", lead_review_waits)
    worker = asyncio.create_task(detection_worker.run(document_id, None, "officer"))
    await reviewing.wait()

    await detection_worker.on_cancel_requested(Event(type=types.DETECTION_CANCEL_REQUESTED, document_id=document_id))
    await worker

    order = [s["step"] for s in steps(document_id)]
    assert specialist_steps(document_id)[LAYOUT]["tools_called"][1:] == ["font_consistency_tool", "layout_anomalies_tool"]
    assert specialist_steps(document_id)[DIGITAL]["tools_called"][1:] == ["metadata_tool"]
    assert order[-1] == "detection_error"
    assert steps(document_id, "detection_error")[-1]["error"] == "Cancelled by loan officer"
    assert store.errors[document_id] == "Cancelled by loan officer"
    assert document_id not in store.reports
    # Back to approved with the error kept, so the officer can run it again (the live run's remark said it couldn't).
    assert store.documents[document_id].status == DocumentStatus.APPROVED
    with (
        patch("app.routers._dependencies.document_repository", store),
        patch("app.routers._dependencies.case_service.is_case_finalised", return_value=False),
        patch("app.routers.detection.detection_results.get",
              side_effect=lambda doc_id: (store.reports.get(doc_id), store.errors.get(doc_id))),
    ):
        retried = await asyncio.to_thread(client.post, f"/documents/{document_id}/detection/retry")
    assert retried.status_code == 200
    never.set()  # this time the lead's review answers
    await detection_worker.run(document_id, None, "officer")
    assert store.documents[document_id].status == DocumentStatus.DETECTED
    assert store.reports[document_id]["verdict"] == "flagged"
    assert document_id not in store.errors


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-55", "MA-031")
async def test_each_tool_call_is_stored_with_its_arguments_and_the_run_with_its_results(ai, store):
    document_id = credit_report_with_font_and_metadata_tampering(store)
    script_layout_and_digital(ai)

    report = await run_detection(document_id)

    for step in steps(document_id, "detection_specialist"):
        assert [call["name"] for call in step["tool_calls"]] == step["tools_called"]
        assert all("args" in call for call in step["tool_calls"])
    # What each tool returned is in the run's saved report: every finding names the tool that raised it.
    stored = store.reports[document_id]
    assert stored == report
    tools_with_results = {f["tool"] for f in stored["findings"]}
    assert {"font_consistency_tool", "metadata_tool"} <= tools_with_results
    assert all(f["summary"] for f in stored["findings"])


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-55", "MA-032")
async def test_the_reasoning_behind_each_run_tool_and_finding_is_stored(ai, store):
    document_id = credit_report_with_font_and_metadata_tampering(store)
    ai.briefs[LAYOUT] = "Credit report with printed figures: check fonts and layout."
    ai.reviews = [Review("The font of one field and the file's metadata both point to editing.")]
    script_layout_and_digital(ai, dismiss={"metadata_tool-2"})

    await run_detection(document_id)

    [plan] = steps(document_id, "detection_plan")
    assert plan["detection_briefs"][LAYOUT] == "Credit report with printed figures: check fonts and layout."  # why it ran
    stored = store.reports[document_id]
    assert all(f["rationale"].startswith("Rated ") for f in stored["findings"])  # why each was kept
    [dismissed] = specialist_steps(document_id)[DIGITAL]["dismissed"]
    assert (dismissed["id"], dismissed["reason"]) == ("metadata_tool-2", "False positive for this document.")
    assert stored["summary"] == "The font of one field and the file's metadata both point to editing."


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-55", "MA-039")
async def test_the_trace_is_read_back_from_storage_after_a_restart(ai, store, monkeypatch):
    table: dict[str, dict] = {}
    monkeypatch.setattr(trace, "_persist", REAL_PERSIST)
    monkeypatch.setattr(trace, "_write_row", lambda snapshot: table.__setitem__(snapshot["document_id"], snapshot))
    monkeypatch.setattr(trace, "get_persisted", table.get)
    document_id = credit_report_with_font_and_metadata_tampering(store)
    script_layout_and_digital(ai)
    await run_detection(document_id)
    in_memory = trace.get(document_id)

    trace._traces.clear()  # the restart: nothing in this process's memory

    assert trace.get(document_id) is None
    assert trace.load(document_id) == in_memory
    [done] = [s for s in trace.load(document_id)["steps"] if s["step"] == "detection_done"]
    assert done["detection_verdict"] == "flagged"


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-283", "AE-016")
async def test_confidence_scores_and_findings_are_saved_with_the_completed_run(ai, store):
    document_id = credit_report_with_font_and_metadata_tampering(store)
    script_layout_and_digital(ai)

    report = await run_detection(document_id)

    saved = store.reports[document_id]
    assert saved == report
    assert saved["findings"]
    confidences = {c["specialist"]: c["decision_confidences"][0]["confidence"] for c in saved["consulted"]}
    assert (confidences[LAYOUT], confidences[DIGITAL]) == (0.8, 0.9)
    font = next(f for f in saved["findings"] if f["tool"] == "font_consistency_tool")
    assert font["agent_confidences"] == [{"specialist": LAYOUT, "confidence": 0.8}]
    # The run's own score is its least confident decision.
    assert saved["confidence"] == min(d["confidence"] for d in saved["agent_decisions"])
