"""The live push of a case snapshot (snapshots.push_case_snapshot): best effort, only when someone is watching."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app import snapshots


async def _push(*, watching: bool = True, listening: bool = False, build=None, **kwargs):
    broadcaster = MagicMock()
    broadcaster.has_subscribers.return_value = watching
    build = build or MagicMock(return_value={"type": "snapshot"})
    with (
        patch.object(snapshots, "broadcaster", broadcaster),
        patch("app.case_service.build_case_snapshot", build),
        patch("app.realtime_listener.listening", return_value=listening),
    ):
        await snapshots.push_case_snapshot("case-1", **kwargs)
    return broadcaster, build


async def test_a_snapshot_is_built_and_published_to_the_cases_viewers() -> None:
    broadcaster, build = await _push()

    build.assert_called_once_with("case-1")
    broadcaster.publish.assert_called_once_with("case-1", {"type": "snapshot"})


async def test_nothing_is_built_when_nobody_is_watching() -> None:
    broadcaster, build = await _push(watching=False)

    build.assert_not_called()
    broadcaster.publish.assert_not_called()


async def test_a_change_the_realtime_listener_already_covers_is_not_built_twice() -> None:
    _, build = await _push(listening=True, realtime_covers=True)
    build.assert_not_called()


async def test_a_change_realtime_does_not_see_is_built_even_with_a_listener() -> None:
    _, build = await _push(listening=True)
    build.assert_called_once()


async def test_a_change_realtime_covers_is_built_when_no_listener_runs_here() -> None:
    _, build = await _push(listening=False, realtime_covers=True)
    build.assert_called_once()


async def test_a_failed_build_is_logged_and_never_raised() -> None:
    broadcaster, _ = await _push(build=MagicMock(side_effect=RuntimeError("database down")))

    broadcaster.publish.assert_not_called()


async def test_a_case_that_is_gone_publishes_nothing() -> None:
    broadcaster, _ = await _push(build=MagicMock(return_value=None))

    broadcaster.publish.assert_not_called()
