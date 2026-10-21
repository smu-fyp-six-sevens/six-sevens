import { assertOk } from './request';
import { authFetch } from '../../lib/api';
import type { CaseNote } from './types';

// Every note on the case and its documents, oldest first.
export async function listCaseNotes(caseId: string): Promise<CaseNote[]> {
  const response = await authFetch(`/cases/${caseId}/notes`);

  await assertOk(response, 'Failed to load notes');

  return (await response.json()) as CaseNote[];
}

// The backend signs the note with the logged-in officer; a reply (parentId) takes its note's document.
export async function addCaseNote(
  caseId: string,
  note: { body: string; documentId?: string | null; parentId?: string | null },
): Promise<CaseNote> {
  const response = await authFetch(`/cases/${caseId}/notes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ body: note.body, document_id: note.documentId ?? null, parent_id: note.parentId ?? null }),
  });

  await assertOk(response, 'Failed to save the note');

  return (await response.json()) as CaseNote;
}

// Only the note's author may edit or delete it; the backend refuses anyone else (403).
export async function updateCaseNote(caseId: string, noteId: string, body: string): Promise<CaseNote> {
  const response = await authFetch(`/cases/${caseId}/notes/${noteId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ body }),
  });

  await assertOk(response, 'Failed to save the note');

  return (await response.json()) as CaseNote;
}

// Deletes the note. Replies stay: a note with replies becomes a "This note was deleted" placeholder.
export async function deleteCaseNote(caseId: string, noteId: string): Promise<void> {
  const response = await authFetch(`/cases/${caseId}/notes/${noteId}`, { method: 'DELETE' });

  await assertOk(response, 'Failed to delete the note');
}
