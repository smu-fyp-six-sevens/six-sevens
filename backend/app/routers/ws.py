from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import case_repository, case_status, document_repository, extracted_fields_repository
from ..auth import verify_token
from ..models import CaseDocumentsStatus, DocumentRecord

router = APIRouter(tags=["ws"])
logger = logging.getLogger(__name__)

# Matches the frontend's former client-side poll interval (see
# POLL_INTERVAL_MS in UploadPage.tsx) — used while a case actually has
# something moving. IDLE_POLL_INTERVAL_SECONDS kicks in once a case has
# nothing in flight (see _poll_interval_for) so a tab left open on a
# long-finished case doesn't keep hitting Supabase every 2 seconds
# indefinitely. A fresh upload into an idle case is still picked up
# promptly despite the slower tick — see caseSocket.ts's refresh(), which
# sends a text frame over the socket right after a successful upload; any
# text arriving here (its content is never inspected) makes the
# in-progress wait_for below return immediately instead of waiting out the
# rest of the interval, so the next snapshot goes out right away.
ACTIVE_POLL_INTERVAL_SECONDS = 2
IDLE_POLL_INTERVAL_SECONDS = 30
_IDLE_STATUSES = {CaseDocumentsStatus.ANALYZED, CaseDocumentsStatus.NO_DOCUMENTS}


def _poll_interval_for(documents_status: CaseDocumentsStatus) -> int:
    """
    STUCK/FAILED stay on the fast interval since those are exactly the
    statuses a loan officer is likely actively watching (about to retry a
    document); IN_PROGRESS obviously stays fast too. Only ANALYZED (fully
    done) and NO_DOCUMENTS (nothing uploaded yet) count as idle.
    """
    return (
        IDLE_POLL_INTERVAL_SECONDS
        if documents_status in _IDLE_STATUSES
        else ACTIVE_POLL_INTERVAL_SECONDS
    )


def _enrich_with_extracted_fields(documents: list[DocumentRecord]) -> list[DocumentRecord]:
    fields_by_document = extracted_fields_repository.get_extracted_fields_for_documents(
        [document.id for document in documents]
    )
    for document in documents:
        fields = fields_by_document.get(document.id)
        if fields:
            document.extracted_fields = {
                field.field_name: {"value": field.field_value, "confidence": field.confidence}
                for field in fields
            }
    return documents


def _build_snapshot(case_id: str) -> tuple[dict, CaseDocumentsStatus] | None:
    """
    Runs on a worker thread (supabase-py is sync) — mirrors what the
    frontend used to assemble itself from GET /cases/{id} +
    GET /documents?case_id=... + GET /documents/{id} per document.

    Fetches the case and its documents directly (rather than calling
    case_status.get_case_summary) and passes them into
    case_status.build_summary itself, since the documents are also needed
    here for extracted-fields enrichment — going through get_case_summary
    would fetch the same document list a second time on every tick.
    """
    case = case_repository.get_case(case_id)
    if case is None:
        return None

    documents = document_repository.list_documents(case_id=case_id)
    summary = case_status.build_summary(case, documents)
    enriched = _enrich_with_extracted_fields(documents)
    payload = {
        "type": "snapshot",
        "case": summary.model_dump(mode="json"),
        "documents": [document.model_dump(mode="json") for document in enriched],
    }
    return payload, summary.documents_status


@router.websocket("/ws/cases/{case_id}")
async def case_status_socket(websocket: WebSocket, case_id: str, token: str = "") -> None:
    """
    Push-based replacement for the client-side poll loop that used to live
    in UploadPage.tsx. One connection covers a whole case — new documents
    (from a later upload) and every existing document's status both ride
    the same stream, instead of each document polling independently.

    Auth can't reuse require_auth's Authorization-header check: a browser's
    native WebSocket API has no way to set custom headers on the handshake,
    so the token travels as a query param and is checked before accept()
    (rejecting here closes the handshake instead of the connection, which
    is the standard pattern for WS auth failures).
    """
    if not verify_token(token):
        await websocket.close(code=1008)
        return

    await websocket.accept()

    last_payload: str | None = None
    try:
        while True:
            result = await asyncio.to_thread(_build_snapshot, case_id)
            if result is None:
                await websocket.send_json({"type": "error", "detail": "Case not found"})
                break

            payload, documents_status = result

            # Most ticks see no change while a document sits mid-pipeline —
            # skip sending (and skip the frontend re-render) when nothing
            # actually moved since the last snapshot.
            serialized = json.dumps(payload, sort_keys=True)
            if serialized != last_payload:
                await websocket.send_json(payload)
                last_payload = serialized

            # Waiting on receive_text() (rather than plain asyncio.sleep) is
            # what actually notices the client going away: receive_text
            # raises WebSocketDisconnect on a disconnect event, whereas a
            # bare sleep loop that only ever calls send() never consumes
            # that event, so a dropped client would leave this loop (and
            # the Cloud Run instance holding it) running indefinitely. It
            # also doubles as the wake-up channel described above — any
            # text arriving before the timeout (a disconnect or a refresh
            # ping) ends the wait early and the loop polls again right away.
            try:
                await asyncio.wait_for(
                    websocket.receive_text(), timeout=_poll_interval_for(documents_status)
                )
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        return
    except Exception:
        logger.exception("Case status socket failed for case %s", case_id)

    # Reached on a normal break (e.g. "Case not found") or an unexpected
    # error rather than a client-initiated disconnect — returning without
    # this would leave the handshake open with no close frame ever sent,
    # so the client's socket just hangs instead of seeing the connection
    # end. Already-closed (e.g. the send() that triggered the except
    # Exception branch above) is fine to no-op past.
    try:
        await websocket.close()
    except RuntimeError:
        pass
