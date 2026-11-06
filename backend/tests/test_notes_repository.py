"""
The case_notes table (notes_repository.py), on an in-memory table (tests/support/fake_supabase.py): editing, the
placeholder a deleted note with replies leaves, and notes following a moved document.
"""
from __future__ import annotations

import pytest

from app import notes_repository
from tests.support.fake_supabase import FakeSupabase

CASE, OTHER_CASE = "case-1", "case-2"
# Every test runs against the in-memory table.
pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def db(monkeypatch) -> FakeSupabase:
    fake = FakeSupabase(defaults={"case_notes": {"document_id": None, "parent_id": None, "edited_at": None,
                                                  "deleted_at": None}})
    monkeypatch.setattr(notes_repository, "service_supabase_client", lambda: fake)
    return fake


def note(body: str, *, parent_id: str | None = None, document_id: str | None = None):
    return notes_repository.add_note(case_id=CASE, author="demo", body=body, parent_id=parent_id, document_id=document_id)


def test_an_edit_changes_the_text_and_marks_it_edited() -> None:
    original = note("Checked the payslip.")

    edited = notes_repository.update_note(original.id, "Checked the payslip against the bank credit.")

    assert edited.body == "Checked the payslip against the bank credit."
    assert edited.edited_at is not None
    assert notes_repository.get_note(original.id).body == edited.body


def test_editing_a_note_that_is_gone_raises() -> None:
    with pytest.raises(RuntimeError, match="was not updated"):
        notes_repository.update_note("no-such-note", "text")


def test_deleting_a_note_without_replies_removes_it() -> None:
    lone = note("Nothing to add.")

    notes_repository.delete_note(lone)

    assert notes_repository.list_notes(CASE) == []


def test_deleting_a_note_with_replies_leaves_a_placeholder_and_keeps_the_replies() -> None:
    parent = note("Director differs on the BizFile.")
    reply = note("Confirmed with the client.", parent_id=parent.id)

    notes_repository.delete_note(parent)

    placeholder = notes_repository.get_note(parent.id)
    assert (placeholder.body, placeholder.deleted_at is not None) == (notes_repository.DELETED_BODY, True)
    assert notes_repository.get_note(reply.id).body == "Confirmed with the client."


def test_removing_the_last_reply_also_removes_its_placeholder() -> None:
    parent = note("Director differs on the BizFile.")
    first = note("Asked the client.", parent_id=parent.id)
    second = note("Client confirmed.", parent_id=parent.id)
    notes_repository.delete_note(notes_repository.get_note(parent.id))

    notes_repository.delete_note(first)
    assert notes_repository.get_note(parent.id) is not None  # a reply is still under it

    notes_repository.delete_note(second)
    assert notes_repository.list_notes(CASE) == []


def test_removing_a_reply_keeps_a_note_that_was_not_deleted() -> None:
    parent = note("Director differs on the BizFile.")
    reply = note("Asked the client.", parent_id=parent.id)

    notes_repository.delete_note(reply)

    assert [n.id for n in notes_repository.list_notes(CASE)] == [parent.id]


def test_notes_on_a_moved_document_follow_it() -> None:
    on_document = note("Payslip net pay matches.", document_id="doc-1")
    on_case = note("Case looks complete.")

    notes_repository.move_with_documents(["doc-1"], OTHER_CASE)

    assert [n.id for n in notes_repository.list_notes(OTHER_CASE)] == [on_document.id]
    assert [n.id for n in notes_repository.list_notes(CASE)] == [on_case.id]
