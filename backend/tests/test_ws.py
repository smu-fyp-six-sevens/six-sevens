from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import auth
from app.main import app
from app.models import CaseRecord, DocumentRecord, DocumentStatus

client = TestClient(app)


def _make_case(case_id: str = "case-1") -> CaseRecord:
    return CaseRecord(
        id=case_id,
        greenlink_case_id="GLDB-1",
        status="open",
        submission_finalized=False,
        recommended_additional_documents=[],
        created_at="2026-08-26T00:00:00Z",
        updated_at="2026-08-26T00:00:00Z",
    )


def _make_document(document_id: str = "doc-1") -> DocumentRecord:
    # A fixed past uploaded_at would roll this up to STUCK instead of
    # IN_PROGRESS once enough real time has passed since it was written
    # (see monitoring.is_stuck) — use "now" so the case_status.build_summary
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
        patch("app.case_status.case_repository") as mock_case_repo,
        patch("app.case_status.document_repository") as mock_doc_repo,
        patch("app.case_status.extracted_fields_repository") as mock_fields_repo,
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
        patch("app.case_status.case_repository") as mock_case_repo,
        patch("app.case_status.document_repository") as mock_doc_repo,
        patch("app.case_status.extracted_fields_repository") as mock_fields_repo,
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
        patch("app.case_status.case_repository") as mock_case_repo,
        patch("app.case_status.document_repository") as mock_doc_repo,
        patch("app.case_status.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_case_repo.get_case.return_value = _make_case("case-1")
        mock_doc_repo.list_documents.return_value = [_make_document("doc-1")]
        mock_fields_repo.get_extracted_fields_for_documents.return_value = {}

        with client.websocket_connect(f"/ws/cases/case-1?ticket={ticket}") as ws:
            payload = ws.receive_json()

    assert payload["type"] == "snapshot"
    assert payload["case"]["id"] == "case-1"
    # PROCESSING (see _make_document's default) rolls up to IN_PROGRESS —
    # this is real case_status.build_summary logic, not a mock return value.
    assert payload["case"]["documents_status"] == "in_progress"
    assert [doc["id"] for doc in payload["documents"]] == ["doc-1"]
    mock_case_repo.get_case.assert_called_with("case-1")
    mock_doc_repo.list_documents.assert_called_with(case_id="case-1")


def test_closes_with_error_when_case_not_found() -> None:
    ticket = auth.create_ws_ticket()
    with patch("app.case_status.case_repository") as mock_case_repo:
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
        patch("app.case_status.case_repository") as mock_case_repo,
        patch("app.case_status.document_repository") as mock_doc_repo,
        patch("app.case_status.extracted_fields_repository") as mock_fields_repo,
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
