"""
Regression tests (SCRUM-296) for officer notes — "Test Cases - Sprint
4.xlsx", Case Management sheet, Attach officer notes (SCRUM-81):
CM-029 – CM-036.

Backend half: the real notes endpoints (routers/notes.py) over an in-memory
case_notes table, so a note saved through the API is the one read back on
reload, as on case GLDB-130 in the live run. The UI half (the notes section,
who/when/which document, reload) is in frontend/e2e/specs/notes.spec.ts.
Existing tests in test_notes.py that cover these cases are tagged sprint4
centrally in conftest.py.
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
@pytest.mark.story("SCRUM-81", "CM-029")
def test_a_note_can_be_added_to_a_case(notes):
    response = add_note("Directors match the incumbency certificate.")

    assert response.status_code == 201
    assert [n["body"] for n in reload()] == ["Directors match the incumbency certificate."]


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-81", "CM-030")
def test_a_note_can_be_added_to_a_single_document(notes):
    response = add_note("Net pay matches the salary credits.", PAYSLIP_ID)

    assert response.status_code == 201
    assert response.json()["document_id"] == PAYSLIP_ID
    [saved] = reload()
    assert saved["document_id"] == PAYSLIP_ID


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-81", "CM-030")
def test_a_note_cannot_be_added_to_a_document_that_is_not_in_the_case(notes):
    response = add_note("Wrong document.", "99999999-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert notes.rows == []


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-81", "CM-031")
def test_a_saved_case_note_shows_its_author_and_when_it_was_saved(notes):
    body = add_note("Called the employer to confirm.").json()

    assert body["author"] == TEST_USER
    assert datetime.fromisoformat(body["created_at"]) == notes.clock


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-81", "CM-032")
def test_a_saved_document_note_shows_its_author_time_and_document(notes):
    body = add_note("CPF figure checked against the CPF statement.", PAYSLIP_ID).json()

    assert (body["author"], body["document_id"]) == (TEST_USER, PAYSLIP_ID)
    assert body["created_at"]


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-81", "CM-033")
def test_a_case_level_note_has_no_document(notes):
    body = add_note("Case-wide: all documents received.").json()

    assert body["document_id"] is None
    assert (body["author"], bool(body["created_at"])) == (TEST_USER, True)


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-81", "CM-034")
def test_a_saved_case_note_is_unchanged_after_reloading_the_case(notes):
    saved = add_note("Directors match the incumbency certificate.").json()

    assert reload() == [saved]


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-81", "CM-035")
def test_a_saved_document_note_is_unchanged_and_still_on_its_document_after_reloading(notes):
    case_note = add_note("Case-wide note.").json()
    document_note = add_note("Net pay matches the salary credits.", PAYSLIP_ID).json()

    reloaded = reload()

    assert reloaded == [case_note, document_note]
    assert [n["document_id"] for n in reloaded] == [None, PAYSLIP_ID]


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
