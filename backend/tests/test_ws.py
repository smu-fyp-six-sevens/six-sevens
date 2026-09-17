from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import auth
from app.main import app
from app.models import CaseDocumentsStatus, CaseRecord, DocumentRecord, DocumentStatus
from app.routers.ws import _poll_interval_for, ACTIVE_POLL_INTERVAL_SECONDS, IDLE_POLL_INTERVAL_SECONDS

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


def test_rejects_missing_token() -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/cases/case-1"):
            pass


def test_rejects_invalid_token() -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/cases/case-1?token=not-a-real-token"):
            pass


def test_pushes_snapshot_on_connect() -> None:
    token = auth.create_token()
    with (
        patch("app.routers.ws.case_repository") as mock_case_repo,
        patch("app.routers.ws.document_repository") as mock_doc_repo,
        patch("app.routers.ws.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_case_repo.get_case.return_value = _make_case("case-1")
        mock_doc_repo.list_documents.return_value = [_make_document("doc-1")]
        mock_fields_repo.get_extracted_fields_for_documents.return_value = {}

        with client.websocket_connect(f"/ws/cases/case-1?token={token}") as ws:
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
    token = auth.create_token()
    with patch("app.routers.ws.case_repository") as mock_case_repo:
        mock_case_repo.get_case.return_value = None

        with client.websocket_connect(f"/ws/cases/missing-case?token={token}") as ws:
            payload = ws.receive_json()
            assert payload == {"type": "error", "detail": "Case not found"}
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()


def test_poll_interval_is_active_for_statuses_needing_attention() -> None:
    for status in (
        CaseDocumentsStatus.IN_PROGRESS,
        CaseDocumentsStatus.STUCK,
        CaseDocumentsStatus.FAILED,
    ):
        assert _poll_interval_for(status) == ACTIVE_POLL_INTERVAL_SECONDS


def test_poll_interval_is_idle_once_nothing_is_in_flight() -> None:
    for status in (CaseDocumentsStatus.ANALYZED, CaseDocumentsStatus.NO_DOCUMENTS):
        assert _poll_interval_for(status) == IDLE_POLL_INTERVAL_SECONDS


def test_client_message_wakes_idle_poll_immediately() -> None:
    """
    Once a case is idle (ANALYZED here) the loop's wait_for uses the slow
    30s IDLE_POLL_INTERVAL_SECONDS — without the wake-up path, a document
    landing during that wait wouldn't show up until that full interval
    elapsed. caseSocket.ts's refresh() sends a text frame right after a
    successful upload specifically to avoid that; this asserts the second
    snapshot arrives promptly (well under 30s) once that frame arrives,
    rather than only after the idle interval times out.
    """
    token = auth.create_token()
    case = _make_case("case-1")
    analyzed_document = _make_document("doc-1")
    analyzed_document.status = DocumentStatus.ANALYZED
    new_document = _make_document("doc-2")

    with (
        patch("app.routers.ws.case_repository") as mock_case_repo,
        patch("app.routers.ws.document_repository") as mock_doc_repo,
        patch("app.routers.ws.extracted_fields_repository") as mock_fields_repo,
    ):
        mock_case_repo.get_case.return_value = case
        mock_fields_repo.get_extracted_fields_for_documents.return_value = {}
        mock_doc_repo.list_documents.return_value = [analyzed_document]

        with client.websocket_connect(f"/ws/cases/case-1?token={token}") as ws:
            first = ws.receive_json()
            assert first["case"]["documents_status"] == "analyzed"  # idle -> slow interval

            # A new document lands (e.g. a fresh upload) while the socket is
            # sitting on that slow interval.
            mock_doc_repo.list_documents.return_value = [analyzed_document, new_document]
            ws.send_text("refresh")

            second = ws.receive_json()

    assert {doc["id"] for doc in second["documents"]} == {"doc-1", "doc-2"}
