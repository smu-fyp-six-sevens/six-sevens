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
