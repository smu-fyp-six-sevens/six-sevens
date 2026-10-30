"""
Regression tests (SCRUM-296) for officer notes — "Test Cases - Sprint
4.xlsx", Case Management sheet, Attach officer notes (SCRUM-81):
CM-029 – CM-036.

Backend half: the real notes endpoints (routers/notes.py) over an in-memory
case_notes table, for what only a stored note shows: a note saved through the
API is the one read back on reload (as on case GLDB-130 in the live run), and a
note on an approved document can be added to while the document is only ever
read. The rules for a single request (signed by the officer, a document from
another case refused, ...) are test_notes.py's, tagged sprint4 per test in
conftest.py. The UI half is in frontend/e2e/specs/notes.spec.ts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import CaseNote, CaseRecord, DocumentRecord, DocumentStatus
from tests.conftest import TEST_USER

client = TestClient(app)

CASE_ID = "13013013-0000-0000-0000-000000000130"  # GLDB-130
PAYSLIP_ID = "13013013-0000-0000-0000-000000000001"
_CREATED = datetime(2026, 10, 21, tzinfo=timezone.utc)


class NotesTable:
    """Stands in for notes_repository: the case_notes table, in memory."""

    def __init__(self) -> None:
        self.rows: list[CaseNote] = []
        self.clock = datetime(2026, 10, 22, 2, 0, tzinfo=timezone.utc)

    def _tick(self) -> datetime:
        self.clock += timedelta(minutes=1)
        return self.clock

    def add_note(self, *, case_id, author, body, document_id=None, parent_id=None) -> CaseNote:
        note = CaseNote(id=f"{len(self.rows) + 1:08d}-0000-0000-0000-000000000000", case_id=case_id,
                        document_id=document_id, parent_id=parent_id, author=author, body=body,
                        created_at=self._tick())
        self.rows.append(note)
        return note

    def list_notes(self, case_id) -> list[CaseNote]:
        return sorted((n for n in self.rows if n.case_id == case_id), key=lambda n: n.created_at)

    def get_note(self, note_id) -> CaseNote | None:
        return next((n for n in self.rows if n.id == note_id), None)

    def update_note(self, note_id, body) -> CaseNote:
        note = self.get_note(note_id)
        assert note is not None
        updated = note.model_copy(update={"body": body, "edited_at": self._tick()})
        self.rows[self.rows.index(note)] = updated
        return updated


@pytest.fixture
def payslip() -> DocumentRecord:
    return DocumentRecord(id=PAYSLIP_ID, case_id=CASE_ID, filename="GLDB-130_Payslip.pdf", document_type="payslip",
                          status=DocumentStatus.ANALYZED, uploaded_at=_CREATED)


@pytest.fixture
def document_repository(payslip):
    """The case's one document (the payslip), read-only: a test checks nothing ever writes to it."""
    with patch("app.routers.notes.document_repository") as documents:
        documents.get_document.side_effect = lambda document_id, **_: payslip if document_id == PAYSLIP_ID else None
        yield documents


@pytest.fixture
def notes(document_repository):
    table = NotesTable()
    with (
        patch("app.routers._dependencies.case_service") as case_service,
        patch("app.routers.notes.notes_repository", table),
    ):
        case_service.get_case.side_effect = lambda case_id: (
            CaseRecord(id=CASE_ID, greenlink_case_id="GLDB-130", created_at=_CREATED, updated_at=_CREATED)
            if case_id == CASE_ID else None
        )
        yield table


def add_note(body: str, document_id: str | None = None):
    return client.post(f"/cases/{CASE_ID}/notes", json={"body": body, "document_id": document_id})


def add_note_reply(parent_id: str, body: str):
    return client.post(f"/cases/{CASE_ID}/notes", json={"body": body, "parent_id": parent_id})


def reload() -> list[dict]:
    response = client.get(f"/cases/{CASE_ID}/notes")
    assert response.status_code == 200
    return response.json()


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-81", "CM-029 – CM-035")
def test_case_and_document_notes_are_read_back_unchanged_on_reload(notes):
    case_note = add_note("Directors match the incumbency certificate.")
    document_note = add_note("Net pay matches the salary credits.", PAYSLIP_ID)
    assert (case_note.status_code, document_note.status_code) == (201, 201)

    reloaded = reload()

    # Exactly what was saved, oldest first: each signed by the officer, timestamped, and on its own document.
    assert reloaded == [case_note.json(), document_note.json()]
    assert [(n["author"], n["document_id"]) for n in reloaded] == [(TEST_USER, None), (TEST_USER, PAYSLIP_ID)]
    assert [datetime.fromisoformat(n["created_at"]) for n in reloaded] == [n.created_at for n in notes.rows]


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-81", "CM-036")
def test_a_note_on_an_approved_document_can_be_added_to_and_the_document_stays_locked(
    notes, payslip, document_repository
):
    payslip.status = DocumentStatus.APPROVED
    note = add_note("Approved: figures reconcile.", PAYSLIP_ID).json()

    edited = client.patch(f"/cases/{CASE_ID}/notes/{note['id']}",
                          json={"body": "Approved: figures reconcile. Bank confirmed the employer by phone."})
    reply = add_note_reply(note["id"], "Second officer: agreed.")

    assert edited.status_code == 200
    assert edited.json()["body"] == "Approved: figures reconcile. Bank confirmed the employer by phone."
    assert edited.json()["edited_at"]
    assert reply.status_code == 201 and reply.json()["document_id"] == PAYSLIP_ID
    assert [n["body"] for n in reload()] == [
        "Approved: figures reconcile. Bank confirmed the employer by phone.", "Second officer: agreed.",
    ]
    # Notes only ever read the document: it is still approved, and nothing wrote to it.
    assert payslip.status == DocumentStatus.APPROVED
    assert {c[0] for c in document_repository.method_calls} == {"get_document"}
