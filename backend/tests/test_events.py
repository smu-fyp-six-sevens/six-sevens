from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
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
    both_ran = asyncio.Event()

    def ran(role: str) -> None:
        seen.append(role)
        if len(seen) == 2:
            both_ran.set()

    async def worker(event: Event) -> None:
        ran("worker")

    async def audit(event: Event) -> None:
        ran("audit")

    registry.install("case-worker", {types.CASE_ANALYSIS_REQUESTED: worker})
    registry.install("audit-metrics", {types.EVERY_TYPE: audit})
    await bus.publish(Event(type=types.CASE_ANALYSIS_REQUESTED, case_id="c"))
    await asyncio.wait_for(both_ran.wait(), timeout=2)
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


# --- Push authentication: Pub/Sub's OIDC token, checked when PUSH_AUDIENCE is set --------------------------


@pytest.fixture
def signed_push(clean_registry, monkeypatch):
    """A push endpoint that requires Pub/Sub's token from the given service account, and the worker behind it."""
    worker = AsyncMock()
    registry.install("detection-worker", {types.DOCUMENT_APPROVED: worker})
    monkeypatch.setattr(settings, "PUSH_AUDIENCE", "https://detection-worker.example")
    monkeypatch.setattr(settings, "PUSH_SERVICE_ACCOUNT", "pubsub-push@project.iam.gserviceaccount.com")
    app = FastAPI()
    app.include_router(event_push.router)
    return TestClient(app), worker


def _push_with(client: TestClient, authorization: str | None):
    headers = {"Authorization": authorization} if authorization else {}
    return client.post("/push/detection-worker", json=_push_body(Event(type=types.DOCUMENT_APPROVED)), headers=headers)


@pytest.mark.parametrize("authorization", [None, "Basic abc", "Bearer"])
def test_a_push_without_a_bearer_token_is_refused(signed_push, authorization) -> None:
    client, worker = signed_push

    response = _push_with(client, authorization)

    assert (response.status_code, response.json()["detail"]) == (401, "Missing push token")
    worker.assert_not_awaited()


def test_a_push_with_a_token_that_does_not_verify_is_refused(signed_push) -> None:
    client, worker = signed_push
    with patch("google.oauth2.id_token.verify_oauth2_token", side_effect=ValueError("Token expired")) as verify:
        response = _push_with(client, "Bearer forged")

    assert (response.status_code, response.json()["detail"]) == (401, "Invalid push token")
    assert verify.call_args.kwargs["audience"] == "https://detection-worker.example"
    worker.assert_not_awaited()


@pytest.mark.parametrize("claims", [
    {"email": "someone-else@project.iam.gserviceaccount.com", "email_verified": True},
    {"email": "pubsub-push@project.iam.gserviceaccount.com", "email_verified": False},
])
def test_a_push_signed_by_another_account_is_refused(signed_push, claims) -> None:
    client, worker = signed_push
    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=claims):
        response = _push_with(client, "Bearer token")

    assert (response.status_code, response.json()["detail"]) == (401, "Push token from the wrong account")
    worker.assert_not_awaited()


def test_a_push_signed_by_pubsub_runs_the_handler(signed_push) -> None:
    client, worker = signed_push
    claims = {"email": "pubsub-push@project.iam.gserviceaccount.com", "email_verified": True}
    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=claims):
        response = _push_with(client, "Bearer token")

    assert response.status_code == 204
    worker.assert_awaited_once()


# --- Publishing: an event that triggers work fails its caller when it can't be published ---------------------


def _bus_down(event: Event) -> None:
    raise RuntimeError("bus down")


async def test_publish_raises_when_the_event_cannot_be_published(monkeypatch) -> None:
    monkeypatch.setattr(bus, "_dispatch_in_process", _bus_down)

    with pytest.raises(RuntimeError, match="bus down"):
        await bus.publish(Event(type=types.DOCUMENT_APPROVED, document_id="d"))


async def test_record_logs_a_failed_publish_without_raising(monkeypatch, caplog) -> None:
    monkeypatch.setattr(bus, "_dispatch_in_process", _bus_down)

    await bus.record(Event(type=types.NOTE_ADDED, case_id="c"))

    assert "Failed to publish note.added event" in caplog.text


async def test_pubsub_publishes_the_event_with_its_type_and_owner_as_attributes(monkeypatch) -> None:
    class Publisher:
        def __init__(self) -> None:
            self.sent: list[tuple[str, bytes, dict]] = []

        def topic_path(self, project: str, topic: str) -> str:
            return f"projects/{project}/topics/{topic}"

        def publish(self, topic: str, data: bytes, **attributes: str):
            self.sent.append((topic, data, attributes))
            return SimpleNamespace(result=lambda timeout: "message-1")

    publisher = Publisher()
    monkeypatch.setattr(settings, "EVENT_TRANSPORT", "pubsub")
    monkeypatch.setattr(settings, "GOOGLE_CLOUD_PROJECT", "six-sevens")
    monkeypatch.setattr(settings, "PUBSUB_TOPIC", "events")
    monkeypatch.setattr(bus, "_publisher", publisher)
    event = Event(type=types.RECOVERY_REQUESTED, document_id="d", detail={types.OWNER_ATTRIBUTE: "detection-worker"})

    await bus.publish(event)

    [(topic, data, attributes)] = publisher.sent
    assert topic == "projects/six-sevens/topics/events"
    assert Event.model_validate_json(data) == event
    assert (attributes["type"], attributes[types.OWNER_ATTRIBUTE]) == (types.RECOVERY_REQUESTED, "detection-worker")


async def test_a_pubsub_publish_that_times_out_raises(monkeypatch) -> None:
    def timed_out(timeout: float):
        raise TimeoutError("no ack from Pub/Sub")

    publisher = SimpleNamespace(topic_path=lambda project, topic: topic,
                                publish=lambda topic, data, **attributes: SimpleNamespace(result=timed_out))
    monkeypatch.setattr(settings, "EVENT_TRANSPORT", "pubsub")
    monkeypatch.setattr(bus, "_publisher", publisher)

    with pytest.raises(TimeoutError):
        await bus.publish(Event(type=types.DOCUMENT_APPROVED, document_id="d"))


async def test_detection_skips_an_untyped_approval() -> None:
    with patch("app.workers.detection.run_detection", new=AsyncMock()) as run:
        await detection_worker.on_requested(Event(
            type=types.DOCUMENT_APPROVED, document_id="d", detail={"skip_detection": True},
        ))
    run.assert_not_awaited()


async def test_detection_run_announces_detected(published_events) -> None:
    with (
        patch("app.workers.detection.run_detection", new=AsyncMock(return_value={"verdict": "clean", "findings": []})),
        patch("app.workers.detection.document_repository.update_status", return_value=True),
        patch("app.workers.detection.push_case_snapshot", new=AsyncMock()) as push,
    ):
        await detection_worker.on_requested(Event(type=types.DOCUMENT_APPROVED, document_id="d", case_id="c"))

    # The document's status write reaches viewers through Realtime, where it runs in this process.
    push.assert_awaited_once_with("c", realtime_covers=True)
    assert [(e.type, e.outcome) for e in published_events] == [(types.DOCUMENT_DETECTED, "success")]
    detail = published_events[0].detail
    assert (detail["verdict"], detail["findings"], detail["run"]) == ("clean", 0, "detection")
    # The run's Claude usage, so the audit trail carries cost per document.
    assert detail["llm_cost_usd"] == 0 and detail["llm_calls"] == 0


async def test_detection_failure_announces_failed(published_events) -> None:
    with (
        patch("app.workers.detection.run_detection", new=AsyncMock(side_effect=RuntimeError("graph broke"))),
        patch("app.workers.detection.document_repository.update_status", return_value=True),
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
        patch("app.workers.detection.document_repository.update_status", return_value=True),
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
