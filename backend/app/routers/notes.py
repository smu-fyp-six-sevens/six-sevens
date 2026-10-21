from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, document_repository, notes_repository
from ..auth import CurrentUser
from ..models import CaseNote, CaseRecord, CreateNoteRequest, UpdateNoteRequest
from ._dependencies import bad_gateway_on_error, get_case_or_404, to_thread_or_502

router = APIRouter(prefix="/cases", tags=["notes"])
logger = logging.getLogger(__name__)


@router.get("/{case_id}/notes", response_model=list[CaseNote])
async def list_case_notes(case_id: str, case: CaseRecord = Depends(get_case_or_404)) -> list[CaseNote]:
    """Every note on the case and on its documents, oldest first."""
    return await to_thread_or_502(
        notes_repository.list_notes, case_id,
        detail="Failed to load the case's notes. Check Supabase table access.",
        log=("Failed to list notes for case %s", case_id),
    )


@router.post("/{case_id}/notes", response_model=CaseNote, status_code=status.HTTP_201_CREATED)
async def add_case_note(
    case_id: str,
    request: CreateNoteRequest,
    user: CurrentUser,
    case: CaseRecord = Depends(get_case_or_404),
) -> CaseNote:
    """
    Saves a note signed by the officer making the request. Allowed on any document, approved and
    rejected ones included: a note is stored on its own and never edits the document, so the
    document's lock is untouched. Any officer can reply to a note (parent_id).
    """
    document_id = str(request.document_id) if request.document_id else None
    parent_id = str(request.parent_id) if request.parent_id else None
    with bad_gateway_on_error(
        "Failed to save the note. Check Supabase table access.", "Failed to add a note to case %s", case_id
    ):
        if parent_id:
            parent = await asyncio.to_thread(notes_repository.get_note, parent_id)
            if parent is None or parent.case_id != case_id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found in this case")
            if parent.deleted_at is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This note was deleted.")
            if parent.parent_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Reply to the original note, not to one of its replies.",
                )
            if document_id and document_id != parent.document_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A reply belongs to the same document as the note it replies to.",
                )
            document_id = parent.document_id
        elif document_id:
            document = await asyncio.to_thread(document_repository.get_document, document_id, include_fields=False)
            if document is None or document.case_id != case_id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found in this case")

        note = await asyncio.to_thread(
            notes_repository.add_note,
            case_id=case_id, author=user, body=request.body, document_id=document_id, parent_id=parent_id,
        )
    await audit.arecord(
        "note.added", user, case_id=case_id, document_id=document_id, note_id=note.id, reply=parent_id is not None,
    )
    return note


async def _own_note(case_id: str, note_id: str, user: str, action: str) -> CaseNote:
    """The note, if it is in this case, still there, and the requesting officer wrote it."""
    with bad_gateway_on_error("Failed to load the note. Check Supabase table access.", "Failed to fetch note %s", note_id):
        note = await asyncio.to_thread(notes_repository.get_note, note_id)
    if note is None or note.case_id != case_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found in this case")
    if note.author != user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Only the officer who wrote a note can {action} it.")
    if note.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This note was already deleted.")
    return note


@router.patch("/{case_id}/notes/{note_id}", response_model=CaseNote)
async def edit_case_note(
    case_id: str,
    note_id: UUID,
    request: UpdateNoteRequest,
    user: CurrentUser,
    case: CaseRecord = Depends(get_case_or_404),
) -> CaseNote:
    """Replaces the text of the officer's own note and stamps edited_at. Its document is never touched."""
    note = await _own_note(case_id, str(note_id), user, "edit")
    updated = await to_thread_or_502(
        notes_repository.update_note, note.id, request.body,
        detail="Failed to save the note. Check Supabase table access.",
        log=("Failed to edit note %s", note.id),
    )
    await audit.arecord("note.edited", user, case_id=case_id, document_id=note.document_id, note_id=note.id)
    return updated


@router.delete("/{case_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case_note(
    case_id: str,
    note_id: UUID,
    user: CurrentUser,
    case: CaseRecord = Depends(get_case_or_404),
) -> None:
    """
    Deletes the officer's own note. Replies by anyone stay: a note with replies becomes a "This
    note was deleted" placeholder above them (see notes_repository.delete_note).
    """
    note = await _own_note(case_id, str(note_id), user, "delete")
    await to_thread_or_502(
        notes_repository.delete_note, note,
        detail="Failed to delete the note. Check Supabase table access.",
        log=("Failed to delete note %s", note.id),
    )
    await audit.arecord("note.deleted", user, case_id=case_id, document_id=note.document_id, note_id=note.id)
