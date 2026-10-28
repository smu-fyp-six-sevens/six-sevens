import { test, expect } from '../support/test';
import { loadFixture } from '../support/fixtures';
import type { AuditEvent } from '../../src/services/uploadService/types';

/**
 * The case audit trail: every recorded action on the case, oldest first, each with its time and the
 * loan officer behind it. The mock backend serves whatever entries the test gives it, newest first.
 */

function event(caseId: string, at: string, type: string, overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    event_id: `${type}-${at}`,
    type,
    occurred_at: at,
    actor_type: 'user',
    actor: 'officer',
    case_id: caseId,
    document_id: null,
    outcome: 'success',
    started_at: null,
    duration_ms: null,
    detail: {},
    ...overrides,
  };
}

test.describe('Case audit trail', () => {
  test("lists the case's actions oldest first, each with its time and loan officer", async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    const caseId = fixture.case.id;
    backend.auditEvents = [
      event(caseId, '2026-10-21T02:03:00Z', 'document.detected', {
        actor_type: 'agent', actor: 'detection-worker', document_id: payslip.id, duration_ms: 42_000,
        detail: { requested_by: 'demo', verdict: 'clean', findings: 0 },
      }),
      event(caseId, '2026-10-21T02:00:00Z', 'document.uploaded', {
        document_id: payslip.id, detail: { filename: payslip.filename },
      }),
      event(caseId, '2026-10-21T02:02:00Z', 'document.approved', { actor: 'demo', document_id: payslip.id }),
      event(caseId, '2026-10-21T02:01:00Z', 'document.analyzed', {
        actor_type: 'agent', actor: 'doc-worker', document_id: payslip.id,
        detail: { requested_by: 'officer', document_type: 'payslip' },
      }),
      event('another-case', '2026-10-21T02:04:00Z', 'document.uploaded'),
    ];

    await page.goto(`${fixture.caseUrl}?section=audit`);

    const entries = page.getByTestId('audit-entry');
    await expect(entries).toHaveCount(4);
    await expect(entries.locator('.audit-action')).toHaveText([
      'Uploaded document',
      'Classified and extracted document',
      'Approved document',
      'Fraud detection run',
    ]);
    await expect(entries.locator('.audit-officer')).toHaveText([
      'By officer',
      'System · started by officer',
      'By K. Lim',
      'System · started by K. Lim',
    ]);
    await expect(entries.first().locator('time')).toHaveAttribute('datetime', '2026-10-21T02:00:00Z');
    await expect(entries.first().locator('time')).not.toBeEmpty();
    await expect(entries.first().locator('.audit-document')).toHaveText(payslip.filename);
    expect(backend.requestsTo('GET', /^\/audit\/events\?/)[0].path).toContain(`case_id=${caseId}`);
  });

  test('a field edit shows each changed field with its old and new value', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    backend.auditEvents = [
      event(fixture.case.id, '2026-10-21T02:00:00Z', 'document.field_edited', {
        document_id: payslip.id,
        detail: {
          action: 'saved',
          fields: ['employer_uen', 'net_pay', 'remarks'],
          changes: [
            { field: 'net_pay', from: '5000', to: '5200' },
            { field: 'employer_uen', from: null, to: '201912345A' },
            { field: 'remarks', from: 'see p.2', to: null },
          ],
        },
      }),
    ];

    await page.goto(`${fixture.caseUrl}?section=audit`);

    const entry = page.getByTestId('audit-entry');
    await expect(entry.locator('.audit-action')).toHaveText('Edited extracted fields');
    await expect(entry.getByTestId('audit-change')).toHaveText([
      'Net Pay:5000→5200',
      'Employer UEN:(none)→201912345A',
      'Remarks:see p.2→(none)',
    ]);
    await expect(entry.getByTestId('audit-change').first().locator('del')).toHaveText('5000');
    await expect(entry.getByTestId('audit-change').first().locator('ins')).toHaveText('5200');
  });

  test("the app's own stuck-work recovery is left out of the trail", async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    const caseId = fixture.case.id;
    backend.auditEvents = [
      event(caseId, '2026-10-21T02:00:00Z', 'document.approved', { document_id: payslip.id }),
      event(caseId, '2026-10-21T02:20:00Z', 'recovery.requested', {
        actor_type: 'system', actor: 'sweeper', document_id: payslip.id, detail: { stage: 'detecting', attempt: 1 },
      }),
      event(caseId, '2026-10-21T02:22:00Z', 'document.recovered', {
        actor_type: 'agent', actor: 'detection-worker', document_id: payslip.id, detail: { stage: 'detecting' },
      }),
      event(caseId, '2026-10-21T02:40:00Z', 'recovery.exhausted', {
        actor_type: 'agent', actor: 'detection-worker', outcome: 'failed', document_id: payslip.id,
      }),
    ];

    await page.goto(`${fixture.caseUrl}?section=audit`);

    await expect(page.getByTestId('audit-entry').locator('.audit-action')).toHaveText(['Approved document']);
    await expect(page.locator('.audit-legend li')).toHaveText(['Loan officer', 'System', 'Failed', 'Cancelled']);
  });

  test('creating the case is the first entry, with its case number and company', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    backend.auditEvents = [
      event(fixture.case.id, '2026-10-21T01:59:00Z', 'case.created', {
        actor: 'demo', detail: { company_name: 'Anchorpoint Pte Ltd', greenlink_case_id: 'GLDB-0042' },
      }),
    ];

    await page.goto(`${fixture.caseUrl}?section=audit`);

    const entry = page.getByTestId('audit-entry');
    await expect(entry.locator('.audit-action')).toHaveText('Created case');
    await expect(entry.locator('.audit-summary')).toHaveText('GLDB-0042 · Anchorpoint Pte Ltd');
    await expect(entry.locator('.audit-officer')).toHaveText('By K. Lim');
  });

  test('a note on the whole case names the case in its pill, a note on a document names the document', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    backend.auditEvents = [
      event(fixture.case.id, '2026-10-21T02:00:00Z', 'note.added', { actor: 'bob', detail: { note_id: 'note-1' } }),
      event(fixture.case.id, '2026-10-21T02:01:00Z', 'note.added', {
        actor: 'bob', document_id: payslip.id, detail: { note_id: 'note-2' },
      }),
    ];

    await page.goto(`${fixture.caseUrl}?section=audit`);

    const entries = page.getByTestId('audit-entry');
    await expect(entries.locator('.audit-document')).toHaveText([fixture.case.greenlink_case_id, payslip.filename]);
    await expect(entries.locator('.audit-officer')).toHaveText(['By B. Smith', 'By B. Smith']);
  });

  test('an officer reviewing a document\'s findings is an entry naming the officer and the document', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    backend.auditEvents = [
      event(fixture.case.id, '2026-10-21T02:00:00Z', 'finding_review.saved', {
        actor: 'bob', document_id: payslip.id, detail: { scope: 'document', real: 1, false_alarms: 2, cleared: 0 },
      }),
      event(fixture.case.id, '2026-10-21T02:01:00Z', 'finding_review.saved', {
        actor: 'bob', detail: { scope: 'case', real: 1, false_alarms: 0, cleared: 0 },
      }),
      event(fixture.case.id, '2026-10-21T02:02:00Z', 'finding_review.missed_reported', {
        actor: 'bob', document_id: payslip.id, detail: { scope: 'document', check_name: 'metadata_tool' },
      }),
    ];

    await page.goto(`${fixture.caseUrl}?section=audit`);

    const entries = page.getByTestId('audit-entry');
    await expect(entries.locator('.audit-action')).toHaveText(['Reviewed findings', 'Reviewed findings', 'Reported a missed issue']);
    await expect(entries.locator('.audit-summary')).toHaveText([
      '1 real issue(s) · 2 false alarm(s)',
      'Cross-document findings: 1 real issue(s)',
      'Against Metadata',
    ]);
    await expect(entries.locator('.audit-document')).toHaveText([payslip.filename, fixture.case.greenlink_case_id, payslip.filename]);
    await expect(entries.locator('.audit-officer')).toHaveText(['By B. Smith', 'By B. Smith', 'By B. Smith']);
  });

  test('a list field edit shows its item counts and each item added or removed', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    backend.auditEvents = [
      event(fixture.case.id, '2026-10-21T02:00:00Z', 'document.field_edited', {
        detail: {
          action: 'saved',
          changes: [{
            field: 'accounts', from: '4 items', to: '5 items', removed: [],
            added: ['{"account_type": "Credit Card", "date_closed": "", "grantor": "test"}'],
          }, {
            field: 'registered_address', from: null, to: '{"postal_code": "049145", "street": "1 Raffles Pl"}',
          }],
        },
      }),
    ];

    await page.goto(`${fixture.caseUrl}?section=audit`);

    const [list, address] = [page.getByTestId('audit-change').first(), page.getByTestId('audit-change').nth(1)];
    await expect(list.locator('del')).toHaveText('4 items');
    await expect(list.locator('ins')).toHaveText('5 items');
    await expect(list.getByTestId('audit-list-item')).toHaveText(['+ Account Type: Credit Card · Grantor: test']);
    // An object value reads as its fields, not as JSON.
    await expect(address.locator('ins')).toHaveText('Postal Code: 049145 · Street: 1 Raffles Pl');
  });

  test('a failed run shows a plain line; the raw error only goes to the console', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    const failed = (at: string, type: string, detail: Record<string, unknown>) =>
      event(fixture.case.id, at, type, {
        actor_type: 'agent', actor: 'worker', outcome: 'failed', document_id: payslip.id,
        detail: { requested_by: 'demo', ...detail },
      });
    backend.auditEvents = [
      failed('2026-10-21T02:00:00Z', 'document.failed', { failure_reason: "Unhandled processing error: KeyError: 'net_pay'" }),
      failed('2026-10-21T02:01:00Z', 'detection.failed', { error: 'RuntimeError: tool crashed' }),
    ];

    const consoleErrors: string[] = [];
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });

    await page.goto(`${fixture.caseUrl}?section=audit`);

    const entries = page.getByTestId('audit-entry');
    await expect(entries.locator('.audit-summary')).toHaveText(['Unexpected system error', 'The run stopped before it finished']);
    await expect(page.getByText(/KeyError|RuntimeError/)).toHaveCount(0);
    await expect.poll(() => consoleErrors.join('\n')).toContain("Unhandled processing error: KeyError: 'net_pay'");
    expect(consoleErrors.join('\n')).toContain('RuntimeError: tool crashed');
  });

  test('re-running one detection check is an entry, with what it found', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    const check = (at: string, detail: Record<string, unknown>, outcome: AuditEvent['outcome'] = 'success') =>
      event(fixture.case.id, at, 'detection.check_run', { actor: 'demo', document_id: payslip.id, outcome, detail });
    backend.auditEvents = [
      check('2026-10-21T02:00:00Z', { check: 'external-validation', finding_count: 2, unable_to_assess: false }),
      check('2026-10-21T02:01:00Z', { check: 'ai-generation', likelihood: 'low' }),
      check('2026-10-21T02:02:00Z', { check: 'font-consistency', error: 'Failed to download the document.' }, 'failed'),
    ];

    await page.goto(`${fixture.caseUrl}?section=audit`);

    const entries = page.getByTestId('audit-entry');
    await expect(entries.locator('.audit-action')).toHaveText([
      'Ran External validation check',
      'Ran AI generation check',
      'Ran Font consistency check',
    ]);
    await expect(entries.locator('.audit-summary')).toHaveText([
      '2 finding(s)',
      'AI-generation likelihood: low',
      "The check couldn't run",
    ]);
    await expect(page.getByText('Failed to download the document.')).toHaveCount(0);
    await expect(entries.locator('.audit-officer')).toHaveText(['By K. Lim', 'By K. Lim', 'By K. Lim']);
    await expect(entries.nth(2).locator('.audit-outcome')).toHaveText('Failed');
  });

  test("an officer's cancel gets the cancelled dot, without a badge", async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    backend.auditEvents = [
      event(fixture.case.id, '2026-10-21T02:00:00Z', 'document.cancelled', { actor: 'bob', document_id: payslip.id }),
      event(fixture.case.id, '2026-10-21T02:01:00Z', 'document.approved', { actor: 'bob', document_id: payslip.id }),
    ];

    await page.goto(`${fixture.caseUrl}?section=audit`);

    const entries = page.getByTestId('audit-entry');
    await expect(entries.first().locator('.audit-action')).toHaveText('Cancelled document processing');
    await expect(entries.first().locator('.audit-marker')).toHaveClass(/audit-marker--cancelled/);
    await expect(entries.first().locator('.audit-outcome')).toHaveCount(0);
    await expect(entries.nth(1).locator('.audit-marker')).not.toHaveClass(/audit-marker--cancelled/);
  });

  test('a case analysis run shows its risk level first', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    backend.auditEvents = [
      event(fixture.case.id, '2026-10-21T02:00:00Z', 'case.analysis.completed', {
        actor_type: 'agent', actor: 'case-worker', duration_ms: 22_000,
        detail: { requested_by: 'bob', risk_level: 'high', findings: 0 },
      }),
    ];

    await page.goto(`${fixture.caseUrl}?section=audit`);

    const summary = page.getByTestId('audit-entry').locator('.audit-summary');
    await expect(summary.locator('.risk-badge')).toHaveText('High Risk');
    await expect(summary.locator('.risk-badge')).toHaveClass(/risk-badge--high/);
    await expect(summary).toContainText('0 cross-document finding(s)');
  });

  test('says so when nothing has been recorded yet', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');

    await page.goto(`${fixture.caseUrl}?section=audit`);

    await expect(page.getByText('No audit events available yet.')).toBeVisible();
  });

  test('is reached from the case page while reviewing the case', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');

    await page.goto(fixture.caseUrl);
    await page.getByRole('button', { name: /Audit Trail/ }).click();

    await expect(page.getByRole('heading', { name: /Case Audit Trail/ })).toBeVisible();
  });
});
