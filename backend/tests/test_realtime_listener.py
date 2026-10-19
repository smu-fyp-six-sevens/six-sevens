from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import realtime_listener
from app.case_broadcaster import CaseBroadcaster


@pytest.fixture(autouse=True)
def _reset_module_state():
    """
    realtime_listener holds process-global state (_client, _channel,
    _pending) that start()/stop() mutate — reset it before and after every
    test so tests can't leak state into each other.
    """
    realtime_listener._client = None
    realtime_listener._channel = None
    realtime_listener._pending.clear()
    yield
    realtime_listener._client = None
    realtime_listener._channel = None
    realtime_listener._pending.clear()


@pytest.mark.asyncio
async def test_debounced_publish_builds_and_broadcasts_snapshot_after_the_delay() -> None:
    with (
        patch("app.realtime_listener.asyncio.sleep", new=AsyncMock()),
        patch(
            "app.realtime_listener.case_service.build_case_snapshot",
            return_value={"type": "snapshot"},
        ) as mock_build,
        patch("app.realtime_listener.broadcaster.publish") as mock_publish,
    ):
        realtime_listener._pending["case-1"] = MagicMock()

        await realtime_listener._debounced_publish("case-1")

        mock_build.assert_called_once_with("case-1")
        mock_publish.assert_called_once_with("case-1", {"type": "snapshot"})
        # Always pops itself out of _pending once it actually runs.
        assert "case-1" not in realtime_listener._pending


@pytest.mark.asyncio
async def test_debounced_publish_returns_quietly_when_cancelled_during_the_delay() -> None:
    with (
        patch(
            "app.realtime_listener.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)
        ),
        patch("app.realtime_listener.case_service.build_case_snapshot") as mock_build,
        patch("app.realtime_listener.broadcaster.publish") as mock_publish,
    ):
        await realtime_listener._debounced_publish("case-1")

        mock_build.assert_not_called()
        mock_publish.assert_not_called()


@pytest.mark.asyncio
async def test_debounced_publish_swallows_snapshot_build_errors() -> None:
    with (
        patch("app.realtime_listener.asyncio.sleep", new=AsyncMock()),
        patch(
            "app.realtime_listener.case_service.build_case_snapshot",
            side_effect=RuntimeError("boom"),
        ),
        patch("app.realtime_listener.broadcaster.publish") as mock_publish,
    ):
        realtime_listener._pending["case-1"] = MagicMock()

        # Should not raise even though build_case_snapshot blew up.
        await realtime_listener._debounced_publish("case-1")

        mock_publish.assert_not_called()
        assert "case-1" not in realtime_listener._pending


@pytest.mark.asyncio
async def test_debounced_publish_skips_broadcast_when_case_no_longer_exists() -> None:
    with (
        patch("app.realtime_listener.asyncio.sleep", new=AsyncMock()),
        patch("app.realtime_listener.case_service.build_case_snapshot", return_value=None),
        patch("app.realtime_listener.broadcaster.publish") as mock_publish,
    ):
        await realtime_listener._debounced_publish("case-1")

        mock_publish.assert_not_called()


# --- a change event, end to end: real debounce, real broadcaster, a viewer's queue ---------------


def _open_cases(*case_ids: str):
    """A real broadcaster with one viewer per case, and a snapshot builder that records what it built."""
    live = CaseBroadcaster()
    viewers = {case_id: live.subscribe(case_id) for case_id in case_ids}
    built: list[str] = []

    def build(case_id: str) -> dict:
        built.append(case_id)
        return {"type": "snapshot", "case": case_id}

    return live, viewers, built, build


async def _deliver(live, build, *payloads: dict) -> None:
    """Feeds change events to the listener and waits out the debounce."""
    with (
        patch.object(realtime_listener, "broadcaster", live),
        patch.object(realtime_listener, "DEBOUNCE_SECONDS", 0.01),
        patch("app.realtime_listener.case_service.build_case_snapshot", side_effect=build),
    ):
        try:
            for payload in payloads:
                realtime_listener._on_change(payload)
            await asyncio.sleep(0.1)
        finally:
            realtime_listener._pending.clear()


def _change(case_id: str, key: str = "record") -> dict:
    return {"data": {key: {"case_id": case_id}}}


@pytest.mark.asyncio
async def test_a_burst_of_changes_to_an_open_case_delivers_one_snapshot() -> None:
    live, viewers, built, build = _open_cases("case-1")

    await _deliver(live, build, *[_change("case-1")] * 5)

    assert built == ["case-1"]
    assert viewers["case-1"].qsize() == 1
    assert viewers["case-1"].get_nowait() == {"type": "snapshot", "case": "case-1"}


@pytest.mark.asyncio
async def test_each_open_case_gets_its_own_snapshot() -> None:
    live, viewers, built, build = _open_cases("case-1", "case-2")

    await _deliver(live, build, _change("case-1"), _change("case-2"), _change("case-1"))

    assert sorted(built) == ["case-1", "case-2"]
    assert {case_id: queue.qsize() for case_id, queue in viewers.items()} == {"case-1": 1, "case-2": 1}


@pytest.mark.asyncio
async def test_a_change_to_a_case_nobody_has_open_builds_nothing() -> None:
    live, viewers, built, build = _open_cases("case-1")

    await _deliver(live, build, _change("case-2"))

    assert built == []
    assert viewers["case-1"].empty()
    assert realtime_listener._pending == {}


@pytest.mark.asyncio
async def test_a_deleted_row_still_refreshes_its_case_from_the_old_record() -> None:
    live, viewers, built, build = _open_cases("case-1")

    await _deliver(live, build, _change("case-1", key="old_record"))

    assert built == ["case-1"]
    assert viewers["case-1"].qsize() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"data": {}}, {}, {"data": {"record": {"id": "doc-1"}}}], ids=[
    "no record", "no data", "a record with no case id",
])
async def test_an_event_that_names_no_case_delivers_nothing(payload) -> None:
    live, viewers, built, build = _open_cases("case-1")

    await _deliver(live, build, payload)

    assert built == []
    assert viewers["case-1"].empty()


@pytest.mark.asyncio
async def test_start_subscribes_to_documents_and_cases_changes() -> None:
    mock_channel = MagicMock()
    mock_channel.subscribe = AsyncMock()
    mock_client = MagicMock()
    mock_client.channel.return_value = mock_channel

    with patch(
        "app.realtime_listener.build_async_supabase_client",
        new=AsyncMock(return_value=mock_client),
    ):
        await realtime_listener.start()

    mock_client.channel.assert_called_once_with("case-status-changes")
    assert mock_channel.on_postgres_changes.call_count == 2
    tables = {call.kwargs["table"] for call in mock_channel.on_postgres_changes.call_args_list}
    assert tables == {"documents", "cases"}
    mock_channel.subscribe.assert_awaited_once()
    assert realtime_listener._client is mock_client
    assert realtime_listener._channel is mock_channel


@pytest.mark.asyncio
async def test_stop_cancels_pending_tasks_and_closes_the_realtime_client() -> None:
    pending_task = MagicMock()
    realtime_listener._pending["case-1"] = pending_task

    mock_client = MagicMock()
    mock_client.realtime.close = AsyncMock()
    realtime_listener._client = mock_client
    realtime_listener._channel = MagicMock()

    await realtime_listener.stop()

    pending_task.cancel.assert_called_once()
    assert realtime_listener._pending == {}
    mock_client.realtime.close.assert_awaited_once()
    assert realtime_listener._client is None
    assert realtime_listener._channel is None


@pytest.mark.asyncio
async def test_stop_is_a_no_op_when_never_started() -> None:
    # _client is None (never started) — should not try to close anything.
    await realtime_listener.stop()

    assert realtime_listener._client is None
    assert realtime_listener._channel is None
