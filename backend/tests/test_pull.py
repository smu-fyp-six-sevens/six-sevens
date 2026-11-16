"""The pull adapter (events/pull.py), which delivers every event on GKE: ack, nack, scopes, drain and restarts."""

from __future__ import annotations

import asyncio
import concurrent.futures
from types import SimpleNamespace

import pytest

from app import case_repository, document_repository, query_stats
from app.config import settings
from app.events import Event, bus, emulator_setup, pull, registry, types


class FakeMessage:
    def __init__(self, data: bytes, log: list[str] | None = None) -> None:
        self.data = data
        self.message_id = "m-1"
        self.delivery_attempt = 1
        self.attributes: dict[str, str] = {}
        self.log = log if log is not None else []

    def ack(self) -> None:
        self.log.append("ack")

    def nack(self) -> None:
        self.log.append("nack")


def _message(event_type: str = types.CASE_ANALYSIS_REQUESTED, log: list[str] | None = None) -> FakeMessage:
    return FakeMessage(Event(type=event_type, case_id="c1").model_dump_json().encode(), log)


@pytest.fixture
def clean_registry():
    saved = dict(registry._by_role)
    registry.clear()
    yield
    registry.clear()
    registry._by_role.update(saved)


@pytest.fixture
def pull_state():
    pull._supervisors.clear()
    pull._inflight.clear()
    saved = pull._subscriber
    yield
    pull._accepting = False
    pull._supervisors.clear()
    pull._inflight.clear()
    pull._subscriber = saved


def _install(handler) -> None:
    registry.install("case-worker", {types.CASE_ANALYSIS_REQUESTED: handler})


# ---- one message: the same outcomes as push's 2xx and 5xx


async def test_a_handler_that_returns_acks(clean_registry) -> None:
    seen: list[str] = []

    async def handler(event: Event) -> None:
        seen.append(event.case_id or "")

    _install(handler)
    message = _message()
    await pull._handle("case-worker", message)  # type: ignore[arg-type]
    assert seen == ["c1"]
    assert message.log == ["ack"]


async def test_a_handler_that_raises_nacks(clean_registry) -> None:
    async def handler(event: Event) -> None:
        raise RuntimeError("boom")

    _install(handler)
    message = _message()
    await pull._handle("case-worker", message)  # type: ignore[arg-type]
    assert message.log == ["nack"]


async def test_malformed_data_nacks_without_running_anything(clean_registry) -> None:
    called: list[Event] = []

    async def handler(event: Event) -> None:
        called.append(event)

    _install(handler)
    message = FakeMessage(b"not an event")
    await pull._handle("case-worker", message)  # type: ignore[arg-type]
    assert message.log == ["nack"]
    assert called == []


async def test_a_type_the_role_does_not_handle_acks(clean_registry) -> None:
    called: list[Event] = []

    async def handler(event: Event) -> None:
        called.append(event)

    _install(handler)
    message = _message(types.DOCUMENT_APPROVED)
    await pull._handle("case-worker", message)  # type: ignore[arg-type]
    assert message.log == ["ack"]
    assert called == []


async def test_a_cancelled_run_nacks_and_stays_cancelled(clean_registry) -> None:
    started = asyncio.Event()

    async def handler(event: Event) -> None:
        started.set()
        await asyncio.sleep(60)

    _install(handler)
    message = _message()
    task = asyncio.create_task(pull._handle("case-worker", message))  # type: ignore[arg-type]
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert message.log == ["nack"]


async def test_scopes_are_open_and_case_refreshes_flushed_before_the_ack(clean_registry, monkeypatch) -> None:
    log: list[str] = []
    inside: dict[str, object] = {}

    async def handler(event: Event) -> None:
        scope = query_stats._current.get()
        inside["query_scope"] = scope.name if scope is not None else None
        inside["case_scope"] = case_repository._request_cases.get()
        requested = document_repository._request_cases.get()
        assert requested is not None
        requested.add("c1")  # what a document write's _sync_case does

    def flush(case_ids: set[str]) -> None:
        log.append(f"flush {sorted(case_ids)}")

    monkeypatch.setattr(document_repository, "flush_syncs", flush)
    _install(handler)
    message = _message(log=log)
    await pull._handle("case-worker", message)  # type: ignore[arg-type]
    assert inside["query_scope"] == "PULL case-worker"
    assert inside["case_scope"] == {}
    assert log == ["flush ['c1']", "ack"]
    assert case_repository._request_cases.get() is None  # closed again


async def test_no_flush_when_nothing_was_scheduled(clean_registry, monkeypatch) -> None:
    flushed: list[set[str]] = []
    monkeypatch.setattr(document_repository, "flush_syncs", flushed.append)

    async def handler(event: Event) -> None:
        pass

    _install(handler)
    message = _message()
    await pull._handle("case-worker", message)  # type: ignore[arg-type]
    assert flushed == []
    assert message.log == ["ack"]


# ---- shutdown


async def test_stop_drains_then_cancels_and_refuses_new_messages(clean_registry, pull_state, monkeypatch) -> None:
    monkeypatch.setattr(settings, "PULL_DRAIN_SECONDS", 0.3)
    finished_by_drain = asyncio.Event()
    hung = asyncio.Event()
    cleaned_up: list[str] = []

    async def handler(event: Event) -> None:
        if event.detail.get("slow"):
            hung.set()
            try:
                await asyncio.sleep(60)
            finally:
                cleaned_up.append("slow")
        else:
            await finished_by_drain.wait()

    _install(handler)
    quick = _message()
    slow = FakeMessage(Event(type=types.CASE_ANALYSIS_REQUESTED, case_id="c2", detail={"slow": True}).model_dump_json().encode())
    pull._accepting = True
    pull._spawn("case-worker", quick)  # type: ignore[arg-type]
    pull._spawn("case-worker", slow)  # type: ignore[arg-type]
    await hung.wait()

    stopping = asyncio.create_task(pull.stop())
    await asyncio.sleep(0)
    late = _message()
    pull._spawn("case-worker", late)  # type: ignore[arg-type]
    assert late.log == ["nack"]  # arrived during the drain

    finished_by_drain.set()  # the quick run finishes inside PULL_DRAIN_SECONDS
    await stopping
    assert quick.log == ["ack"]
    assert slow.log == ["nack"]  # still going at the end: cancelled, so redelivered
    assert cleaned_up == ["slow"]  # its own cleanup ran
    assert not pull._inflight


async def test_stop_closes_the_streams(pull_state, monkeypatch) -> None:
    client = _FakeSubscriber([None])  # one stream that never ends on its own
    monkeypatch.setattr(pull, "_subscriber", client)
    loop = asyncio.get_running_loop()
    pull._supervisors.append(asyncio.create_task(pull._supervise("case-worker", loop)))
    while not client.futures:
        await asyncio.sleep(0)
    await pull.stop()
    assert client.futures[0].cancelled()
    assert pull._supervisors == []


# ---- the stream supervisor


class _FakeSubscriber:
    """Each subscribe() takes the next outcome: an exception ends that stream with it, None leaves it open."""

    def __init__(self, outcomes: list[Exception | None]) -> None:
        self.outcomes = list(outcomes)
        self.futures: list[concurrent.futures.Future] = []
        self.paths: list[str] = []

    def subscription_path(self, project: str, sub: str) -> str:
        return f"projects/{project}/subscriptions/{sub}"

    def subscribe(self, path: str, callback, flow_control) -> concurrent.futures.Future:
        self.paths.append(path)
        future: concurrent.futures.Future = concurrent.futures.Future()
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome is not None:
            future.set_exception(outcome)
        self.futures.append(future)
        return future


async def test_a_failed_stream_restarts_with_doubling_backoff(monkeypatch) -> None:
    real_sleep = asyncio.sleep
    sleeps: list[float] = []

    async def fake_sleep(seconds: float, *args, **kwargs):
        if seconds >= 0.5:  # the supervisor's backoff; other waits pass through
            sleeps.append(seconds)
            seconds = 0
        return await real_sleep(seconds, *args, **kwargs)

    client = _FakeSubscriber([RuntimeError("permission denied"), RuntimeError("again"), RuntimeError("again"), None])
    monkeypatch.setattr(pull, "_subscriber", client)
    monkeypatch.setattr(settings, "GOOGLE_CLOUD_PROJECT", "p")
    monkeypatch.setattr(pull.asyncio, "sleep", fake_sleep)
    task = asyncio.create_task(pull._supervise("case-worker", asyncio.get_running_loop()))
    while len(client.futures) < 4:
        await real_sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    first = pull._RESTART_FIRST_SECONDS
    assert sleeps == [first, first * 2, first * 4]
    assert client.paths == ["projects/p/subscriptions/case-worker-push"] * 4
    assert client.futures[-1].cancelled()


async def test_backoff_resets_after_a_healthy_run(monkeypatch) -> None:
    real_sleep = asyncio.sleep
    sleeps: list[float] = []
    clock = iter([0.0, 1.0, 10.0, 10.0 + pull._HEALTHY_RUN_SECONDS + 5, 200.0, 201.0, 300.0, 301.0])

    async def fake_sleep(seconds: float, *args, **kwargs):
        if seconds >= 0.5:
            sleeps.append(seconds)
            seconds = 0
        return await real_sleep(seconds, *args, **kwargs)

    client = _FakeSubscriber([RuntimeError("a"), RuntimeError("b"), RuntimeError("c"), None])
    monkeypatch.setattr(pull, "_subscriber", client)
    monkeypatch.setattr(pull.asyncio, "sleep", fake_sleep)
    # The module's own clock only: the event loop reads time.monotonic too.
    monkeypatch.setattr(pull, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    task = asyncio.create_task(pull._supervise("case-worker", asyncio.get_running_loop()))
    while len(client.futures) < 4:
        await real_sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    first = pull._RESTART_FIRST_SECONDS
    # 1st fails fast; 2nd ran over the healthy time, so it starts again from the first step; 3rd fails fast.
    assert sleeps == [first, first, first * 2]


def test_receive_hands_the_message_to_the_loop_or_nacks_when_it_is_closed() -> None:
    loop = asyncio.new_event_loop()
    loop.close()
    message = _message()
    pull._receive("case-worker", loop, message)  # type: ignore[arg-type]
    assert message.log == ["nack"]


# ---- wiring and validation


@pytest.mark.parametrize(
    ("transport", "delivery", "max_messages", "ok"),
    [
        ("pubsub", "pull", 2, True),
        ("pubsub", "push", 2, True),
        ("pubsub", "none", 2, True),
        ("pubsub", "poll", 2, False),
        ("inprocess", "pull", 2, False),
        ("pubsub", "pull", 0, False),
    ],
)
def test_check_transport(monkeypatch, transport: str, delivery: str, max_messages: int, ok: bool) -> None:
    monkeypatch.setattr(settings, "EVENT_TRANSPORT", transport)
    monkeypatch.setattr(settings, "EVENT_DELIVERY", delivery)
    monkeypatch.setattr(settings, "PULL_MAX_MESSAGES", max_messages)
    monkeypatch.setattr(settings, "GOOGLE_CLOUD_PROJECT", "p")
    if ok:
        bus.check_transport()
    else:
        with pytest.raises(ValueError):
            bus.check_transport()


class _FakeEmulatorClient:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def topic_path(self, project: str, topic: str) -> str:
        return f"projects/{project}/topics/{topic}"

    def subscription_path(self, project: str, sub: str) -> str:
        return f"projects/{project}/subscriptions/{sub}"

    def create_topic(self, name: str) -> None:
        pass

    def delete_subscription(self, subscription: str) -> None:
        pass

    def create_subscription(self, request: dict) -> None:
        self.requests.append(request)


def _run_emulator_setup(monkeypatch, delivery: str, filters: str | None) -> list[dict]:
    client = _FakeEmulatorClient()
    monkeypatch.setattr(emulator_setup, "pubsub_v1", SimpleNamespace(
        PublisherClient=lambda: client, SubscriberClient=lambda: client,
    ))
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "localhost:8085")
    if filters is None:
        monkeypatch.delenv("EMULATOR_FILTERS", raising=False)
    else:
        monkeypatch.setenv("EMULATOR_FILTERS", filters)
    monkeypatch.setattr(settings, "EVENT_DELIVERY", delivery)
    monkeypatch.setattr(emulator_setup.sys, "argv", ["emulator_setup"])
    emulator_setup.main()
    return [r for r in client.requests if not r["name"].endswith("-dead-letter-pull")]


def test_emulator_subscriptions_in_pull_mode_have_no_push_endpoint(monkeypatch) -> None:
    requests = _run_emulator_setup(monkeypatch, "pull", None)
    assert {r["name"].rsplit("/", 1)[1] for r in requests} == {f"{role}-push" for role in types.SUBSCRIPTIONS}
    assert all("push_config" not in r for r in requests)
    assert any(r.get("filter") for r in requests)


def test_emulator_subscriptions_in_push_mode_push(monkeypatch) -> None:
    requests = _run_emulator_setup(monkeypatch, "push", None)
    assert all(r["push_config"]["push_endpoint"].endswith(r["name"].rsplit("/", 1)[1].removesuffix("-push"))
               for r in requests)


def test_emulator_filters_off_gives_every_role_every_event(monkeypatch) -> None:
    requests = _run_emulator_setup(monkeypatch, "pull", "false")
    assert all("filter" not in r for r in requests)
