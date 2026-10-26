import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { loadFixture } from '../support/fixtures';

/**
 * Notes on a case or a document (SCRUM-81): a loan officer's reasoning kept with the evidence.
 * The mock backend signs notes with its logged-in officer, "officer".
 */

test.describe('Case and document notes @SCRUM-81', () => {
  test('a saved note shows who wrote it, when, and its document, and survives a reload', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');

    await page.goto(`${fixture.caseUrl}?section=notes`);
    await page.getByLabel('New note').fill('Salary matches the bank statement credits.');
    await page.locator('.notes-attach select').selectOption({ label: payslip.filename });
    await page.getByRole('button', { name: 'Save note' }).click();

    const note = page.getByTestId('note');
    await expect(note).toHaveCount(1);
    await expect(note.locator('.note-meta strong')).toHaveText('officer');
    await expect(note.locator('.note-meta time')).not.toBeEmpty();
    await expect(note.locator('.note-document')).toHaveText(payslip.filename);
    await expect(note.locator('.note-body')).toHaveText('Salary matches the bank statement credits.');
    expect(backend.requestsTo('POST', `/cases/${fixture.case.id}/notes`)[0].body).toEqual({
      body: 'Salary matches the bank statement credits.',
      document_id: payslip.id,
      parent_id: null,
    });

    const savedAt = await note.locator('.note-meta time').getAttribute('datetime');
    await page.reload();

    await expect(page.getByTestId('note')).toHaveCount(1);
    await expect(page.getByTestId('note').locator('.note-body')).toHaveText('Salary matches the bank statement credits.');
    await expect(page.getByTestId('note').locator('.note-meta time')).toHaveAttribute('datetime', savedAt ?? '');
  });

  test('a note on an approved document can be replied to while the document stays locked', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    payslip.status = 'approved';
    backend.notes.push({
      id: 'note-1',
      case_id: fixture.case.id,
      document_id: payslip.id,
      parent_id: null,
      author: 'bob',
      body: 'Approved: figures reconcile.',
      created_at: '2026-10-20T03:00:00+00:00',
      edited_at: null,
      deleted_at: null,
    });

    await page.goto(fixture.documentUrl(payslip));

    await expect(page.locator('.notes-locked')).toContainText('approved and locked');
    const note = page.getByTestId('note');
    await expect(note.locator('.note-meta strong')).toHaveText('B. Smith');

    await note.getByRole('button', { name: 'Reply' }).click();
    await note.getByLabel('Reply to this note').fill('Bank confirmed the employer by phone.');
    await note.getByRole('button', { name: 'Save reply' }).click();

    const reply = note.getByTestId('note-reply');
    await expect(reply.locator('.note-meta strong')).toHaveText('Reply from officer');
    await expect(reply.locator('.note-body')).toHaveText('Bank confirmed the employer by phone.');
    // The original note is left exactly as it was.
    await expect(note.locator(':scope > .note-body')).toHaveText('Approved: figures reconcile.');
    expect(backend.requestsTo('POST', `/cases/${fixture.case.id}/notes`)[0].body).toMatchObject({ parent_id: 'note-1' });

    // Replying never writes to the document: it is still approved and its fields can't be edited.
    expect(backend.requests.filter((r) => r.method !== 'GET' && r.path.startsWith('/documents/'))).toEqual([]);
    expect(payslip.status).toBe('approved');
    await expect(page.getByRole('button', { name: /add field/i })).toHaveCount(0);
  });

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
  const saveNote = async (page: Page, body: string, documentFilename?: string) => {
    await page.getByLabel('New note').fill(body);
    if (documentFilename) await page.locator('.notes-attach select').selectOption({ label: documentFilename });
    await page.getByRole('button', { name: 'Save note' }).click();
  };

  test('CM-029 a note can be added to a case and shows under it @CM-029', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    await page.goto(`${fixture.caseUrl}?section=notes`);

    await saveNote(page, 'Directors match the incumbency certificate.');

    await expect(page.getByTestId('note')).toHaveCount(1);
    await expect(page.getByTestId('note').locator('.note-body')).toHaveText('Directors match the incumbency certificate.');
    expect(backend.requestsTo('POST', `/cases/${fixture.case.id}/notes`)[0].body).toEqual({
      body: 'Directors match the incumbency certificate.',
      document_id: null,
      parent_id: null,
    });
  });

  test('CM-030 a note can be added to a single document @CM-030', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    await page.goto(fixture.documentUrl(payslip));

    await page.getByLabel('New note').fill('Net pay matches the salary credits.');
    await page.getByRole('button', { name: 'Save note' }).click();

    await expect(page.getByTestId('note').locator('.note-body')).toHaveText('Net pay matches the salary credits.');
    // Saved against this document, without having to pick it.
    expect(backend.requestsTo('POST', `/cases/${fixture.case.id}/notes`)[0].body).toMatchObject({ document_id: payslip.id });
  });

  test('CM-031 a saved case note shows its author and when it was saved @CM-031', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    await page.goto(`${fixture.caseUrl}?section=notes`);

    await saveNote(page, 'Called the employer to confirm.');

    const meta = page.getByTestId('note').locator('.note-meta');
    await expect(meta.locator('strong')).toHaveText('officer');
    await expect(meta.locator('time')).toHaveAttribute('datetime', backend.notes[0].created_at);
    await expect(meta.locator('time')).not.toBeEmpty();
  });

  test('CM-032 a saved document note shows its author, time and document @CM-032', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    await page.goto(`${fixture.caseUrl}?section=notes`);

    await saveNote(page, 'CPF figure checked against the CPF statement.', payslip.filename);

    const meta = page.getByTestId('note').locator('.note-meta');
    await expect(meta.locator('strong')).toHaveText('officer');
    await expect(meta.locator('time')).not.toBeEmpty();
    await expect(meta.locator('.note-document')).toHaveText(payslip.filename);
  });

  test('CM-033 a case-level note is not shown against any document @CM-033', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    await page.goto(`${fixture.caseUrl}?section=notes`);

    await saveNote(page, 'Case-wide: all documents received.');

    const meta = page.getByTestId('note').locator('.note-meta');
    await expect(meta.locator('strong')).toHaveText('officer');
    await expect(meta.locator('time')).not.toBeEmpty();
    // Labelled with the case itself, never one of its documents.
    await expect(meta.locator('.note-document')).toHaveText(fixture.case.greenlink_case_id);
    const filenames = fixture.documents.map((d) => d.filename);
    expect(filenames).not.toContain(await meta.locator('.note-document').textContent());
    expect(backend.notes[0].document_id).toBeNull();
  });

  test('CM-034 a saved case note is unchanged after reloading the case @CM-034', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    await page.goto(`${fixture.caseUrl}?section=notes`);
    await saveNote(page, 'Directors match the incumbency certificate.');
    const note = page.getByTestId('note');
    await expect(note).toHaveCount(1);
    const before = { meta: await note.locator('.note-meta').textContent(), time: await note.locator('time').getAttribute('datetime') };

    await page.reload();

    await expect(note).toHaveCount(1);
    await expect(note.locator('.note-body')).toHaveText('Directors match the incumbency certificate.');
    await expect(note.locator('.note-meta')).toHaveText(before.meta ?? '');
    await expect(note.locator('time')).toHaveAttribute('datetime', before.time ?? '');
  });

  test('CM-035 a saved document note is unchanged and still under its document after reloading @CM-035', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    const statement = fixture.document('Statement_128262');
    await page.goto(fixture.documentUrl(payslip));
    await page.getByLabel('New note').fill('Net pay matches the salary credits.');
    await page.getByRole('button', { name: 'Save note' }).click();
    const note = page.getByTestId('note');
    await expect(note).toHaveCount(1);
    const before = { meta: await note.locator('.note-meta').textContent(), time: await note.locator('time').getAttribute('datetime') };

    await page.reload();

    await expect(note).toHaveCount(1);
    await expect(note.locator('.note-body')).toHaveText('Net pay matches the salary credits.');
    await expect(note.locator('.note-meta')).toHaveText(before.meta ?? '');
    await expect(note.locator('time')).toHaveAttribute('datetime', before.time ?? '');
    // Still only on its own document: another document of the case doesn't show it.
    await page.goto(fixture.documentUrl(statement));
    await expect(page.getByText('No notes yet.')).toBeVisible();
    await expect(page.getByTestId('note')).toHaveCount(0);
    // And the case's notes list it against the payslip.
    await page.goto(`${fixture.caseUrl}?section=notes`);
    await expect(page.getByTestId('note').locator('.note-document')).toHaveText(payslip.filename);
  });

  test('CM-036 a note on an approved document can be added to while the document stays locked @CM-036', async ({ page, backend }) => {
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
    await note.getByRole('button', { name: 'Edit' }).click();
    await note.getByLabel('Edit note').fill('Approved: figures reconcile. Bank confirmed the employer by phone.');
    await note.getByRole('button', { name: 'Save changes' }).click();

    await expect(note.locator('.note-body')).toHaveText('Approved: figures reconcile. Bank confirmed the employer by phone.');
    await expect(note.locator('.note-edited')).toContainText('edited');
    // The document is untouched: still approved, still locked, and no request ever wrote to it.
    expect(backend.requests.filter((r) => r.method !== 'GET' && r.path.startsWith('/documents/'))).toEqual([]);
    expect(payslip.status).toBe('approved');
    await expect(page.locator('.notes-locked')).toContainText('approved and locked');
    await expect(page.getByRole('button', { name: /add field/i })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^approve/i })).toHaveCount(0);
  });
});
