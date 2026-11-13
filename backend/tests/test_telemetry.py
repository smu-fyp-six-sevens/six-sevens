"""
Trace propagation across the event bus: the request that published an event and the worker that handled it are one
trace, carried as a W3C traceparent in the message attributes (telemetry.inject on publish, continued_from on push).
"""
from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from app import telemetry
from app.config import settings
from app.events import Event, bus, registry, types
from app.events import push as event_push

TRACE_ID, SPAN_ID = 0x4BF92F3577B34DA6A3CE929D0E0E4736, 0x00F067AA0BA902B7
TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


@pytest.fixture
def in_a_trace():
    """A request's trace being current, as the API's instrumentation makes it."""
    span = NonRecordingSpan(SpanContext(TRACE_ID, SPAN_ID, is_remote=False, trace_flags=TraceFlags(TraceFlags.SAMPLED)))
    token = otel_context.attach(trace.set_span_in_context(span))
    yield
    otel_context.detach(token)


def current_trace_id() -> int:
    return trace.get_current_span().get_span_context().trace_id


class _RecordingSpan:
    def __init__(self) -> None:
        self.attributes: dict = {}

    def set_attribute(self, key: str, value) -> None:
        self.attributes[key] = value


def test_a_calls_usage_and_cost_are_put_on_its_span() -> None:
    from langchain_core.messages import AIMessage

    # 1,500 input tokens, 1,000 of them read from the cache: 500 uncached.
    response = AIMessage(
        content="ok", response_metadata={"model_name": "claude-sonnet-5-5"},
        usage_metadata={"input_tokens": 1500, "output_tokens": 200, "total_tokens": 1700,
                        "input_token_details": {"cache_read": 1000}},
    )
    span = _RecordingSpan()

    telemetry.record_usage(span, response)

    assert span.attributes == {
        "llm.model": "claude-sonnet-5-5", "llm.input_tokens": 500, "llm.output_tokens": 200,
        "llm.cache_read_tokens": 1000, "llm.cache_write_tokens": 0,
        # Sonnet 5.5 per million: 2 in, 10 out, 0.10 cache read.
        "llm.cost_usd": round((500 * 2 + 200 * 10 + 1000 * 0.10) / 1_000_000, 6),
    }


def test_a_call_without_usage_puts_nothing_on_its_span() -> None:
    from langchain_core.messages import AIMessage

    span = _RecordingSpan()

    telemetry.record_usage(span, AIMessage(content="ok"))
    telemetry.record_usage(span, object())  # never raises, whatever it's given

    assert span.attributes == {}


@pytest.mark.usefixtures("in_a_trace")
def test_the_current_trace_is_carried_as_a_traceparent() -> None:
    assert telemetry.inject() == {"traceparent": TRACEPARENT}


def test_no_trace_is_carried_when_there_is_none() -> None:
    assert telemetry.inject() == {}


def test_a_carried_trace_is_continued_and_then_let_go() -> None:
    with telemetry.continued_from({"traceparent": TRACEPARENT}):
        assert current_trace_id() == TRACE_ID

    assert current_trace_id() != TRACE_ID


@pytest.mark.usefixtures("in_a_trace")
def test_a_message_without_a_traceparent_leaves_the_context_alone() -> None:
    with telemetry.continued_from({"type": types.DOCUMENT_APPROVED}):
        assert current_trace_id() == TRACE_ID


@pytest.mark.usefixtures("in_a_trace")
async def test_an_event_published_to_pubsub_carries_the_publishers_trace(monkeypatch) -> None:
    sent: list[dict] = []

    def publish(topic: str, data: bytes, **attributes: str):
        sent.append(attributes)
        return SimpleNamespace(result=lambda timeout: "message-1")

    monkeypatch.setattr(settings, "EVENT_TRANSPORT", "pubsub")
    monkeypatch.setattr(bus, "_publisher", SimpleNamespace(topic_path=lambda project, topic: topic, publish=publish))

    await bus.publish(Event(type=types.DOCUMENT_APPROVED, document_id="doc-1"))

    assert sent[0]["traceparent"] == TRACEPARENT


@pytest.fixture
def clean_registry():
    saved = dict(registry._by_role)
    registry.clear()
    yield
    registry.clear()
    registry._by_role.update(saved)


def test_a_pushed_event_is_handled_in_the_publishers_trace(clean_registry, monkeypatch) -> None:
    seen: list[int] = []

    async def handler(event: Event) -> None:
        seen.append(current_trace_id())

    registry.install("detection-worker", {types.DOCUMENT_APPROVED: handler})
    monkeypatch.setattr(settings, "PUSH_AUDIENCE", "")
    app = FastAPI()
    app.include_router(event_push.router)
    data = base64.b64encode(Event(type=types.DOCUMENT_APPROVED, document_id="doc-1").model_dump_json().encode()).decode()

    response = TestClient(app).post("/push/detection-worker", json={
        "message": {"data": data, "messageId": "m-1", "attributes": {"traceparent": TRACEPARENT}},
    })

    assert response.status_code == 204
    assert seen == [TRACE_ID]
