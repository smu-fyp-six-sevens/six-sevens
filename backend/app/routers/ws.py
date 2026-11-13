from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from .. import case_service, case_stage, query_stats
from ..auth import consume_ws_ticket, create_ws_ticket, require_auth
from ..case_broadcaster import broadcaster

router = APIRouter(tags=["ws"])
logger = logging.getLogger(__name__)
_REFRESH_INTERVAL_SECONDS = 1.0


@router.post("/ws/tickets")
async def issue_ws_ticket(case_id: str, user: str = Depends(require_auth)) -> dict:
    """
    Mints a short-lived, single-use ticket for the case status WebSocket
    below. Sits behind require_auth like every other protected endpoint
    (this router is registered outside main.py's `_protected` group only
    because the WebSocket route itself can't use header-based auth — this
    plain HTTP route has no such restriction). See auth.py's
    WS_TICKET_TTL_SECONDS docstring for why this exists instead of just
    putting the normal bearer token in the WS URL. The ticket opens only `case_id`.
    """
    return {"ticket": create_ws_ticket(user, case_id)}


@router.websocket("/ws/cases/{case_id}")
async def case_status_socket(websocket: WebSocket, case_id: str, ticket: str = "") -> None:
    """
    Push-based case status stream: one connection per case, fed by
    realtime_listener.py's Supabase Realtime subscription via
    case_broadcaster.py rather than polling Supabase itself.

    Auth can't reuse require_auth's Authorization-header check: a browser's
    native WebSocket API has no way to set custom headers on the handshake,
    so a short-lived ticket (minted via POST /ws/tickets) travels as a query
    param instead, checked before accept() (rejecting here closes the
    handshake instead of the connection, the standard pattern for WS auth
    failures).
    """
    user = consume_ws_ticket(ticket, case_id)
    if user is None:
        await websocket.close(code=1008)
        return
    logger.info("Case socket opened for case %s by %s", case_id, user)

    await websocket.accept()
    query_stats.count_event("ws.connect")

    queue = broadcaster.subscribe(case_id)
    disconnected = False
    try:
        # Sent unconditionally on every accept() — first connect and every
        # reconnect alike, with no special-casing — because this is the fix
        # for the missed-event race condition, not an optimization to skip:
        # a connection that just (re)opened has missed whatever Realtime
        # events happened while it was down, and this closes that gap.
        with query_stats.scope("ws connect snapshot"):
            # Opening a case brings its stage up to date first, so the snapshot never shows a stale one.
            try:
                await asyncio.to_thread(case_stage.sync_case_stage, case_id)
            except Exception:
                logger.exception("Failed to bring the stage of case %s up to date", case_id)
            initial = await asyncio.to_thread(case_service.build_case_snapshot, case_id)
        if initial is None:
            await websocket.send_json({"type": "error", "detail": "Case not found"})
        else:
            await websocket.send_json(initial)

            last_refresh = time.monotonic()
            while True:
                recv_task = asyncio.create_task(websocket.receive_text())
                queue_task = asyncio.create_task(queue.get())
                done, pending = await asyncio.wait(
                    {recv_task, queue_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()

                if queue_task in done:
                    await websocket.send_json(queue_task.result())
                    query_stats.count_event("ws.push")

                if recv_task in done:
                    # Raises WebSocketDisconnect naturally on a client
                    # disconnect, caught below — this is also what actually
                    # notices the client going away, since nothing else
                    # here ever reads from the socket.
                    recv_task.result()
                    # Any other text frame is caseSocket.ts's refresh()
                    # nudge (content is never inspected) — fetch and push a
                    # snapshot immediately rather than waiting for the next
                    # Realtime event, useful right after an upload before
                    # the new row's insert has necessarily propagated
                    # through Realtime yet.
                    # At most one a second per connection: each is several queries.
                    wait = _REFRESH_INTERVAL_SECONDS - (time.monotonic() - last_refresh)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    last_refresh = time.monotonic()
                    with query_stats.scope("ws refresh snapshot"):
                        snapshot = await asyncio.to_thread(
                            case_service.build_case_snapshot, case_id
                        )
                    if snapshot is not None:
                        await websocket.send_json(snapshot)
    except WebSocketDisconnect:
        disconnected = True
    except Exception:
        logger.exception("Case status socket failed for case %s", case_id)
    finally:
        broadcaster.unsubscribe(case_id, queue)

    if disconnected:
        # The client already closed its end — nothing left to do.
        return

    # Reached on "Case not found" or an unexpected error rather than a
    # client-initiated disconnect — returning without this would leave the
    # handshake open with no close frame ever sent, so the client's socket
    # just hangs instead of seeing the connection end.
    try:
        await websocket.close()
    except RuntimeError:
        pass
