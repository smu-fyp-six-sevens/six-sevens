from __future__ import annotations

import asyncio
import logging

from . import query_stats
from .case_broadcaster import broadcaster

logger = logging.getLogger(__name__)


async def push_case_snapshot(case_id: str, *, realtime_covers: bool = False) -> None:
    """
    Best-effort live refresh for a case's WebSocket viewers in this process. Never raises. realtime_covers: the
    change is a documents, cases or case_analysis_runs write, which this process's Realtime listener (when it runs
    here, as locally) already turns into a build, so this one would be a repeat.
    """
    from . import case_service, realtime_listener

    if not broadcaster.has_subscribers(case_id):
        return
    if realtime_covers and realtime_listener.listening():
        return
    try:
        with query_stats.scope("pushed snapshot"):
            snapshot = await asyncio.to_thread(case_service.build_case_snapshot, case_id)
    except Exception:
        logger.exception("Failed to build case snapshot for case %s", case_id)
        return
    if snapshot is not None:
        broadcaster.publish(case_id, snapshot)
