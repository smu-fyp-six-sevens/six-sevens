"""
The `case_notes` table: what a loan officer wrote against a case, or against one of its documents.

An officer can reply to a note (a row whose parent_id is that note, with its own author and time),
and its author can edit or delete it (routers/notes.py checks who is asking). An edit stamps
edited_at. Deleting a note never removes anyone else's replies: a note with replies becomes a
placeholder (its text erased, deleted_at set), and goes once its last reply does. A note never
touches its document, so an approved document stays locked while its notes change.

Notes go with their document or case when either is deleted (document_repository and
case_repository remove them), and follow a document moved to another case.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .models import CaseNote
from .supabase_client import service_supabase_client

TABLE = "case_notes"
# What a deleted placeholder's body holds: the text is erased, and the column can't be empty.
DELETED_BODY = "(deleted)"


def list_notes(case_id: str) -> list[CaseNote]:
    """Every note on the case and its documents, oldest first, so a reply reads after its note."""
    rows = (
        service_supabase_client().table(TABLE).select("*")
        .eq("case_id", case_id).order("created_at").execute().data or []
    )
    return [CaseNote.model_validate(row) for row in rows]


def get_note(note_id: str) -> CaseNote | None:
    rows = service_supabase_client().table(TABLE).select("*").eq("id", note_id).limit(1).execute().data or []
    return CaseNote.model_validate(rows[0]) if rows else None


def add_note(
    *, case_id: str, author: str, body: str, document_id: str | None = None, parent_id: str | None = None
) -> CaseNote:
    result = service_supabase_client().table(TABLE).insert({
        "case_id": case_id,
        "document_id": document_id,
        "parent_id": parent_id,
        "author": author,
        "body": body,
    }).execute()
    if not result.data:
        raise RuntimeError(f"The note on case {case_id} was not saved")
    return CaseNote.model_validate(result.data[0])


def update_note(note_id: str, body: str) -> CaseNote:
    result = service_supabase_client().table(TABLE).update({
        "body": body,
        "edited_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", note_id).execute()
    if not result.data:
        raise RuntimeError(f"Note {note_id} was not updated")
    return CaseNote.model_validate(result.data[0])


def _replies(note_id: str) -> list[dict]:
    return service_supabase_client().table(TABLE).select("id").eq("parent_id", note_id).execute().data or []


def _remove(note_id: str) -> None:
    result = service_supabase_client().table(TABLE).delete().eq("id", note_id).execute()
    if not result.data:
        raise RuntimeError(f"Note {note_id} was not deleted")


def delete_note(note: CaseNote) -> None:
    """
    Removes the note, unless it has replies: then its text is erased and it stays as a placeholder
    above them. Removing a reply also removes its note's placeholder once no replies are left.
    """
    if note.parent_id is None:
        if not _replies(note.id):
            _remove(note.id)
            return
        result = service_supabase_client().table(TABLE).update({
            "body": DELETED_BODY,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", note.id).execute()
        if not result.data:
            raise RuntimeError(f"Note {note.id} was not deleted")
        return

    _remove(note.id)
    parent = get_note(note.parent_id)
    if parent is not None and parent.deleted_at is not None and not _replies(parent.id):
        _remove(parent.id)


def move_with_documents(document_ids: list[str], case_id: str) -> None:
    """A moved document takes its notes to its new case."""
    if document_ids:
        service_supabase_client().table(TABLE).update({"case_id": case_id}).in_("document_id", document_ids).execute()
