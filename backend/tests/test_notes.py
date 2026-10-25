from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import CaseNote, CaseRecord, DocumentRecord, DocumentStatus
from tests.conftest import TEST_USER

client = TestClient(app)

_TIMESTAMP = datetime.fromisoformat("2026-10-21T02:00:00+00:00")
CASE_ID = "11111111-1111-1111-1111-111111111111"
OTHER_CASE_ID = "22222222-2222-2222-2222-222222222222"
DOCUMENT_ID = "33333333-3333-3333-3333-333333333333"
NOTE_ID = "44444444-4444-4444-4444-444444444444"
REPLY_ID = "55555555-5555-5555-5555-555555555555"


def _case(case_id: str = CASE_ID) -> CaseRecord:
    return CaseRecord(id=case_id, greenlink_case_id="GLDB-1", created_at=_TIMESTAMP, updated_at=_TIMESTAMP)


def _document(status: DocumentStatus = DocumentStatus.APPROVED, case_id: str = CASE_ID) -> DocumentRecord:
    return DocumentRecord(
        id=DOCUMENT_ID, case_id=case_id, filename="CBR.pdf", storage_path="x/CBR.pdf",
        status=status, uploaded_at=_TIMESTAMP,
    )


def _note(**overrides) -> CaseNote:
    return CaseNote(**{
        "id": NOTE_ID, "case_id": CASE_ID, "document_id": None, "parent_id": None,
        "author": TEST_USER, "body": "Checked the CBR against the bank statement.", "created_at": _TIMESTAMP,
        **overrides,
    })


@pytest.fixture
def repos():
    """The case exists; notes and documents are faked so nothing reaches Supabase."""
    with (
        patch("app.routers._dependencies.case_service") as case_service,
        patch("app.routers.notes.notes_repository") as notes_repo,
        patch("app.routers.notes.document_repository") as document_repo,
    ):
        case_service.get_case.side_effect = lambda case_id: _case(case_id) if case_id in (CASE_ID, OTHER_CASE_ID) else None
        notes_repo.add_note.side_effect = lambda **kwargs: _note(
            id=REPLY_ID if kwargs["parent_id"] else NOTE_ID, **{k: v for k, v in kwargs.items()}
        )
        yield notes_repo, document_repo


def test_note_is_signed_by_the_officer_and_timestamped(repos, published_events) -> None:
    notes_repo, _ = repos
    response = client.post(f"/cases/{CASE_ID}/notes", json={"body": "  Looks consistent.  ", "author": "someone-else"})

    assert response.status_code == 201
    body = response.json()
    # The author always comes from the token, never the request.
    assert body["author"] == TEST_USER
    assert body["body"] == "Looks consistent."
    assert body["created_at"]
    assert body["document_id"] is None
    notes_repo.add_note.assert_called_once_with(
        case_id=CASE_ID, author=TEST_USER, body="Looks consistent.", document_id=None, parent_id=None,
    )
    assert published_events[-1].type == "note.added"
    assert published_events[-1].actor == TEST_USER


def test_note_on_an_approved_document_is_saved_and_leaves_the_document_alone(repos) -> None:
    notes_repo, document_repo = repos
    document_repo.get_document.return_value = _document(DocumentStatus.APPROVED)

    response = client.post(f"/cases/{CASE_ID}/notes", json={"body": "Enquiry date looks moved.", "document_id": DOCUMENT_ID})

    assert response.status_code == 201
    assert response.json()["document_id"] == DOCUMENT_ID
    # Only read: the document's status and fields are never written by a note.
    assert [c[0] for c in document_repo.method_calls] == ["get_document"]


def test_note_on_a_document_from_another_case_is_refused(repos) -> None:
    notes_repo, document_repo = repos
    document_repo.get_document.return_value = _document(case_id=OTHER_CASE_ID)

    response = client.post(f"/cases/{CASE_ID}/notes", json={"body": "x", "document_id": DOCUMENT_ID})

    assert response.status_code == 404
    notes_repo.add_note.assert_not_called()


def test_reply_takes_its_notes_document_and_keeps_the_original(repos) -> None:
    notes_repo, document_repo = repos
    notes_repo.get_note.return_value = _note(document_id=DOCUMENT_ID)

    response = client.post(f"/cases/{CASE_ID}/notes", json={"body": "Confirmed with the bank.", "parent_id": NOTE_ID})

    assert response.status_code == 201
    assert response.json()["parent_id"] == NOTE_ID
    assert response.json()["document_id"] == DOCUMENT_ID
    notes_repo.add_note.assert_called_once_with(
        case_id=CASE_ID, author=TEST_USER, body="Confirmed with the bank.", document_id=DOCUMENT_ID, parent_id=NOTE_ID,
    )
    # A new row is added; there is no call that could change the original note.
    assert {c[0] for c in notes_repo.method_calls} == {"get_note", "add_note"}


@pytest.mark.parametrize(
    ("parent", "payload", "expected"),
    [
        (None, {}, 404),  # no such note
        (_note(case_id=OTHER_CASE_ID), {}, 404),  # a note from another case
        (_note(parent_id=REPLY_ID), {}, 400),  # replying to a reply
        (_note(document_id=None), {"document_id": DOCUMENT_ID}, 400),  # moving the reply to another document
    ],
)
def test_reply_is_refused(repos, parent, payload, expected) -> None:
    notes_repo, _ = repos
    notes_repo.get_note.return_value = parent

    response = client.post(f"/cases/{CASE_ID}/notes", json={"body": "more", "parent_id": NOTE_ID, **payload})

    assert response.status_code == expected
    notes_repo.add_note.assert_not_called()


@pytest.mark.parametrize("body", ["", "   ", "x" * 4001])
def test_blank_or_overlong_note_is_refused(repos, body) -> None:
    notes_repo, _ = repos
    assert client.post(f"/cases/{CASE_ID}/notes", json={"body": body}).status_code == 422
    notes_repo.add_note.assert_not_called()


def test_notes_on_an_unknown_case(repos) -> None:
    missing = "99999999-9999-9999-9999-999999999999"
    assert client.get(f"/cases/{missing}/notes").status_code == 404
    assert client.post(f"/cases/{missing}/notes", json={"body": "x"}).status_code == 404


def test_reload_returns_the_saved_notes_unchanged(repos) -> None:
    notes_repo, _ = repos
    saved = [_note(), _note(id=REPLY_ID, parent_id=NOTE_ID, body="Reply.")]
    notes_repo.list_notes.return_value = saved

    response = client.get(f"/cases/{CASE_ID}/notes")

    assert response.status_code == 200
    assert [CaseNote.model_validate(n) for n in response.json()] == saved
    notes_repo.list_notes.assert_called_once_with(CASE_ID)


def test_save_failure_is_a_502(repos) -> None:
    notes_repo, _ = repos
    notes_repo.add_note.side_effect = RuntimeError("down")
    assert client.post(f"/cases/{CASE_ID}/notes", json={"body": "x"}).status_code == 502


def test_author_can_edit_their_note_and_it_shows_as_edited(repos, published_events) -> None:
    notes_repo, document_repo = repos
    notes_repo.get_note.return_value = _note(document_id=DOCUMENT_ID)
    notes_repo.update_note.side_effect = lambda note_id, body: _note(
        id=note_id, document_id=DOCUMENT_ID, body=body, edited_at=_TIMESTAMP,
    )

    response = client.patch(f"/cases/{CASE_ID}/notes/{NOTE_ID}", json={"body": "  Corrected wording.  "})

    assert response.status_code == 200
    assert response.json()["body"] == "Corrected wording."
    assert response.json()["edited_at"]
    notes_repo.update_note.assert_called_once_with(NOTE_ID, "Corrected wording.")
    # Editing a note never reads or writes its document.
    assert document_repo.method_calls == []
    assert published_events[-1].type == "note.edited"
    assert published_events[-1].detail["note_id"] == NOTE_ID


def test_author_can_delete_their_note(repos, published_events) -> None:
    notes_repo, _ = repos
    notes_repo.get_note.return_value = _note()

    response = client.delete(f"/cases/{CASE_ID}/notes/{NOTE_ID}")

    assert response.status_code == 204
    notes_repo.delete_note.assert_called_once_with(_note())
    assert published_events[-1].type == "note.deleted"


@pytest.mark.parametrize(
    ("note", "expected"),
    [
        (None, 404),  # no such note
        (_note(case_id=OTHER_CASE_ID), 404),  # a note from another case
        (_note(author="bob"), 403),  # another officer's note
    ],
)
def test_only_the_author_can_edit_or_delete_a_note_in_this_case(repos, note, expected) -> None:
    notes_repo, _ = repos
    notes_repo.get_note.return_value = note

    assert client.patch(f"/cases/{CASE_ID}/notes/{NOTE_ID}", json={"body": "changed"}).status_code == expected
    assert client.delete(f"/cases/{CASE_ID}/notes/{NOTE_ID}").status_code == expected
    notes_repo.update_note.assert_not_called()
    notes_repo.delete_note.assert_not_called()


def test_edit_to_a_blank_note_is_refused(repos) -> None:
    notes_repo, _ = repos
    notes_repo.get_note.return_value = _note()
    assert client.patch(f"/cases/{CASE_ID}/notes/{NOTE_ID}", json={"body": "   "}).status_code == 422
    notes_repo.update_note.assert_not_called()


def test_a_deleted_note_cannot_be_edited_deleted_or_replied_to(repos) -> None:
    notes_repo, _ = repos
    notes_repo.get_note.return_value = _note(body="(deleted)", deleted_at=_TIMESTAMP)

    assert client.patch(f"/cases/{CASE_ID}/notes/{NOTE_ID}", json={"body": "back"}).status_code == 409
    assert client.delete(f"/cases/{CASE_ID}/notes/{NOTE_ID}").status_code == 409
    assert client.post(f"/cases/{CASE_ID}/notes", json={"body": "hi", "parent_id": NOTE_ID}).status_code == 409
    notes_repo.update_note.assert_not_called()
    notes_repo.delete_note.assert_not_called()
    notes_repo.add_note.assert_not_called()


class _FakeNotesTable:
    """Just enough of the case_notes table for notes_repository.delete_note: rows by id."""

    def __init__(self, *notes: CaseNote) -> None:
        self.rows = {n.id: n.model_dump(mode="json") for n in notes}

    def client(self):
        table = self

        class Query:
            def __init__(self) -> None:
                self.op, self.values, self.filters = "select", None, {}

            def select(self, *_):
                return self

            def delete(self):
                self.op = "delete"
                return self

            def update(self, values):
                self.op, self.values = "update", values
                return self

            def eq(self, column, value):
                self.filters[column] = value
                return self

            def limit(self, _):
                return self

            def execute(self):
                hits = [r for r in table.rows.values() if all(r.get(k) == v for k, v in self.filters.items())]
                for row in hits:
                    if self.op == "delete":
                        del table.rows[row["id"]]
                    elif self.op == "update":
                        row.update(self.values)
                return type("Result", (), {"data": hits})()

        class Client:
            def table(self, _name):
                return Query()

        return Client()


def _delete(fake: _FakeNotesTable, note: CaseNote) -> None:
    from app import notes_repository

    with patch("app.notes_repository.service_supabase_client", fake.client):
        notes_repository.delete_note(note)


def test_deleting_a_note_without_replies_removes_it() -> None:
    fake = _FakeNotesTable(_note())
    _delete(fake, _note())
    assert fake.rows == {}


def test_deleting_a_note_with_replies_leaves_a_placeholder_and_the_replies() -> None:
    reply = _note(id=REPLY_ID, parent_id=NOTE_ID, author="bob", body="Agreed.")
    fake = _FakeNotesTable(_note(), reply)

    _delete(fake, _note())

    placeholder = fake.rows[NOTE_ID]
    assert placeholder["deleted_at"] is not None
    assert placeholder["body"] == "(deleted)"  # the text itself is gone
    assert fake.rows[REPLY_ID]["body"] == "Agreed."


def test_deleting_the_last_reply_also_removes_the_placeholder() -> None:
    placeholder = _note(body="(deleted)", deleted_at=_TIMESTAMP)
    reply = _note(id=REPLY_ID, parent_id=NOTE_ID, body="Agreed.")
    fake = _FakeNotesTable(placeholder, reply)

    _delete(fake, reply)

    assert fake.rows == {}


def test_deleting_a_reply_keeps_a_note_that_was_not_deleted() -> None:
    reply = _note(id=REPLY_ID, parent_id=NOTE_ID, body="Agreed.")
    fake = _FakeNotesTable(_note(), reply)

    _delete(fake, reply)

    assert list(fake.rows) == [NOTE_ID]
