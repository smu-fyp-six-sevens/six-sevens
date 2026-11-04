import type { Locator } from '@playwright/test';
import { test, expect } from '../support/test';
import { loadFixture } from '../support/fixtures';

/**
 * Notes on a case or a document (SCRUM-81): a loan officer's reasoning kept with the evidence.
 * The mock backend signs notes with its logged-in officer, "officer".
 */

test.describe('Case and document notes @SCRUM-81', () => {
  test('an officer can edit and delete their own notes, but not another officer\'s', async ({ page, backend }) => {
    // A token naming the mock backend's officer, so the page knows which notes are theirs.
    await page.addInitScript(() => localStorage.setItem('authToken', `${btoa(JSON.stringify({ sub: 'officer' }))}.sig`));
    const fixture = loadFixture(backend, 'anchorpoint');
    const note = (id: string, author: string, body: string) => ({
      id,
      case_id: fixture.case.id,
      document_id: null,
      parent_id: null,
      author,
      body,
      created_at: '2026-10-20T03:00:00+00:00',
      edited_at: null,
      deleted_at: null,
    });
    backend.notes.push(note('note-mine', 'officer', 'First draft of my reasoning.'), note('note-bob', 'bob', "Bob's reasoning."));

    await page.goto(`${fixture.caseUrl}?section=notes`);
    // Notes list oldest first, so the officer's own note is first.
    const mine = page.getByTestId('note').first();
    await expect(mine).toContainText('First draft of my reasoning.');
    const bobs = page.getByTestId('note').filter({ hasText: "Bob's reasoning." });
    await expect(bobs.getByRole('button', { name: 'Edit' })).toHaveCount(0);
    await expect(bobs.getByRole('button', { name: 'Delete' })).toHaveCount(0);

    await mine.getByRole('button', { name: 'Edit' }).click();
    await mine.getByLabel('Edit note').fill('Reworded reasoning.');
    await mine.getByRole('button', { name: 'Save changes' }).click();
    const edited = page.getByTestId('note').filter({ hasText: 'Reworded reasoning.' });
    await expect(edited.locator('.note-edited')).toContainText('edited');

    await page.reload();
    await expect(edited.locator('.note-body')).toHaveText('Reworded reasoning.');

    await edited.getByRole('button', { name: 'Delete' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Delete' }).click();
    await expect(page.getByTestId('note')).toHaveCount(1);
    await expect(page.getByTestId('note')).toContainText("Bob's reasoning.");

    await page.reload();
    await expect(page.getByTestId('note')).toHaveCount(1);
  });

  test('deleting a note that has replies keeps the replies under a placeholder', async ({ page, backend }) => {
    await page.addInitScript(() => localStorage.setItem('authToken', `${btoa(JSON.stringify({ sub: 'officer' }))}.sig`));
    const fixture = loadFixture(backend, 'anchorpoint');
    const row = { case_id: fixture.case.id, document_id: null, edited_at: null, deleted_at: null };
    backend.notes.push(
      { ...row, id: 'note-mine', parent_id: null, author: 'officer', body: 'My reasoning.', created_at: '2026-10-20T03:00:00+00:00' },
      { ...row, id: 'reply-bob', parent_id: 'note-mine', author: 'bob', body: "Bob's reply.", created_at: '2026-10-20T04:00:00+00:00' },
    );

    await page.goto(`${fixture.caseUrl}?section=notes`);
    const note = page.getByTestId('note');
    await note.locator('.note-meta').first().getByRole('button', { name: 'Delete' }).click();
    await expect(page.getByRole('alertdialog')).toContainText('The reply to it will stay');
    await page.getByRole('alertdialog').getByRole('button', { name: 'Delete' }).click();

    await expect(note.locator(':scope > .note-body')).toHaveText('This note was deleted.');
    await expect(note).not.toContainText('My reasoning.');
    await expect(note.getByTestId('note-reply').locator('.note-body')).toHaveText("Bob's reply.");
    await expect(note.getByRole('button', { name: 'Reply' })).toHaveCount(0);

    await page.reload();
    await expect(page.getByTestId('note').locator(':scope > .note-body')).toHaveText('This note was deleted.');
    await expect(page.getByTestId('note-reply')).toContainText("Bob's reply.");
  });
});

/**
 * Sprint 4 regression (SCRUM-296), "Test Cases - Sprint 4.xlsx" Case Management sheet, CM-029 – CM-036. The
 * live run used case GLDB-130; the captured Anchorpoint case (GLDB-02) stands in for it here.
 */
test.describe('Attach officer notes @sprint4 @SCRUM-81', () => {
  /** What reloading must leave unchanged: the note's header (author, time, document) and its exact timestamp. */
  const snapshotOf = async (note: Locator) => ({
    meta: await note.locator('.note-meta').textContent(),
    time: await note.locator('time').getAttribute('datetime'),
  });

  test('CM-029 / CM-031 / CM-033 / CM-034 a case note shows its author and time, no document, and survives a reload @CM-029 @CM-031 @CM-033 @CM-034', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    await page.goto(`${fixture.caseUrl}?section=notes`);

    await page.getByLabel('New note').fill('Directors match the incumbency certificate.');
    await page.getByRole('button', { name: 'Save note' }).click();

    const note = page.getByTestId('note');
    await expect(note).toHaveCount(1);
    await expect(note.locator('.note-body')).toHaveText('Directors match the incumbency certificate.');
    await expect(note.locator('.note-meta strong')).toHaveText('officer');
    await expect(note.locator('time')).toHaveAttribute('datetime', backend.notes[0].created_at);
    // Labelled with the case itself, never one of its documents.
    await expect(note.locator('.note-document')).toHaveText(fixture.case.greenlink_case_id);
    expect(backend.requestsTo('POST', `/cases/${fixture.case.id}/notes`)[0].body).toEqual({
      body: 'Directors match the incumbency certificate.',
      document_id: null,
      parent_id: null,
    });
    const before = await snapshotOf(note);

    await page.reload();

    await expect(note).toHaveCount(1);
    await expect(note.locator('.note-body')).toHaveText('Directors match the incumbency certificate.');
    expect(await snapshotOf(note)).toEqual(before);
  });

  test('CM-030 / CM-032 / CM-035 a document note shows its author, time and document, and survives a reload on that document only @CM-030 @CM-032 @CM-035', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    const statement = fixture.document('Statement_128262');

    // From the document's own page: saved against it without picking it.
    await page.goto(fixture.documentUrl(payslip));
    await page.getByLabel('New note').fill('Net pay matches the salary credits.');
    await page.getByRole('button', { name: 'Save note' }).click();
    await expect(page.getByTestId('note').locator('.note-body')).toHaveText('Net pay matches the salary credits.');
    expect(backend.requestsTo('POST', `/cases/${fixture.case.id}/notes`)[0].body).toMatchObject({ document_id: payslip.id });

    // From the case's notes, picking the document: it shows who wrote it, when, and which document.
    await page.goto(`${fixture.caseUrl}?section=notes`);
    await page.getByLabel('New note').fill('CPF figure checked against the CPF statement.');
    await page.locator('.notes-attach select').selectOption({ label: payslip.filename });
    await page.getByRole('button', { name: 'Save note' }).click();
    const notes = page.getByTestId('note');
    await expect(notes).toHaveCount(2);
    for (const note of await notes.all()) {
      await expect(note.locator('.note-meta strong')).toHaveText('officer');
      await expect(note.locator('time')).not.toBeEmpty();
      await expect(note.locator('.note-document')).toHaveText(payslip.filename);
    }
    const before = await snapshotOf(notes.last());

    await page.reload();
    await expect(notes).toHaveCount(2);
    expect(await snapshotOf(notes.last())).toEqual(before);

    // Both are under the payslip, and not under another document of the case.
    await page.goto(fixture.documentUrl(payslip));
    await expect(page.getByTestId('note')).toHaveCount(2);
    await page.goto(fixture.documentUrl(statement));
    await expect(page.getByText('No notes yet.')).toBeVisible();
  });

  test('CM-036 a note on an approved document can be added to and replied to while the document stays locked @CM-036', async ({ page, backend }) => {
    // A token naming the mock backend's officer, so the page knows the note is theirs to edit.
    await page.addInitScript(() => localStorage.setItem('authToken', `${btoa(JSON.stringify({ sub: 'officer' }))}.sig`));
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    payslip.status = 'approved';
    backend.notes.push({
      id: 'note-1',
      case_id: fixture.case.id,
      document_id: payslip.id,
      parent_id: null,
      author: 'officer',
      body: 'Approved: figures reconcile.',
      created_at: '2026-10-20T03:00:00+00:00',
      edited_at: null,
      deleted_at: null,
    });

    await page.goto(fixture.documentUrl(payslip));
    await expect(page.locator('.notes-locked')).toContainText('approved and locked');
    const note = page.getByTestId('note');
    await note.locator('.note-meta').first().getByRole('button', { name: 'Edit' }).click();
    await note.getByLabel('Edit note').fill('Approved: figures reconcile. Bank confirmed the employer by phone.');
    await note.getByRole('button', { name: 'Save changes' }).click();
    await expect(note.locator(':scope > .note-body')).toHaveText('Approved: figures reconcile. Bank confirmed the employer by phone.');
    await expect(note.locator('.note-edited').first()).toContainText('edited');

    await note.getByRole('button', { name: 'Reply' }).click();
    await note.getByLabel('Reply to this note').fill('Second check: agreed.');
    await note.getByRole('button', { name: 'Save reply' }).click();
    await expect(note.getByTestId('note-reply').locator('.note-body')).toHaveText('Second check: agreed.');
    expect(backend.requestsTo('POST', `/cases/${fixture.case.id}/notes`)[0].body).toMatchObject({ parent_id: 'note-1' });

    // The document is untouched: still approved, still locked, and no request ever wrote to it.
    expect(backend.requests.filter((r) => r.method !== 'GET' && r.path.startsWith('/documents/'))).toEqual([]);
    expect(payslip.status).toBe('approved');
    await expect(page.locator('.notes-locked')).toContainText('approved and locked');
    await expect(page.getByRole('button', { name: /add field/i })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^approve document/i })).toHaveCount(0);
  });
});
