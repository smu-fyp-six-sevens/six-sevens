from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import auth
from app.case_broadcaster import broadcaster
from app.main import app
from app.models import CaseRecord, DocumentRecord, DocumentStatus
from app.routers.ws import case_status_socket

client = TestClient(app)


def _make_case(case_id: str = "case-1") -> CaseRecord:
    return CaseRecord(
        id=case_id,
        greenlink_case_id="GLDB-1",
        status="open",
        submission_finalized=False,
        created_at="2026-08-26T00:00:00Z",
        updated_at="2026-08-26T00:00:00Z",
    )


def _make_document(document_id: str = "doc-1") -> DocumentRecord:
    # A fixed past uploaded_at would roll this up to STUCK instead of
    # IN_PROGRESS once enough real time has passed since it was written
    # (see monitoring.is_stuck) — use "now" so the case_service.build_summary
    # computation these tests exercise for real stays IN_PROGRESS.
    return DocumentRecord(
        id=document_id,
        case_id="case-1",
        filename="statement.pdf",
        status=DocumentStatus.PROCESSING,
        uploaded_at=datetime.now(timezone.utc),
    )


def test_rejects_missing_ticket() -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/cases/case-1"):
            pass


def test_rejects_invalid_ticket() -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/cases/case-1?ticket=not-a-real-ticket"):
            pass


def test_ws_ticket_is_single_use() -> None:
    ticket = auth.create_ws_ticket()
    with (
        patch("app.case_service.case_repository") as mock_case_repo,
        patch("app.case_service.document_repository") as mock_doc_repo,
        patch("app.case_service.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_case_repo.get_case.return_value = _make_case("case-1")
        mock_doc_repo.list_documents.return_value = []
        mock_fields_repo.get_extracted_fields_for_documents.return_value = {}

        with client.websocket_connect(f"/ws/cases/case-1?ticket={ticket}") as ws:
            ws.receive_json()

    # Same ticket again — already consumed by the connection above.
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/cases/case-1?ticket={ticket}"):
            pass


def test_ws_ticket_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "WS_TICKET_TTL_SECONDS", -1)
    ticket = auth.create_ws_ticket()

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/cases/case-1?ticket={ticket}"):
            pass


def test_issue_ws_ticket_returns_a_usable_ticket() -> None:
    # conftest's autouse _bypass_auth override means this reaches the route
    # regardless of the Authorization header — see test_auth.py for the
    # real require_auth enforcement test on other routes; nothing route-
    # specific to re-test here beyond "the endpoint hands back a ticket
    # that actually works".
    response = client.post("/ws/tickets")
    assert response.status_code == 200
    ticket = response.json()["ticket"]

    with (
        patch("app.case_service.case_repository") as mock_case_repo,
        patch("app.case_service.document_repository") as mock_doc_repo,
        patch("app.case_service.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_case_repo.get_case.return_value = _make_case("case-1")
        mock_doc_repo.list_documents.return_value = []
        mock_fields_repo.get_extracted_fields_for_documents.return_value = {}

        with client.websocket_connect(f"/ws/cases/case-1?ticket={ticket}") as ws:
            payload = ws.receive_json()

    assert payload["type"] == "snapshot"


def test_pushes_snapshot_on_connect() -> None:
    ticket = auth.create_ws_ticket()
    with (
        patch("app.case_service.case_repository") as mock_case_repo,
        patch("app.case_service.document_repository") as mock_doc_repo,
        patch("app.case_service.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_case_repo.get_case.return_value = _make_case("case-1")
        mock_doc_repo.list_documents.return_value = [_make_document("doc-1")]
        mock_fields_repo.get_extracted_fields_for_documents.return_value = {}

        with client.websocket_connect(f"/ws/cases/case-1?ticket={ticket}") as ws:
            payload = ws.receive_json()

    assert payload["type"] == "snapshot"
    assert payload["case"]["id"] == "case-1"
    # PROCESSING (see _make_document's default) rolls up to IN_PROGRESS —
    # this is real case_service.build_summary logic, not a mock return value.
    assert payload["case"]["documents_status"] == "in_progress"
    assert [doc["id"] for doc in payload["documents"]] == ["doc-1"]
    mock_case_repo.get_case.assert_called_with("case-1")
    mock_doc_repo.list_documents.assert_called_with(case_id="case-1", include_fields=False)


def test_closes_with_error_when_case_not_found() -> None:
    ticket = auth.create_ws_ticket()
    with patch("app.case_service.case_repository") as mock_case_repo:
        mock_case_repo.get_case.return_value = None

        with client.websocket_connect(f"/ws/cases/missing-case?ticket={ticket}") as ws:
            payload = ws.receive_json()
            assert payload == {"type": "error", "detail": "Case not found"}
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()


def test_refresh_frame_triggers_an_immediate_rebuilt_snapshot() -> None:
    """
    caseSocket.ts's refresh() sends a bare text frame right after a
    successful upload, independent of whatever the Realtime-driven
    broadcaster push is doing — useful for the moment right after an
    upload, before the new row's insert has necessarily propagated through
    Realtime yet. Content of the frame is never inspected.
    """
    ticket = auth.create_ws_ticket()
    case = _make_case("case-1")
    existing_document = _make_document("doc-1")
    new_document = _make_document("doc-2")

    with (
        patch("app.case_service.case_repository") as mock_case_repo,
        patch("app.case_service.document_repository") as mock_doc_repo,
        patch("app.case_service.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_case_repo.get_case.return_value = case
        mock_fields_repo.get_extracted_fields_for_documents.return_value = {}
        mock_doc_repo.list_documents.return_value = [existing_document]

        with client.websocket_connect(f"/ws/cases/case-1?ticket={ticket}") as ws:
            first = ws.receive_json()
            assert [doc["id"] for doc in first["documents"]] == ["doc-1"]

            mock_doc_repo.list_documents.return_value = [existing_document, new_document]
            ws.send_text("refresh")

            second = ws.receive_json()

    assert {doc["id"] for doc in second["documents"]} == {"doc-1", "doc-2"}


async def test_socket_pushes_a_broadcaster_message_ahead_of_a_client_disconnect() -> None:
    """
    Drives case_status_socket directly (not via TestClient) so the
    broadcaster.publish() call below runs on the same event loop as the
    socket coroutine's `queue.get()` await — exercising the queue_task
    branch of the FIRST_COMPLETED race (routers/ws.py line ~75), which the
    TestClient-based tests above never hit since nothing in them publishes
    to the broadcaster while a connection is open.
    """
    ticket = auth.create_ws_ticket()
    sent: list[dict] = []
    mock_ws = AsyncMock()
    mock_ws.send_json = AsyncMock(side_effect=lambda payload: sent.append(payload))

    async def fake_receive_text() -> str:
        await asyncio.sleep(0.05)
        raise WebSocketDisconnect()

    mock_ws.receive_text = AsyncMock(side_effect=fake_receive_text)

    with patch(
        "app.routers.ws.case_service.build_case_snapshot",
        return_value={"type": "snapshot", "case": {}, "documents": []},
    ):
        task = asyncio.create_task(case_status_socket(mock_ws, "case-1", ticket))
        # Publish only once the socket is listening, or the message is lost.
        async with asyncio.timeout(2):
            while not broadcaster.has_subscribers("case-1"):
                await asyncio.sleep(0)
        broadcaster.publish("case-1", {"type": "snapshot", "documents": ["pushed"]})
        await task

    assert {"type": "snapshot", "documents": ["pushed"]} in sent
    mock_ws.close.assert_not_awaited()  # WebSocketDisconnect path — nothing left to close


async def test_socket_logs_and_returns_on_unexpected_error() -> None:
    ticket = auth.create_ws_ticket()
    mock_ws = AsyncMock()
    mock_ws.receive_text = AsyncMock(side_effect=RuntimeError("boom"))

    with patch(
        "app.routers.ws.case_service.build_case_snapshot",
        return_value={"type": "snapshot", "case": {}, "documents": []},
    ):
        # Should not raise — the generic `except Exception` handler catches
        # it, logs, and falls through to the trailing close() below.
        await case_status_socket(mock_ws, "case-1", ticket)

    mock_ws.close.assert_awaited_once()


async def test_socket_swallows_runtime_error_from_close_on_already_closed_connection() -> None:
    ticket = auth.create_ws_ticket()
    mock_ws = AsyncMock()
    mock_ws.close = AsyncMock(side_effect=RuntimeError("Cannot call \"send\" once a close message has been sent."))

    with patch("app.routers.ws.case_service.build_case_snapshot", return_value=None):
        # "Case not found" path never enters the while loop, then hits the
        # trailing close() below — which here raises RuntimeError, caught
        # and swallowed rather than propagating out of the socket handler.
        await case_status_socket(mock_ws, "case-1", ticket)

    mock_ws.close.assert_awaited_once()
