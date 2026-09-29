from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import realtime_listener


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


def test_on_change_ignores_payload_with_no_record() -> None:
    with patch("app.realtime_listener.asyncio.create_task") as mock_create_task:
        realtime_listener._on_change({"data": {}})

        mock_create_task.assert_not_called()


def test_on_change_ignores_record_with_no_case_id() -> None:
    with patch("app.realtime_listener.asyncio.create_task") as mock_create_task:
        realtime_listener._on_change({"data": {"record": {"id": "doc-1"}}})

        mock_create_task.assert_not_called()


def test_on_change_schedules_debounced_publish_for_the_records_case_id() -> None:
    with (
        patch("app.realtime_listener.asyncio.create_task") as mock_create_task,
        patch("app.realtime_listener._debounced_publish", new=MagicMock()),
    ):
        realtime_listener._on_change({"data": {"record": {"case_id": "case-1"}}})

        mock_create_task.assert_called_once()
        assert realtime_listener._pending["case-1"] is mock_create_task.return_value


def test_on_change_falls_back_to_old_record_when_record_is_absent() -> None:
    with (
        patch("app.realtime_listener.asyncio.create_task") as mock_create_task,
        patch("app.realtime_listener._debounced_publish", new=MagicMock()),
    ):
        realtime_listener._on_change({"data": {"old_record": {"case_id": "case-1"}}})

        mock_create_task.assert_called_once()


def test_on_change_cancels_a_still_pending_task_for_the_same_case_before_scheduling_a_new_one() -> None:
    stale_task = MagicMock()
    stale_task.done.return_value = False
    realtime_listener._pending["case-1"] = stale_task

    with (
        patch("app.realtime_listener.asyncio.create_task") as mock_create_task,
        patch("app.realtime_listener._debounced_publish", new=MagicMock()),
    ):
        realtime_listener._on_change({"data": {"record": {"case_id": "case-1"}}})

        stale_task.cancel.assert_called_once()
        assert realtime_listener._pending["case-1"] is mock_create_task.return_value


def test_on_change_does_not_cancel_an_already_done_task() -> None:
    done_task = MagicMock()
    done_task.done.return_value = True
    realtime_listener._pending["case-1"] = done_task

    with (
        patch("app.realtime_listener.asyncio.create_task"),
        patch("app.realtime_listener._debounced_publish", new=MagicMock()),
    ):
        realtime_listener._on_change({"data": {"record": {"case_id": "case-1"}}})

        done_task.cancel.assert_not_called()


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
