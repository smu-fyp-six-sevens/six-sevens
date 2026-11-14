import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import type { MockBackend } from '../support/mockBackend';
import { loadFixture, readRegressionFixture } from '../support/fixtures';
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
  test('finalising the case is listed by name, not as its event type', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const caseId = fixture.case.id;
    backend.auditEvents = [
      event(caseId, '2026-10-28T14:11:16Z', 'case.finalised', { actor: 'demo', detail: { from: 'case_analysed' } }),
    ];

    await page.goto(`${fixture.caseUrl}?section=audit`);

    const entry = page.getByTestId('audit-entry');
    await expect(entry.getByTestId('audit-action')).toHaveText('Finalised case');
    await expect(entry).not.toContainText('case.finalised');
  });

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
    await expect(entries.getByTestId('audit-action')).toHaveText([
      'Uploaded document',
      'Classified and extracted document',
      'Approved document',
      'Anomaly detection run',
    ]);
    await expect(entries.getByTestId('audit-officer')).toHaveText([
      'By officer',
      'System · started by officer',
      'By K. Lim',
      'System · started by K. Lim',
    ]);
    await expect(entries.first().locator('time')).toHaveAttribute('datetime', '2026-10-21T02:00:00Z');
    await expect(entries.first().locator('time')).not.toBeEmpty();
    await expect(entries.first().getByTestId('audit-document')).toHaveText(payslip.filename);
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
    await expect(entry.getByTestId('audit-action')).toHaveText('Edited extracted fields');
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

    await expect(page.getByTestId('audit-entry').getByTestId('audit-action')).toHaveText(['Approved document']);
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
    await expect(entry.getByTestId('audit-action')).toHaveText('Created case');
    await expect(entry.locator('.audit-summary')).toHaveText('GLDB-0042 · Anchorpoint Pte Ltd');
    await expect(entry.getByTestId('audit-officer')).toHaveText('By K. Lim');
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
    await expect(entries.getByTestId('audit-document')).toHaveText([fixture.case.greenlink_case_id, payslip.filename]);
    await expect(entries.getByTestId('audit-officer')).toHaveText(['By B. Smith', 'By B. Smith']);
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
    await expect(entries.getByTestId('audit-action')).toHaveText(['Reviewed findings', 'Reviewed findings', 'Reported a missed issue']);
    await expect(entries.locator('.audit-summary')).toHaveText([
      '1 real issue(s) · 2 false alarm(s)',
      'Cross-document findings: 1 real issue(s)',
      'Against Metadata',
    ]);
    await expect(entries.getByTestId('audit-document')).toHaveText([payslip.filename, fixture.case.greenlink_case_id, payslip.filename]);
    await expect(entries.getByTestId('audit-officer')).toHaveText(['By B. Smith', 'By B. Smith', 'By B. Smith']);
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
    await expect(entries.getByTestId('audit-action')).toHaveText([
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
    await expect(entries.getByTestId('audit-officer')).toHaveText(['By K. Lim', 'By K. Lim', 'By K. Lim']);
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
    await expect(entries.first().getByTestId('audit-action')).toHaveText('Cancelled document processing');
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
    await expect(summary.getByTestId('risk-badge')).toHaveText('High Risk');
    await expect(summary.getByTestId('risk-badge')).toHaveClass(/risk-badge--high/);
    await expect(summary).toContainText('0 cross-document finding(s)');
  });

  test('says so when nothing has been recorded yet', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');

    await page.goto(`${fixture.caseUrl}?section=audit`);

    await expect(page.getByText('No audit events available yet.')).toBeVisible();
  });
});

/**
 * Sprint 5 regression (SCRUM-323), "Test Cases - Sprint 5.xlsx" Case Management sheet, Audit Trail (SCRUM-298):
 * CM-064 – CM-074. The trail is the one the real endpoints and doc-worker record for an officer's work on the
 * Anchorpoint case (audit-trail-sprint5.json, from test_regression_audit_trail.py): two utility bills uploaded, the
 * first classified, its type confirmed, a field added and one edited, the second cancelled while extracting, the
 * first approved and noted. The backend suite's officer is "test-user".
 */
test.describe('Case audit trail @sprint5 @SCRUM-298', () => {
  const TRAIL = readRegressionFixture<AuditEvent[]>('audit-trail-sprint5.json');
  const entry = (page: Page, action: string) => page.getByTestId('audit-entry').filter({ has: page.getByTestId('audit-action').filter({ hasText: new RegExp(`^${action}$`) }) });

  async function openTrail(page: Page, backend: MockBackend) {
    const fixture = loadFixture(backend, 'anchorpoint');
    backend.auditEvents = structuredClone(TRAIL);
    await page.goto(`${fixture.caseUrl}?section=audit`);
    return fixture;
  }

  test('CM-064 / CM-074 the trail is this case\'s, every action oldest first @CM-064 @CM-074', async ({ page, backend }) => {
    const fixture = await openTrail(page, backend);

    await expect(page.getByTestId('audit-entry').getByTestId('audit-action')).toHaveText([
      'Uploaded document',
      'Uploaded document',
      'Classified and extracted document',
      'Confirmed document type',
      'Added extracted field',
      'Edited extracted fields',
      'Cancelled document processing',
      'Approved document',
      'Added note',
    ]);
    expect(backend.requestsTo('GET', /^\/audit\/events\?/)[0].path).toContain(`case_id=${fixture.case.id}`);
  });

  test('CM-065 an upload shows the document, its time and the officer @CM-065', async ({ page, backend }) => {
    const fixture = await openTrail(page, backend);

    const upload = entry(page, 'Uploaded document').first();
    await expect(upload.getByTestId('audit-document')).toHaveText(fixture.document('POR_UtilityBill').filename);
    await expect(upload.locator('time')).toHaveAttribute('datetime', TRAIL[0].occurred_at);
    await expect(upload.locator('time')).not.toBeEmpty();
    await expect(upload.getByTestId('audit-officer')).toHaveText('By test-user');
  });

  test('CM-066 classification is the system\'s, started by the officer who uploaded @CM-066', async ({ page, backend }) => {
    await openTrail(page, backend);

    await expect(entry(page, 'Classified and extracted document').getByTestId('audit-officer')).toHaveText('System · started by test-user');
  });

  test('CM-067 confirming the document type is an entry by the officer @CM-067', async ({ page, backend }) => {
    const fixture = await openTrail(page, backend);

    const confirmed = entry(page, 'Confirmed document type');
    await expect(confirmed.getByTestId('audit-document')).toHaveText(fixture.document('POR_UtilityBill').filename);
    await expect(confirmed.getByTestId('audit-officer')).toHaveText('By test-user');
  });

  test('CM-068 / CM-069 adding and editing a field show the field and its values @CM-068 @CM-069', async ({ page, backend }) => {
    await openTrail(page, backend);

    await expect(entry(page, 'Added extracted field').getByTestId('audit-change')).toHaveText(['Customer Reference:(none)→KE-8801-2234']);
    // Only the field that changed, though every field was saved.
    await expect(entry(page, 'Edited extracted fields').getByTestId('audit-change')).toHaveText([
      'Individual Address:10 Serangoon Avenue #10-40 Singapore 466208→21 Anchorvale Link #08-12 Singapore 545021',
    ]);
  });

  test('CM-070 cancelling processing is an entry on that document, marked cancelled @CM-070', async ({ page, backend }) => {
    const fixture = await openTrail(page, backend);

    const cancelled = entry(page, 'Cancelled document processing');
    await expect(cancelled.getByTestId('audit-document')).toHaveText(fixture.document('Company_UtilityBill').filename);
    await expect(cancelled.locator('.audit-marker')).toHaveClass(/audit-marker--cancelled/);
    await expect(cancelled.getByTestId('audit-officer')).toHaveText('By test-user');
  });

  test('CM-071 / CM-072 a note and an approval name the officer who made them @CM-071 @CM-072', async ({ page, backend }) => {
    const fixture = await openTrail(page, backend);

    for (const action of ['Added note', 'Approved document']) {
      await expect(entry(page, action).getByTestId('audit-officer')).toHaveText('By test-user');
      await expect(entry(page, action).getByTestId('audit-document')).toHaveText(fixture.document('POR_UtilityBill').filename);
    }
  });

  test('CM-073 edits sit in time order among the other actions @CM-073', async ({ page, backend }) => {
    await openTrail(page, backend);
    await expect(page.getByTestId('audit-entry')).toHaveCount(TRAIL.length);

    const times = await page.getByTestId('audit-entry').locator('time').evaluateAll((nodes) => nodes.map((n) => n.getAttribute('datetime')!));
    expect(times).toEqual(TRAIL.map((e) => e.occurred_at));
    expect([...times].sort()).toEqual(times);
  });
});
