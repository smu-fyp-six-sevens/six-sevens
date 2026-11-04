import { useEffect, useMemo, useState, type ReactNode } from 'react';
import ConfirmDialog from './ConfirmDialog';
import { getUsername } from '../lib/api';
import { officerName } from '../lib/officers';
import { formatDate } from '../pages/document-details/formatters';
import { addCaseNote, deleteCaseNote, listCaseNotes, updateCaseNote, type CaseNote } from '../services/uploadService';
import { ErrorMessage } from './ErrorMessage';
import { Icon } from './Icon';

const MAX_NOTE_LENGTH = 4000; // MAX_NOTE_LENGTH in backend/app/models.py
const WHOLE_CASE = '';

interface NoteDocument {
  id: string;
  filename: string;
}

// What the officer is currently writing: a reply under a note, or a new text for one of their own.
type Editor = { kind: 'reply'; noteId: string } | { kind: 'edit'; noteId: string };

// A loan officer's notes on a case or on one document. Any officer can reply to a note; only its
// author can edit or delete it (the backend enforces this too). An edited note says so.
export function NotesCard({
  caseId,
  caseNumber,
  documents,
  documentId,
  lockedNotice,
  finalised = false,
}: {
  caseId: string;
  // The case's GLDB number, naming notes on the whole case.
  caseNumber?: string | null;
  // The case's documents, to name the one a note belongs to (and, on the case page, to choose it).
  documents: NoteDocument[];
  // Set on a document's page: only its notes are shown, and new ones are attached to it.
  documentId?: string;
  lockedNotice?: string;
  // A finalised case: notes can still be added and replied to, but not edited or deleted.
  finalised?: boolean;
}) {
  const me = getUsername();
  const [notes, setNotes] = useState<CaseNote[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  // The new-note box is one line until the officer starts writing in it.
  const [isComposing, setIsComposing] = useState(false);
  const [attachTo, setAttachTo] = useState(WHOLE_CASE);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [editorDraft, setEditorDraft] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<CaseNote | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setNotes(null);
    setLoadError(null);
    listCaseNotes(caseId)
      .then((loaded) => {
        if (!cancelled) setNotes(loaded);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setLoadError(caught instanceof Error ? caught.message : 'Failed to load notes');
      });
    return () => {
      cancelled = true;
    };
  }, [caseId]);

  const caseLabel = caseNumber ?? 'This case';
  const filenames = useMemo(() => new Map(documents.map((document) => [document.id, document.filename])), [documents]);

  const visible = (notes ?? []).filter((note) => documentId === undefined || note.document_id === documentId);
  const threads = visible.filter((note) => note.parent_id === null);
  const replies = (noteId: string) => visible.filter((note) => note.parent_id === noteId);

  async function run(action: () => Promise<void>, fallback: string): Promise<boolean> {
    setIsSaving(true);
    setSaveError(null);
    try {
      await action();
      return true;
    } catch (caught) {
      setSaveError(caught instanceof Error ? caught.message : fallback);
      return false;
    } finally {
      setIsSaving(false);
    }
  }

  async function saveNote() {
    const target = documentId ?? (attachTo === WHOLE_CASE ? null : attachTo);
    const saved = await run(async () => {
      const note = await addCaseNote(caseId, { body: draft.trim(), documentId: target });
      setNotes((current) => [...(current ?? []), note]);
    }, 'Failed to save the note');
    if (saved) setDraft('');
  }

  async function saveEditor(current: Editor) {
    const saved = await run(async () => {
      if (current.kind === 'reply') {
        const note = await addCaseNote(caseId, { body: editorDraft.trim(), parentId: current.noteId });
        setNotes((existing) => [...(existing ?? []), note]);
      } else {
        const note = await updateCaseNote(caseId, current.noteId, editorDraft.trim());
        setNotes((existing) => (existing ?? []).map((n) => (n.id === note.id ? note : n)));
      }
    }, 'Failed to save the note');
    if (saved) closeEditor();
  }

  async function confirmDelete(note: CaseNote) {
    setIsDeleting(true);
    // Reloaded rather than worked out here: the server decides whether a placeholder stays.
    const deleted = await run(async () => {
      await deleteCaseNote(caseId, note.id);
      setNotes(await listCaseNotes(caseId));
    }, 'Failed to delete the note');
    setIsDeleting(false);
    if (deleted) setDeleting(null);
  }

  function openEditor(next: Editor, text: string) {
    setEditor(next);
    setEditorDraft(text);
    setSaveError(null);
  }

  function closeEditor() {
    setEditor(null);
    setEditorDraft('');
  }

  function documentLabel(note: CaseNote): string {
    if (note.document_id === null) return caseLabel;
    return filenames.get(note.document_id) ?? 'Document';
  }

  function editorForm(current: Editor, label: string) {
    return (
      <form
        className="notes-composer notes-composer--inline"
        onSubmit={(event) => {
          event.preventDefault();
          void saveEditor(current);
        }}
      >
        <textarea
          className="notes-textarea"
          aria-label={label}
          value={editorDraft}
          maxLength={MAX_NOTE_LENGTH}
          rows={2}
          autoFocus
          onChange={(event) => setEditorDraft(event.target.value)}
        />
        <div className="notes-composer-actions">
          <button type="button" className="text-button" onClick={closeEditor}>
            Cancel
          </button>
          <button type="submit" className="analysis-action" disabled={isSaving || !editorDraft.trim()}>
            {isSaving ? 'Saving…' : current.kind === 'reply' ? 'Save reply' : 'Save changes'}
          </button>
        </div>
      </form>
    );
  }

  // The note's text, or the edit form in its place.
  function noteContent(note: CaseNote) {
    if (note.deleted_at) return <p className="note-body note-body--deleted">This note was deleted.</p>;
    if (editor?.kind === 'edit' && editor.noteId === note.id) return editorForm(editor, 'Edit note');
    return <p className="note-body">{note.body}</p>;
  }

  // Reply on a note (not on a reply), plus Edit and Delete for the officer's own notes and replies.
  function noteActions(note: CaseNote) {
    if (note.deleted_at) return null;
    const canAdd = note.parent_id === null && !(editor?.kind === 'reply' && editor.noteId === note.id);
    return (
      <>
        {canAdd && (
          <button type="button" className="text-button" onClick={() => openEditor({ kind: 'reply', noteId: note.id }, '')}>
            Reply
          </button>
        )}
        {note.author === me && !finalised && (
          <span className="note-owner-actions">
            <button type="button" className="text-button" onClick={() => openEditor({ kind: 'edit', noteId: note.id }, note.body)}>
              Edit
            </button>
            <button type="button" className="text-button note-delete" onClick={() => setDeleting(note)}>
              Delete
            </button>
          </span>
        )}
      </>
    );
  }

  const deletingReplies = deleting && deleting.parent_id === null ? replies(deleting.id).length : 0;

  return (
    <section className="summary-card notes-card">
      <div className="summary-card-header">
        <div className="summary-card-title">
          <span className="summary-icon"><Icon name="notes" size={20} /></span>
          <h2>Notes{threads.length > 0 && <span className="notes-count"> · {threads.length}</span>}</h2>
        </div>
        {finalised ? (
          <span className="notes-locked">Case finalised · new notes only</span>
        ) : (
          lockedNotice && <span className="notes-locked">{lockedNotice}</span>
        )}
      </div>

      <form
        className="notes-composer notes-composer--new"
        onSubmit={(event) => {
          event.preventDefault();
          void saveNote();
        }}
      >
        <label className="sr-only" htmlFor={`note-draft-${documentId ?? caseId}`}>
          New note
        </label>
        <textarea
          id={`note-draft-${documentId ?? caseId}`}
          className="notes-textarea"
          value={draft}
          maxLength={MAX_NOTE_LENGTH}
          rows={isComposing || draft ? 3 : 1}
          placeholder="Add a note…"
          onFocus={() => setIsComposing(true)}
          onBlur={() => setIsComposing(false)}
          onChange={(event) => setDraft(event.target.value)}
        />
        <div className="notes-composer-actions">
          {documentId === undefined && (
            <label className="notes-attach">
              On
              <select value={attachTo} onChange={(event) => setAttachTo(event.target.value)}>
                <option value={WHOLE_CASE}>{caseLabel}</option>
                {documents.map((document) => (
                  <option key={document.id} value={document.id}>
                    {document.filename}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button type="submit" className="analysis-action" disabled={isSaving || !draft.trim()}>
            {isSaving && editor === null ? 'Saving…' : 'Save note'}
          </button>
        </div>
      </form>

      {saveError && <ErrorMessage className="card-error" message={saveError} />}

      {loadError ? (
        <ErrorMessage className="card-error" message={loadError} />
      ) : notes === null ? (
        <p className="notes-empty">Loading notes…</p>
      ) : threads.length === 0 ? (
        <p className="notes-empty">No notes yet.</p>
      ) : (
        <ul className="notes-list">
          {threads.map((note) => (
            <li key={note.id} className="note" data-testid="note">
              {note.deleted_at ? (
                documentId === undefined && (
                  <div className="note-meta">
                    <span className="note-document">{documentLabel(note)}</span>
                  </div>
                )
              ) : (
                <NoteHeader note={note} label={documentId === undefined ? documentLabel(note) : null}>
                  {noteActions(note)}
                </NoteHeader>
              )}
              {noteContent(note)}

              {replies(note.id).map((reply) => (
                <div key={reply.id} className="note-reply" data-testid="note-reply">
                  <NoteHeader note={reply} label={null} isReply>
                    {noteActions(reply)}
                  </NoteHeader>
                  {noteContent(reply)}
                </div>
              ))}

              {editor?.kind === 'reply' && editor.noteId === note.id && editorForm(editor, 'Reply to this note')}
            </li>
          ))}
        </ul>
      )}

      {deleting && (
        <ConfirmDialog
          title={deleting.parent_id === null ? 'Delete this note?' : 'Delete this reply?'}
          message={
            deletingReplies > 0
              ? `Its text will be deleted and replaced by "This note was deleted". The ${deletingReplies === 1 ? 'reply' : `${deletingReplies} replies`} to it will stay. This can't be undone.`
              : "It will be deleted. This can't be undone."
          }
          confirmLabel="Delete"
          isConfirming={isDeleting}
          onConfirm={() => void confirmDelete(deleting)}
          onCancel={() => {
            if (!isDeleting) setDeleting(null);
          }}
        />
      )}
    </section>
  );
}

function NoteHeader({
  note,
  label,
  isReply = false,
  children,
}: {
  note: CaseNote;
  label: string | null;
  isReply?: boolean;
  children?: ReactNode;
}) {
  return (
    <div className="note-meta">
      <strong>{isReply ? `Reply from ${officerName(note.author)}` : officerName(note.author)}</strong>
      <time dateTime={note.created_at}>{formatDate(note.created_at)}</time>
      {note.edited_at && (
        <span className="note-edited" title={`Edited ${formatDate(note.edited_at)}`}>
          edited {formatDate(note.edited_at)}
        </span>
      )}
      {label && <span className="note-document">{label}</span>}
      <span className="note-actions">{children}</span>
    </div>
  );
}
