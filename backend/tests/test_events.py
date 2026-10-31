from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.events import Event, bus, officer_event, registry, types
from app.events import push as event_push
from app.workers import detection as detection_worker

# Captured at import, before conftest's published_events fixture replaces it.
_REAL_DISPATCH = bus._dispatch_in_process


@pytest.fixture
def clean_registry():
    saved = dict(registry._by_role)
    registry.clear()
    yield
    registry.clear()
    registry._by_role.update(saved)


def _push_body(event: Event | None = None, raw: str | None = None) -> dict:
    if raw is None:
        assert event is not None
        raw = base64.b64encode(event.model_dump_json().encode()).decode()
    data = raw
    return {"message": {"data": data, "messageId": "m-1"}, "subscription": "projects/p/subscriptions/s"}


def test_event_round_trips_through_its_audit_row() -> None:
    event = Event(type=types.DOCUMENT_APPROVED, document_id="d", case_id="c", detail={"n": 1})
    back = Event.from_audit_row({**event.to_audit_row(), "occurred_at": event.occurred_at.isoformat()})
    assert back == event


def test_detail_is_made_json_safe() -> None:
    from datetime import datetime, timezone

    when = datetime(2026, 10, 21, tzinfo=timezone.utc)
    assert Event(type="x", detail={"at": when}).detail == {"at": str(when)}


def test_officer_event_without_a_request_is_the_system() -> None:
    event = officer_event(types.CASE_CREATED, case_id="c")
    assert (event.actor_type, event.actor) == ("system", "system")


def test_a_role_cannot_handle_a_type_it_does_not_subscribe_to(clean_registry) -> None:
    with pytest.raises(ValueError):
        registry.install("case-worker", {types.DOCUMENT_APPROVED: AsyncMock()})


def test_every_consumed_type_has_a_name() -> None:
    names = {v for k, v in vars(types).items() if k.isupper() and isinstance(v, str)}
    for role, subscribed in types.SUBSCRIPTIONS.items():
        assert subscribed <= names, role


async def test_in_process_dispatch_runs_every_installed_role(clean_registry, monkeypatch) -> None:
    monkeypatch.setattr(bus, "_dispatch_in_process", _REAL_DISPATCH)
    seen: list[str] = []

    async def worker(event: Event) -> None:
        seen.append("worker")

    async def audit(event: Event) -> None:
        seen.append("audit")

    registry.install("case-worker", {types.CASE_ANALYSIS_REQUESTED: worker})
    registry.install("audit-metrics", {types.EVERY_TYPE: audit})
    await bus.publish(Event(type=types.CASE_ANALYSIS_REQUESTED, case_id="c"))
    await asyncio.sleep(0.01)
    assert sorted(seen) == ["audit", "worker"]


def test_push_runs_only_the_named_roles_handler(clean_registry) -> None:
    worker, audit = AsyncMock(), AsyncMock()
    registry.install("detection-worker", {types.DOCUMENT_APPROVED: worker})
    registry.install("audit-metrics", {types.EVERY_TYPE: audit})
    app = FastAPI()
    app.include_router(event_push.router)
    client = TestClient(app)

    event = Event(type=types.DOCUMENT_APPROVED, document_id="d")
    assert client.post("/push/audit-metrics", json=_push_body(event)).status_code == 204
    audit.assert_awaited_once()
    worker.assert_not_awaited()

    assert client.post("/push/detection-worker", json=_push_body(Event(type="other.type"))).status_code == 204
    worker.assert_not_awaited()
    assert client.post("/push/detection-worker", json=_push_body(event)).status_code == 204
    worker.assert_awaited_once()


def test_push_refuses_a_malformed_message_and_an_absent_role(clean_registry) -> None:
    registry.install("detection-worker", {types.DOCUMENT_APPROVED: AsyncMock()})
    app = FastAPI()
    app.include_router(event_push.router)
    client = TestClient(app)

    bad = base64.b64encode(b"{not json").decode()
    assert client.post("/push/detection-worker", json=_push_body(raw=bad)).status_code == 400
    assert client.post("/push/doc-worker", json=_push_body(Event(type="x"))).status_code == 404


def test_a_failing_handler_is_not_acked(clean_registry) -> None:
    registry.install("detection-worker", {types.DOCUMENT_APPROVED: AsyncMock(side_effect=RuntimeError("boom"))})
    app = FastAPI()
    app.include_router(event_push.router)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/push/detection-worker", json=_push_body(Event(type=types.DOCUMENT_APPROVED)))
    assert response.status_code == 500


async def test_detection_skips_an_untyped_approval() -> None:
    with patch("app.workers.detection.run_detection", new=AsyncMock()) as run:
        await detection_worker.on_requested(Event(
            type=types.DOCUMENT_APPROVED, document_id="d", detail={"skip_detection": True},
        ))
    run.assert_not_awaited()


async def test_detection_run_announces_detected(published_events) -> None:
    with (
        patch("app.workers.detection.run_detection", new=AsyncMock(return_value={"verdict": "clean", "findings": []})),
        patch("app.workers.detection.push_case_snapshot", new=AsyncMock()) as push,
    ):
        await detection_worker.on_requested(Event(type=types.DOCUMENT_APPROVED, document_id="d", case_id="c"))

    push.assert_awaited_once_with("c")
    assert [(e.type, e.outcome) for e in published_events] == [(types.DOCUMENT_DETECTED, "success")]
    detail = published_events[0].detail
    assert (detail["verdict"], detail["findings"], detail["run"]) == ("clean", 0, "detection")
    # The run's Claude usage, so the audit trail carries cost per document.
    assert detail["llm_cost_usd"] == 0 and detail["llm_calls"] == 0


async def test_detection_failure_announces_failed(published_events) -> None:
    with (
        patch("app.workers.detection.run_detection", new=AsyncMock(side_effect=RuntimeError("graph broke"))),
        patch("app.workers.detection.push_case_snapshot", new=AsyncMock()),
    ):
        await detection_worker.on_requested(Event(type=types.DOCUMENT_APPROVED, document_id="d", case_id="c"))

    assert [(e.type, e.outcome) for e in published_events] == [(types.DETECTION_FAILED, "failed")]


async def test_an_officer_cancel_saves_the_error_and_ends_the_run(published_events) -> None:
    started = asyncio.Event()

    async def slow(_: str) -> dict:
        started.set()
        await asyncio.sleep(10)
        return {}

    with (
        patch("app.workers.detection.run_detection", new=slow),
        patch("app.workers.detection.push_case_snapshot", new=AsyncMock()),
        patch("app.workers.detection.langgraph_trace") as trace,
        patch("app.workers.detection.detection_results") as results,
        patch("app.workers.detection.end_unfinished_run") as end_run,
    ):
        run = asyncio.create_task(detection_worker.on_requested(
            Event(type=types.DOCUMENT_APPROVED, document_id="d", case_id="c")
        ))
        await asyncio.wait_for(started.wait(), timeout=2)
        await detection_worker.on_cancel_requested(Event(type=types.DETECTION_CANCEL_REQUESTED, document_id="d"))
        await asyncio.wait_for(run, timeout=2)

    end_run.assert_called_once_with("d")
    trace.record.assert_called_once_with("d", "detection_error", error="Cancelled by loan officer")
    results.save_error.assert_called_once_with("d", "Cancelled by loan officer")
    assert [(e.type, e.outcome) for e in published_events] == [(types.DETECTION_FAILED, "cancelled")]


def test_terraform_subscriptions_match_the_code() -> None:
    from pathlib import Path

    exported = Path(__file__).resolve().parents[2] / "deploy" / "terraform" / "subscriptions.json"
    assert exported.read_text() == types.subscriptions_export(), "run: python -m app.events.types"
