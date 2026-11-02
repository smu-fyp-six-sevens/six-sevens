import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { loadFixture, readFixture, readRegressionFixture } from '../support/fixtures';
import type { DocumentRow, MockBackend } from '../support/mockBackend';

/**
 * Detection (Sprint 3) — "Test Cases - Sprint 3.xlsx":
 *   Multi-Agent sheet, Trigger Detection (SCRUM-26) MA-005 – MA-012
 *   Fraud Detection sheet, Lead Detection Agent (SCRUM-119) FD-015 – FD-020
 *   Document Ingestion sheet, case match (SCRUM-110) DI-045
 *
 * The detection report shown here is the one the real detection graph
 * produces for a tampered credit report (C028), with Claude's replies
 * scripted — backend/tests/test_regression_detection.py writes it and keeps
 * it in sync. Whether the specialists find the right things on the real
 * documents is checked by the manual live run at the end of each sprint.
 */

type Report = {
  verdict: string;
  summary: string;
  findings: { severity: string; summary: string; rationale: string; specialists: string[] }[];
  consulted: { specialist: string; title: string; brief: string; tools_called: string[]; second_look: boolean }[];
  second_look: { from: string; to: string; question: string; reason: string } | null;
  unable_to_assess: { specialist: string; tool: string | null; reason: string | null }[];
  nothing_to_check: { specialist: string; tool: string | null; reason: string | null }[];
};

const C028_REPORT = readRegressionFixture<Report>('detection-report-c028.json');
// What the real graph reports for a clean certificate, and for an image-only page the font check can't read.
const CLEAN_REPORT = readRegressionFixture<Report>('detection-report-clean.json');
const INCONCLUSIVE_REPORT = readRegressionFixture<Report>('detection-report-inconclusive.json');

const approveButton = (page: Page) => page.getByRole('button', { name: '✓ Approve Document' });
const rejectButton = (page: Page) => page.getByRole('button', { name: '✕ Reject Document' });
// Rejecting is final, so the page asks first.
async function rejectDocument(page: Page) {
  await rejectButton(page).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Reject' }).click();
}
const documentStatus = (page: Page) => page.locator('.document-status');
const detectionPanel = (page: Page) => page.locator('section', { has: page.getByRole('heading', { name: 'Detection' }) });
const extractedCard = (page: Page) => page.locator('.documents-card:not(.approved-documents-card, .rejected-documents-card)');
const approvedCard = (page: Page) => page.locator('.approved-documents-card');

/** A real credit report from GLDB-02 that is ready to approve (no flagged fields). */
function creditReport(backend: MockBackend) {
  const fixture = loadFixture(backend, 'anchorpoint');
  return { fixture, document: fixture.document('Credit_Bureau_Report') };
}

/** GLDB-02's real payslip with one field still under the confidence threshold. */
function payslipPendingReview(backend: MockBackend) {
  const fixture = loadFixture(backend, 'anchorpoint');
  const payslip = fixture.document('Payslip');
  const fields = payslip.extracted_fields as Record<string, { value: unknown; confidence: number | null }>;
  fields.net_pay = { ...fields.net_pay, confidence: 0.4 };
  return { fixture, payslip };
}

async function expectLocked(page: Page) {
  await expect(page.getByRole('button', { name: '+ Add field' })).toHaveCount(0);
  await expect(page.locator('.delete-field-button')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Save fields' })).toHaveCount(0);
  await expect(page.locator('.details-type-select')).toHaveCount(0);
  await expect(approveButton(page)).toHaveCount(0);
  await expect(rejectButton(page)).toHaveCount(0);
  await page.getByRole('button', { name: 'Document actions' }).click();
  await expect(page.getByRole('menuitem', { name: 'Delete document' })).toHaveCount(0);
  await page.keyboard.press('Escape');
}

test.describe('Trigger detection @sprint3 @SCRUM-26', () => {
  test('MA-005 / MA-007 / MA-009 approving starts detection, locks the document and moves it to Approved Documents @MA-005 @MA-007 @MA-009', async ({ page, backend }) => {
    const { fixture, document } = creditReport(backend);
    await page.goto(fixture.documentUrl(document));

    await approveButton(page).click();

    // MA-009: approved and not yet reported on, the page says it's scanning, and offers to cancel.
    await expect(page.locator('.details-label', { hasText: 'Routing Outcome' }).locator('xpath=following-sibling::strong')).toHaveText('Approved');
    expect(backend.requestsTo('POST', `/documents/${document.id}/approve`)).toHaveLength(1);
    await expect(documentStatus(page)).toHaveText('Scanning…', { timeout: 5000 });
    await expect(detectionPanel(page)).toContainText('Scanning…');
    await expect(detectionPanel(page).getByRole('button', { name: 'Cancel detection' })).toBeVisible();

    // MA-007: it can no longer be edited, deleted or re-typed, before and after a reload.
    await expectLocked(page);

    document.status = 'detected';
    backend.detectionReports[document.id] = { ...C028_REPORT, document_id: document.id };
    await page.getByRole('button', { name: 'Refresh detection' }).click();
    await expect(detectionPanel(page)).toContainText('Verdict: FLAGGED');
    await page.reload();
    await expectLocked(page);

    // MA-005: it has left Uploaded Files for its own tab.
    await page.goto(`${fixture.caseUrl}?section=documents`);
    await expect(extractedCard(page)).not.toContainText(document.filename);
    await page.goto(`${fixture.caseUrl}?section=approved`);
    await expect(approvedCard(page)).toContainText(document.filename);
  });

  test('MA-006 a document not yet approved stays in Uploaded Files @MA-006', async ({ page, backend }) => {
    const { fixture } = creditReport(backend);
    const constitution = fixture.document('Constitution');

    await page.goto(`${fixture.caseUrl}?section=documents`);

    await expect(extractedCard(page)).toContainText(constitution.filename);
    // No document is approved yet, so the Approved Documents tab has nothing to list.
    await page.goto(`${fixture.caseUrl}?section=approved`);
    await expect(page.getByText('No approved documents yet.')).toBeVisible();
    await expect(approvedCard(page)).toHaveCount(0);
  });

  test('MA-008 a document with an unreviewed low-confidence field cannot be approved @MA-008', async ({ page, backend }) => {
    const { fixture, payslip } = payslipPendingReview(backend);
    await page.goto(fixture.documentUrl(payslip));

    await expect(approveButton(page)).toBeDisabled();
    await expect(page.locator('.approval-warning', { hasText: 'low confidence' })).toContainText('Cannot approve yet');
    await expect(page.locator('.approval-warning', { hasText: 'low confidence' }).locator('strong').first()).toHaveText('Net Pay');
    expect(backend.requestsTo('POST', `/documents/${payslip.id}/approve`)).toHaveLength(0);
  });

  test('MA-010 a rejected document can no longer be edited, and is shown as rejected @MA-010', async ({ page, backend }) => {
    const { fixture, document } = creditReport(backend);
    await page.goto(fixture.documentUrl(document));

    await rejectDocument(page);

    await expect(documentStatus(page)).toHaveText('Rejected');
    await expect(documentStatus(page)).toHaveClass(/document-status--rejected/);
    await expect(page.getByRole('button', { name: '+ Add field' })).toHaveCount(0);
    await expect(page.locator('.delete-field-button')).toHaveCount(0);
    await expect(page.locator('.details-type-select')).toHaveCount(0);
    await expect(approveButton(page)).toHaveCount(0);
  });

  test('a confident document with nothing to review can still have a field added, until it is approved @SCRUM-299', async ({ page, backend }) => {
    const { fixture, document } = creditReport(backend);
    await page.goto(fixture.documentUrl(document));

    // Nothing needs review here, so there is no Save fields, but the officer can add what is missing.
    await expect(page.getByRole('button', { name: 'Save fields' })).toHaveCount(0);
    await page.getByRole('button', { name: '+ Add field' }).click();
    await page.locator('#new-field-name').fill('broker_reference');
    await page.locator('#new-field-value').fill('BR-1');
    await page.getByRole('button', { name: 'Add field', exact: true }).click();

    await expect(page.locator('.extracted-table-row', { hasText: 'Broker Reference' })).toBeVisible();

    await approveButton(page).click();
    await expect(page.getByRole('button', { name: '+ Add field' })).toHaveCount(0);
  });

  test('cancelling a scan ends in "Detection failed" with a retry, not "Scanning" @SCRUM-299', async ({ page, backend }) => {
    const { fixture, document } = creditReport(backend);
    document.status = 'detecting';
    await page.goto(fixture.documentUrl(document));
    await expect(documentStatus(page)).toHaveText('Scanning…');

    await detectionPanel(page).getByRole('button', { name: 'Cancel detection' }).click();

    await expect.poll(() => backend.requestsTo('POST', `/documents/${document.id}/detection/cancel`).length).toBe(1);
    // The worker stops the run and leaves the document approved; the page hears of it with the snapshot.
    backend.push(document.case_id!);
    await expect(documentStatus(page)).toHaveText('Detection failed');
    await expect(detectionPanel(page)).toContainText('The last detection run did not finish.');
    // The stored error ("Detection failed: Cancelled by loan officer...") stays in the console.
    await expect(detectionPanel(page)).not.toContainText('Detection failed:');
    await expect(detectionPanel(page).getByRole('button', { name: 'Retry detection' })).toBeVisible();
    await expect(detectionPanel(page).getByRole('button', { name: 'Cancel detection' })).toHaveCount(0);
  });

  /** A credit report whose last detection run was cancelled: approved, with the run's error stored and no report. */
  function cancelledRun(backend: MockBackend) {
    const { fixture, document } = creditReport(backend);
    document.status = 'approved';
    document.has_detection_error = true;
    backend.detectionErrors[document.id] = 'Cancelled by loan officer';
    return { fixture, document };
  }

  test('Retry detection after a cancelled run starts it again, and it can be cancelled @SCRUM-299 @sprint5 @SCRUM-55 @MA-029', async ({ page, backend }) => {
    const { fixture, document } = cancelledRun(backend);
    await page.goto(fixture.documentUrl(document));
    await expect(documentStatus(page)).toHaveText('Detection failed');

    await detectionPanel(page).getByRole('button', { name: 'Retry detection' }).click();

    await expect.poll(() => backend.requestsTo('POST', `/documents/${document.id}/detection/retry`).length).toBe(1);
    backend.push(document.case_id!);
    await expect(documentStatus(page)).toHaveText('Scanning…');
    await expect(detectionPanel(page).getByRole('button', { name: 'Retry detection' })).toHaveCount(0);
    await expect(detectionPanel(page).getByRole('button', { name: 'Cancel detection' })).toBeVisible();
  });

  test('after a cancel whose reply arrives late, a retried run can still be cancelled @SCRUM-299', async ({ page, backend }) => {
    // REG-014: the panel sets "Cancelling…" when the cancel request answers, and clears it when the document leaves
    // detecting. With the worker's snapshot arriving first, it is set after being cleared and stays set, so the
    // retried run shows a disabled "Cancelling…" instead of "Cancel detection".
    test.fail();
    const { fixture, document } = creditReport(backend);
    document.status = 'detecting';
    let answerCancel!: () => void;
    backend.cancelGate = new Promise<void>((resolve) => (answerCancel = resolve));
    await page.goto(fixture.documentUrl(document));

    await detectionPanel(page).getByRole('button', { name: 'Cancel detection' }).click();
    await expect.poll(() => backend.requestsTo('POST', `/documents/${document.id}/detection/cancel`).length).toBe(1);
    backend.push(document.case_id!);
    await expect(documentStatus(page)).toHaveText('Detection failed');
    answerCancel();
    await detectionPanel(page).getByRole('button', { name: 'Retry detection' }).click();
    await expect.poll(() => backend.requestsTo('POST', `/documents/${document.id}/detection/retry`).length).toBe(1);
    backend.push(document.case_id!);

    // A short wait: the expected failure needn't hold the run for the default timeout.
    await expect(detectionPanel(page).getByRole('button', { name: 'Cancel detection' })).toBeVisible({ timeout: 3000 });
  });

  test('a scanned document can be run again; a cancelled rerun leaves the earlier report standing @SCRUM-299', async ({ page, backend }) => {
    const { fixture, document } = creditReport(backend);
    document.status = 'detected';
    backend.detectionReports[document.id] = { ...C028_REPORT, document_id: document.id };
    await page.goto(fixture.documentUrl(document));
    await expect(detectionPanel(page)).toContainText('Verdict: FLAGGED');
    await expect(detectionPanel(page).getByRole('button', { name: 'Cancel detection' })).toHaveCount(0);

    // Running again asks first: it costs a full run and replaces the report and findings.
    await detectionPanel(page).getByRole('button', { name: 'Run detection again' }).click();
    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toContainText('Run detection again?');
    await dialog.getByRole('button', { name: 'Run again' }).click();
    await expect.poll(() => backend.requestsTo('POST', `/documents/${document.id}/detection/retry`).length).toBe(1);

    backend.push(document.case_id!);
    await expect(documentStatus(page)).toHaveText('Scanning…');
    await expect(detectionPanel(page)).toContainText('the report below is from the previous run');
    await detectionPanel(page).getByRole('button', { name: 'Cancel detection' }).click();
    expect(backend.requestsTo('POST', `/documents/${document.id}/detection/cancel`)).toHaveLength(1);

    // The rerun stopped: the document is back to scanned, on the earlier report, which says the last run didn't finish.
    backend.push(document.case_id!);
    await expect(detectionPanel(page)).toContainText('The last run did not finish (Cancelled by loan officer)');
    await expect(detectionPanel(page)).toContainText('Verdict: FLAGGED');
    await expect(detectionPanel(page).getByRole('button', { name: 'Run detection again' })).toBeVisible();
  });

  test('a rerun that crashed says it did not finish; the raw error only goes to the console', async ({ page, backend }) => {
    const { fixture, document } = creditReport(backend);
    document.status = 'detected';
    backend.detectionReports[document.id] = { ...C028_REPORT, document_id: document.id };
    backend.detectionErrors[document.id] = "AnthropicInvalidRequestError: Error code: 400 - {'type': 'error'}";
    const logged: string[] = [];
    page.on('console', (message) => logged.push(message.text()));

    await page.goto(fixture.documentUrl(document));

    await expect(detectionPanel(page)).toContainText('The last run did not finish. The report below is from the run before it.');
    await expect(detectionPanel(page)).not.toContainText('AnthropicInvalidRequestError');
    await expect.poll(() => logged.some((line) => line.includes('AnthropicInvalidRequestError'))).toBe(true);
  });

  test('a scan still waiting for a worker can be cancelled at once @SCRUM-299', async ({ page, backend }) => {
    const { fixture, document } = creditReport(backend);
    document.status = 'approved';
    await page.goto(fixture.documentUrl(document));
    await expect(detectionPanel(page)).toContainText('Scanning…');

    await detectionPanel(page).getByRole('button', { name: 'Cancel detection' }).click();

    // Nothing was running, so there is nothing to wait for: the panel offers a retry straight away.
    await expect(detectionPanel(page).getByRole('button', { name: 'Retry detection' })).toBeVisible();
    await expect(detectionPanel(page).getByRole('button', { name: 'Cancel detection' })).toHaveCount(0);
  });

  test('MA-011 detection does not run on a rejected document @MA-011', async ({ page, backend }) => {
    const { fixture, document } = creditReport(backend);
    await page.goto(fixture.documentUrl(document));

    await rejectDocument(page);

    await expect(detectionPanel(page)).toContainText("Detection doesn't run on rejected documents.");
    await expect(detectionPanel(page).getByRole('button', { name: 'Cancel detection' })).toHaveCount(0);
    expect(backend.requestsTo('POST', `/documents/${document.id}/approve`)).toHaveLength(0);
    expect(backend.requestsTo('POST', /\/detection\//)).toHaveLength(0);
  });

  test('MA-012 once the low-confidence field is saved the document can be approved @MA-012', async ({ page, backend }) => {
    const { fixture, payslip } = payslipPendingReview(backend);
    await page.goto(fixture.documentUrl(payslip));
    await expect(approveButton(page)).toBeDisabled();

    await page.getByRole('button', { name: 'Save fields' }).click();

    await expect(page.locator('.approval-warning')).toHaveCount(0);
    await expect(approveButton(page)).toBeEnabled();
    await approveButton(page).click();
    expect(backend.requestsTo('POST', `/documents/${payslip.id}/approve`)).toHaveLength(1);
    await expect(approveButton(page)).toHaveCount(0);
  });
});

test.describe('Lead detection agent @sprint3 @SCRUM-119', () => {
  async function openReport(page: Page, backend: MockBackend, report: Report) {
    const { fixture, document } = creditReport(backend);
    document.status = 'detected';
    backend.detectionReports[document.id] = { ...report, document_id: document.id };
    await page.goto(fixture.documentUrl(document));
    await expect(detectionPanel(page)).toContainText('Verdict:');
    return detectionPanel(page);
  }

  test('FD-015 one verdict, one summary and one findings list naming each finding\'s specialist @FD-015', async ({ page, backend }) => {
    const panel = await openReport(page, backend, C028_REPORT);

    await expect(panel.getByText(/^Verdict:/)).toHaveCount(1);
    await expect(panel).toContainText(`Verdict: FLAGGED — ${C028_REPORT.summary}`);
    await expect(panel.getByText(/^Findings \(\d+\):$/)).toHaveText(`Findings (${C028_REPORT.findings.length}):`);
    for (const finding of C028_REPORT.findings) {
      await expect(panel).toContainText(`(${finding.specialists.join(', ')})`);
      await expect(panel).toContainText(finding.summary);
    }
    expect(C028_REPORT.consulted.length).toBeGreaterThanOrEqual(2);
  });

  test('FD-016 each specialist is listed with its brief and the tools it ran @FD-016', async ({ page, backend }) => {
    const panel = await openReport(page, backend, C028_REPORT);

    for (const specialist of C028_REPORT.consulted) {
      const line = `${specialist.title}${specialist.second_look ? ' (second look)' : ''} — ${specialist.brief} [tools: ${specialist.tools_called.join(', ')}]`;
      await expect(panel).toContainText(line);
    }
    await expect(panel).toContainText('Nothing to check (passed):');
    await expect(panel).toContainText(C028_REPORT.nothing_to_check[0].reason!);
  });

  test('FD-017 a report with a low, medium or high finding reads FLAGGED @FD-017', async ({ page, backend }) => {
    const panel = await openReport(page, backend, C028_REPORT);

    expect(C028_REPORT.findings.some((f) => ['low', 'medium', 'high'].includes(f.severity))).toBe(true);
    await expect(panel).toContainText('Verdict: FLAGGED');
    await expect(panel).toContainText('[low]');
    await expect(panel).toContainText('[medium]');
  });

  test('FD-018 a clean report reads CLEAN with no findings @FD-018', async ({ page, backend }) => {
    const panel = await openReport(page, backend, CLEAN_REPORT);

    await expect(panel).toContainText('Verdict: CLEAN — Nothing worth flagging was found.');
    await expect(panel).toContainText('Findings (0):');
    await expect(panel).not.toContainText('Could not assess:');
  });

  test('FD-019 no findings with a check that could not run reads INCONCLUSIVE and lists the check @FD-019', async ({ page, backend }) => {
    const panel = await openReport(page, backend, INCONCLUSIVE_REPORT);
    const [unassessed] = INCONCLUSIVE_REPORT.unable_to_assess;

    await expect(panel).toContainText('Verdict: INCONCLUSIVE');
    await expect(panel).toContainText('Findings (0):');
    await expect(panel).toContainText('Could not assess:');
    await expect(panel).toContainText(`layout_rendering / font_consistency_tool — ${unassessed.reason}`);
  });

  test('FD-020 a second look is shown once, to a different specialist, with the lead\'s question @FD-020', async ({ page, backend }) => {
    const panel = await openReport(page, backend, C028_REPORT);
    const secondLook = C028_REPORT.second_look!;

    expect(secondLook.from).not.toBe(secondLook.to);
    await expect(panel.getByText(/^Second look:/)).toHaveCount(1);
    await expect(panel).toContainText(`Second look: ${secondLook.from} → ${secondLook.to} — ${secondLook.question}`);
    await expect(panel).toContainText('Digital Authenticity Analyst (second look)');
  });
});

test.describe('Case match @sprint3 @SCRUM-110', () => {
  const triage = (page: Page) => page.locator('.review-message--warning', { hasText: 'Sent to manual review' });

  test('DI-045 a person not on the case shows their ID against the case\'s IDs @DI-045', async ({ page, backend }) => {
    // GLDB-16's real passport is TEO KAI MING's, who is not on the certificate of incumbency. The case match is
    // the one production computed when the case was captured: sent to manual review, not to detection.
    const fixture = loadFixture(backend, 'harbor-light');
    const passport = fixture.document('Passport_5187');
    passport.status = 'analyzed';

    await page.goto(fixture.documentUrl(passport));

    await expect(triage(page)).toContainText('TEO KAI MING');
    await expect(triage(page)).toContainText('ID S7273147P (TEO KAI MING)'); // the document's value
    // The case's values, from the certificate of incumbency.
    await expect(triage(page)).toContainText('(certificate of incumbency: S7634564N, S8081681J, S8001781P)');
  });

  test('a document from another company is sent to manual review, and an officer can still approve it @DI-041 @DI-043', async ({ page, backend }) => {
    // Harbor Light's statement filed into the Anchorpoint case: the case match is what the real check_case_match
    // gives it (case-match-foreign-company.json, kept in step by backend/tests/test_regression_case_match.py).
    const fixture = loadFixture(backend, 'anchorpoint');
    const foreign = readRegressionFixture<{ document: Partial<DocumentRow>; case_match: unknown }>('case-match-foreign-company.json');
    const harbor = readFixture('harbor-light');
    const statement = Object.values(harbor.documents).find((d) => d.filename === foreign.document.filename)!;
    const misfiled = { ...structuredClone(statement), ...foreign.document, status: 'analyzed' } as DocumentRow;
    backend.documents.push(misfiled);
    backend.caseMatch[fixture.case.id] = foreign.case_match;

    await page.goto(fixture.documentUrl(misfiled));

    await expect(triage(page)).toContainText('UEN 201634564N does not match the certificate of incumbency');
    await expect(approveButton(page)).toBeEnabled();
    await approveButton(page).click();
    expect(backend.requestsTo('POST', `/documents/${misfiled.id}/approve`)).toHaveLength(1);
  });
});
