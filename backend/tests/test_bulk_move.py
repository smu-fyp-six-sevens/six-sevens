"""
Moving several documents to another case at once (POST /documents/bulk-move, document_service.bulk_move): each
document by the single move's rules, refused ones listed with why, the rest moved in one write, and every affected
case resynced once.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app import case_service
from app.events import types
from app.main import app
from app.models import CaseRecord, CaseStage, DocumentRecord, DocumentStatus

client = TestClient(app)
NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)
TARGET = "case-target"


def document(document_id: str, status: DocumentStatus = DocumentStatus.ANALYZED, case_id: str = "case-1") -> DocumentRecord:
    return DocumentRecord(id=document_id, case_id=case_id, filename=f"{document_id}.pdf", status=status, uploaded_at=NOW)


def case(case_id: str, status: CaseStage = CaseStage.OPEN) -> CaseRecord:
    return CaseRecord(id=case_id, greenlink_case_id=f"GLDB-{case_id}", status=status.value, created_at=NOW, updated_at=NOW)


def bulk_move(documents: list[DocumentRecord], ids: list[str], *, target: CaseRecord | None = None,
              finalised: frozenset[str] = frozenset()):
    """Posts the move; returns the response, what was moved in the one write, and the cases resynced."""
    with (
        patch("app.document_service.case_service.get_case", return_value=case(TARGET) if target is None else target),
        patch("app.document_service.case_service.is_case_finalised", side_effect=lambda case_id: case_id in finalised),
        patch("app.document_service.document_repository.get_documents_by_ids", return_value=documents),
        patch("app.document_service.document_repository.move_documents") as move,
        patch("app.document_service._resync_case", AsyncMock()) as resync,
    ):
        response = client.post("/documents/bulk-move", json={"document_ids": ids, "case_id": TARGET})
    moved = move.call_args.args if move.called else None
    return response, moved, sorted(call.args[0] for call in resync.await_args_list)


def test_eligible_documents_move_in_one_write_and_every_affected_case_is_resynced_once(published_events) -> None:
    documents = [document("doc-1"), document("doc-2"), document("doc-3", case_id="case-2")]

    response, moved, resynced = bulk_move(documents, ["doc-1", "doc-2", "doc-3"])

    assert response.status_code == 200
    assert response.json() == {"moved": ["doc-1", "doc-2", "doc-3"], "failed": []}
    assert moved == (["doc-1", "doc-2", "doc-3"], TARGET)
    assert resynced == sorted(["case-1", "case-2", TARGET])
    audits = [e for e in published_events if e.type == types.DOCUMENT_MOVED]
    assert [(e.document_id, e.detail["from_case_id"], e.detail["to_case_id"]) for e in audits] == [
        ("doc-1", "case-1", TARGET), ("doc-2", "case-1", TARGET), ("doc-3", "case-2", TARGET),
    ]


@pytest.mark.parametrize("status, reason", [
    (DocumentStatus.APPROVED, "This document has been approved, so it can't be moved to another case."),
    (DocumentStatus.REJECTED, "This document has been rejected, so it can't be moved to another case."),
    (DocumentStatus.EXTRACTING, "Document is still being processed."),
])
def test_a_locked_or_in_flight_document_is_refused_and_the_rest_still_move(status, reason) -> None:
    response, moved, _ = bulk_move([document("doc-1", status), document("doc-2")], ["doc-1", "doc-2"])

    assert response.json()["moved"] == ["doc-2"]
    assert response.json()["failed"] == [{"document_id": "doc-1", "reason": reason}]
    assert moved == (["doc-2"], TARGET)


def test_documents_already_in_the_case_from_a_finalised_case_or_unknown_are_refused(published_events) -> None:
    documents = [document("here", case_id=TARGET), document("locked-case", case_id="case-done")]

    response, moved, resynced = bulk_move(documents, ["here", "locked-case", "missing"], finalised=frozenset({"case-done"}))

    assert response.json()["moved"] == []
    assert {f["document_id"]: f["reason"] for f in response.json()["failed"]} == {
        "here": "Document is already in that case.",
        "locked-case": case_service.FINALISED_DETAIL,
        "missing": "Document not found",
    }
    # Nothing moved: no write, no resync, nothing in the audit trail.
    assert (moved, resynced) == (None, [])
    assert [e for e in published_events if e.type == types.DOCUMENT_MOVED] == []


def test_nothing_moves_into_a_finalised_case() -> None:
    response, moved, _ = bulk_move([document("doc-1")], ["doc-1"], target=case(TARGET, CaseStage.FINALISED))

    assert response.status_code == 409
    assert response.json()["detail"] == case_service.FINALISED_DETAIL
    assert moved is None


def test_moving_to_an_unknown_case_is_not_found() -> None:
    with patch("app.document_service.case_service.get_case", return_value=None):
        response = client.post("/documents/bulk-move", json={"document_ids": ["doc-1"], "case_id": "no-such-case"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Target case not found"


def test_an_empty_list_is_refused() -> None:
    response = client.post("/documents/bulk-move", json={"document_ids": [], "case_id": TARGET})

    assert response.status_code == 400
    assert response.json()["detail"] == "No documents specified."
