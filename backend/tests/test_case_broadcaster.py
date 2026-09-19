from __future__ import annotations

import asyncio

import pytest

from app.case_broadcaster import CaseBroadcaster


def test_subscribe_returns_a_queue_registered_for_the_case() -> None:
    broadcaster = CaseBroadcaster()

    queue = broadcaster.subscribe("case-1")

    assert isinstance(queue, asyncio.Queue)
    assert queue in broadcaster._subscribers["case-1"]


def test_unsubscribe_removes_the_queue_and_cleans_up_empty_case_entry() -> None:
    broadcaster = CaseBroadcaster()
    queue = broadcaster.subscribe("case-1")

    broadcaster.unsubscribe("case-1", queue)

    assert "case-1" not in broadcaster._subscribers


def test_unsubscribe_for_unknown_case_id_is_a_no_op() -> None:
    broadcaster = CaseBroadcaster()

    # Should not raise even though "case-1" was never subscribed to.
    broadcaster.unsubscribe("case-1", asyncio.Queue())


def test_unsubscribe_leaves_other_subscribers_for_the_same_case_intact() -> None:
    broadcaster = CaseBroadcaster()
    queue_a = broadcaster.subscribe("case-1")
    queue_b = broadcaster.subscribe("case-1")

    broadcaster.unsubscribe("case-1", queue_a)

    assert "case-1" in broadcaster._subscribers
    assert broadcaster._subscribers["case-1"] == {queue_b}


@pytest.mark.asyncio
async def test_publish_delivers_the_snapshot_to_every_subscriber_of_that_case() -> None:
    broadcaster = CaseBroadcaster()
    queue_a = broadcaster.subscribe("case-1")
    queue_b = broadcaster.subscribe("case-1")
    other_case_queue = broadcaster.subscribe("case-2")

    broadcaster.publish("case-1", {"type": "snapshot"})

    assert queue_a.get_nowait() == {"type": "snapshot"}
    assert queue_b.get_nowait() == {"type": "snapshot"}
    assert other_case_queue.empty()


def test_publish_with_no_subscribers_does_not_raise() -> None:
    broadcaster = CaseBroadcaster()

    broadcaster.publish("case-with-nobody-listening", {"type": "snapshot"})
