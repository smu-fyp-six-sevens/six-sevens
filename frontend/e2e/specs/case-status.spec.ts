import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { makeCase, makeDocument, type CaseRow, type MockBackend } from '../support/mockBackend';
import { REQUIRED_DOCUMENT_TYPES } from '../../src/services/uploadService/documentTypes';

/**
 * Track case status (SCRUM-80): each case moves through clear stages, and every change is recorded and shown.
 * AC1: given a case changes stage, then the stage is recorded and displayed.
 */

function seedCase(backend: MockBackend, overrides: Partial<CaseRow> = {}) {
  const found = makeCase({
    id: 'case-acme',
    greenlink_case_id: 'GLDB-1200',
    company_name: 'Acme Trading Pte Ltd',
    status: 'case_analysed',
    findings_to_review: 0,
    ...overrides,
  });
  backend.cases.push(found);
  return found;
}

const progressCard = (page: Page) => page.getByRole('region', { name: 'Case Status' });
// The stage shown on the Case Status card (the case summary).
const stageBadge = (page: Page) => progressCard(page).locator('.case-stage-badge');

test.describe('Track case status @SCRUM-80', () => {
  test('AC1 the case queue shows each case at its stage @SCRUM-80 @sprint5 @CM-089', async ({ page, backend }) => {
    seedCase(backend);
    backend.cases.push(makeCase({ id: 'case-new', greenlink_case_id: 'GLDB-1201', company_name: 'Newco Pte Ltd', status: 'fraud_detection' }));
    await page.goto('/cases');

    await expect(page.getByTestId('case-row').filter({ hasText: 'Acme Trading' }).getByTestId('case-row-status')).toHaveText('Ready to Finalise');
    await expect(page.getByTestId('case-row').filter({ hasText: 'Newco' }).getByTestId('case-row-status')).toHaveText('Detecting Anomalies');
  });

  test('AC1 the case page shows the current stage @SCRUM-80 @sprint5 @CM-086', async ({ page, backend }) => {
    seedCase(backend);
    await page.goto('/cases/GLDB-1200');

    await expect(stageBadge(page)).toHaveText('Ready to Finalise');
    await expect(progressCard(page).locator('[aria-current="step"]')).toHaveText('Ready to Finalise');
  });

  test('AC1 finalising moves the case to Finalised and locks it @SCRUM-80 @sprint5 @CM-087 @CM-090', async ({ page, backend }) => {
    seedCase(backend);
    await page.goto('/cases/GLDB-1200');

    await progressCard(page).getByRole('button', { name: 'Finalise Case' }).click();
    // Finalising asks first, since it locks the case.
    const confirm = page.getByRole('alertdialog', { name: 'Finalise GLDB-1200?' });
    await expect(confirm).toContainText('view-only');
    expect(backend.requestsTo('POST', /^\/cases\/case-acme\/finalise$/)).toHaveLength(0);
    await confirm.getByRole('button', { name: 'Finalise Case' }).click();

    await expect(stageBadge(page)).toHaveText('Finalised');
    expect(backend.requestsTo('POST', /^\/cases\/case-acme\/finalise$/)).toHaveLength(1);
    // Finalising is permanent: there is no way back, only the view-only case.
    await expect(progressCard(page).getByRole('button')).toHaveCount(0);
    await expect(progressCard(page).locator('.case-status-next')).toContainText('view-only');

    // Nothing can be uploaded to a finalised case.
    await page.goto('/cases/GLDB-1200?section=documents');
    await expect(page.getByRole('status').filter({ hasText: 'This case is view-only.' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Upload supporting documents' })).toHaveCount(0);
  });

  test('cancelling the finalise confirmation leaves the case as it is @SCRUM-80', async ({ page, backend }) => {
    seedCase(backend);
    await page.goto('/cases/GLDB-1200');

    await progressCard(page).getByRole('button', { name: 'Finalise Case' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Cancel' }).click();

    await expect(stageBadge(page)).toHaveText('Ready to Finalise');
    expect(backend.requestsTo('POST', /^\/cases\/case-acme\/finalise$/)).toHaveLength(0);
  });

  test('a finalised case is view-only, but notes can still be added @SCRUM-80', async ({ page, backend }) => {
    await page.addInitScript(() => localStorage.setItem('authUser', 'officer'));
    seedCase(backend, { status: 'finalised' });
    const document = makeDocument({
      id: 'doc-acme', case_id: 'case-acme', filename: 'bank-statement.pdf', status: 'analyzed',
      document_type: 'bank_statement', classification_confidence: 0.99, routing_status: 'accepted',
    });
    backend.documents.push(document);
    backend.notes.push({
      id: 'note-before', case_id: 'case-acme', document_id: null, parent_id: null, author: 'officer',
      body: 'Written before finalising.', created_at: '2026-10-20T03:00:00+00:00', edited_at: null, deleted_at: null,
    });

    await page.goto('/cases/GLDB-1200?section=notes');
    await expect(page.getByText('This case is view-only.')).toBeVisible();
    // Notes written before finalising stay as they were.
    const before = page.getByTestId('note').filter({ hasText: 'Written before finalising.' });
    await expect(before.getByRole('button', { name: 'Edit' })).toHaveCount(0);
    await expect(before.getByRole('button', { name: 'Delete' })).toHaveCount(0);
    await page.getByRole('textbox', { name: 'New note' }).fill('Client called after finalising.');
    await page.getByRole('button', { name: 'Save note' }).click();
    await expect(page.getByTestId('note').filter({ hasText: 'Client called after finalising.' })).toBeVisible();

    await page.goto('/cases/GLDB-1200/documents/doc-acme');
    await expect(page.getByText('This case is view-only.')).toBeVisible();
    await expect(page.getByRole('button', { name: /Approve Document/ })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /Reject Document/ })).toHaveCount(0);
    await expect(page.getByRole('textbox', { name: 'New note' })).toBeVisible();
  });

  test('a case that is not analysed cannot be finalised @SCRUM-80 @sprint5 @CM-091', async ({ page, backend }) => {
    seedCase(backend, { status: 'pending_review' });
    await page.goto('/cases/GLDB-1200');

    await expect(stageBadge(page)).toHaveText('Pending Review');
    await expect(progressCard(page).getByRole('button', { name: 'Finalise Case' })).toHaveCount(0);
  });

  test('the track shows every status by its full name @SCRUM-80', async ({ page, backend }) => {
    seedCase(backend);
    await page.goto('/cases/GLDB-1200');

    await expect(progressCard(page).locator('.case-status-step-label')).toHaveText([
      'Awaiting Documents', 'Processing Documents', 'Pending Review', 'Detecting Anomalies', 'Pending Evaluation',
      'Ready to Finalise', 'Finalised',
    ]);
  });

  test('an analysed case waits at Pending Evaluation until its findings are marked @SCRUM-80', async ({ page, backend }) => {
    seedCase(backend, { status: 'pending_evaluation', findings_to_review: 2 });
    await page.goto('/cases/GLDB-1200');

    await expect(stageBadge(page)).toHaveText('Pending Evaluation');
    await expect(progressCard(page).locator('[aria-current="step"]')).toHaveText('Pending Evaluation');
    await expect(progressCard(page).locator('.case-status-next')).toContainText('2 still need your verdict');
    // Finalising comes after the evaluation, at Case Analysed.
    await expect(progressCard(page).getByRole('button', { name: 'Finalise Case' })).toHaveCount(0);
    // The link goes to the Findings tab, where findings are marked correct or wrong.
    await progressCard(page).getByRole('button', { name: /Evaluate findings/ }).click();
    await expect(page).toHaveURL(/section=findings/);
  });

  test('a case reaches Case Analysed once its findings are evaluated @SCRUM-80', async ({ page, backend }) => {
    seedCase(backend);
    await page.goto('/cases/GLDB-1200');

    // Pending Evaluation is a finished step behind the current one.
    await expect(progressCard(page).locator('.case-status-step--done .case-status-step-label').last()).toHaveText('Pending Evaluation');
    await expect(progressCard(page).locator('[aria-current="step"]')).toHaveText('Ready to Finalise');
    await expect(progressCard(page).getByRole('button', { name: 'Finalise Case' })).toBeEnabled();
  });

  test('a case that enforces the checklist is incomplete until every required document is approved @SCRUM-80', async ({ page, backend }) => {
    // No documents approved, so every required checklist item is outstanding.
    seedCase(backend, {
      status: 'documents_incomplete',
      checklist_enforced: true,
      unapproved_requirements: REQUIRED_DOCUMENT_TYPES.map((requirement) => requirement.value),
    });
    await page.goto('/cases/GLDB-1200');

    await expect(stageBadge(page)).toHaveText('Missing Documents');
    const next = progressCard(page).locator('.case-status-next');
    await expect(next).toContainText('Every required document must be approved: 11 out of 11 not yet approved.');

    // The analysis waits for the same documents.
    await page.goto('/cases/GLDB-1200?section=analysis');
    await expect(page.getByText('11 of the 11 required documents still need approving.', { exact: false })).toBeVisible();
    await expect(page.locator('.summary-message')).toHaveCount(1);
  });
  test('a failed case says which step failed @SCRUM-80', async ({ page, backend }) => {
    seedCase(backend, { status: 'failed', failure: 'fraud_detection' });
    await page.goto('/cases/GLDB-1200');

    await expect(stageBadge(page)).toHaveText('Failed');
    await expect(progressCard(page).locator('.case-status-next')).toContainText('Anomaly detection failed');
    // The failure sits on the step where it happened, and the link goes where it can be fixed.
    await expect(progressCard(page).locator('[aria-current="step"]')).toHaveText(/Detecting Anomalies/);
    await progressCard(page).getByRole('button', { name: /View approved documents/ }).click();
    await expect(page).toHaveURL(/section=documents/);
  });
});

/**
 * Sprint 5 regression (SCRUM-323), "Test Cases - Sprint 5.xlsx" Case Management sheet, Track Case Status (SCRUM-80):
 * CM-086 – CM-092. CM-086, CM-087, CM-089, CM-090 and CM-091 are the tests above, tagged @sprint5; these cover
 * the change being recorded, and the stage reading the same wherever the case is shown.
 */
test.describe('Track case status @sprint5 @SCRUM-80', () => {
  /** The seeded analysed case, finalised from its Case Status card. */
  async function finalised(page: Page, backend: MockBackend) {
    seedCase(backend);
    await page.goto('/cases/GLDB-1200');
    await progressCard(page).getByRole('button', { name: 'Finalise Case' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Finalise Case' }).click();
    await expect(stageBadge(page)).toHaveText('Finalised');
  }

  test('CM-088 finalising is recorded in the audit trail, by the officer @CM-088', async ({ page, backend }) => {
    await finalised(page, backend);

    await page.goto('/cases/GLDB-1200?section=audit');

    const entry = page.getByTestId('audit-entry').filter({ hasText: 'Finalised case' });
    await expect(entry.getByTestId('audit-officer')).toHaveText('By officer');
  });

  test('CM-089 / CM-092 after a change the case reads the same stage on its page, the queue and the dashboard @CM-089 @CM-092', async ({ page, backend }) => {
    await finalised(page, backend);

    await page.goto('/cases');
    await expect(page.getByTestId('case-row').filter({ hasText: 'Acme Trading' }).getByTestId('case-row-status')).toHaveText('Finalised');

    // The dashboard counts it on its case pipeline, under Finalised.
    await page.goto('/dashboard');
    await page.getByRole('button', { name: 'Date range' }).click();
    await page.getByRole('button', { name: 'All time' }).click();
    const finalisedStep = page.locator('.case-pipeline-step', { has: page.locator('.case-pipeline-label', { hasText: /^Finalised$/ }) });
    await expect(finalisedStep.locator('.case-pipeline-count')).toHaveText('1');
  });
});
