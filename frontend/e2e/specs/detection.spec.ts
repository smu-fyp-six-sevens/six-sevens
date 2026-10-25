import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { loadFixture, readRegressionFixture } from '../support/fixtures';
import type { MockBackend } from '../support/mockBackend';

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

const approveButton = (page: Page) => page.getByRole('button', { name: '✓ Approve Document' });
const rejectButton = (page: Page) => page.getByRole('button', { name: '✕ Reject Document' });
const documentStatus = (page: Page) => page.locator('.document-status');
const detectionPanel = (page: Page) => page.locator('section', { has: page.getByRole('heading', { name: 'Detection' }) });
const extractedCard = (page: Page) => page.locator('.documents-card:not(.approved-documents-card)');
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
  test('MA-005 an approved document moves to Approved Documents with status Approved @MA-005', async ({ page, backend }) => {
    const { fixture, document } = creditReport(backend);
    await page.goto(fixture.documentUrl(document));

    await approveButton(page).click();

    await expect(page.locator('.details-label', { hasText: 'Routing Outcome' }).locator('xpath=following-sibling::strong')).toHaveText('Approved');
    expect(backend.requestsTo('POST', `/documents/${document.id}/approve`)).toHaveLength(1);
    await page.goto(`${fixture.caseUrl}?section=documents`);
    await expect(extractedCard(page)).not.toContainText(document.filename);
    // Approved documents have their own tab.
    await page.goto(`${fixture.caseUrl}?section=approved`);
    await expect(approvedCard(page)).toContainText(document.filename);
  });

  test('MA-006 a document not yet approved stays in Extracted Files @MA-006', async ({ page, backend }) => {
    const { fixture } = creditReport(backend);
    const constitution = fixture.document('Constitution');

    await page.goto(`${fixture.caseUrl}?section=documents`);

    await expect(extractedCard(page)).toContainText(constitution.filename);
    // No document is approved yet, so the Approved Documents tab has nothing to list.
    await page.goto(`${fixture.caseUrl}?section=approved`);
    await expect(page.getByText('No approved documents yet.')).toBeVisible();
    await expect(approvedCard(page)).toHaveCount(0);
  });

  test('MA-007 an approved document can no longer be edited, deleted or re-typed @MA-007', async ({ page, backend }) => {
    const { fixture, document } = creditReport(backend);
    await page.goto(fixture.documentUrl(document));

    await approveButton(page).click();
    await expect(approveButton(page)).toHaveCount(0);

    await expectLocked(page);
    await page.reload();
    await expectLocked(page);
  });

  test('MA-008 a document with an unreviewed low-confidence field cannot be approved @MA-008', async ({ page, backend }) => {
    const { fixture, payslip } = payslipPendingReview(backend);
    await page.goto(fixture.documentUrl(payslip));

    await expect(approveButton(page)).toBeDisabled();
    await expect(page.locator('.approval-warning', { hasText: 'low confidence' })).toContainText('Cannot approve yet');
    await expect(page.locator('.approval-warning', { hasText: 'low confidence' }).locator('strong').first()).toHaveText('Net Pay');
    expect(backend.requestsTo('POST', `/documents/${payslip.id}/approve`)).toHaveLength(0);
  });

  test('MA-009 detection starts as soon as the document is approved, then shows its result @MA-009', async ({ page, backend }) => {
    const { fixture, document } = creditReport(backend);
    await page.goto(fixture.documentUrl(document));

    await approveButton(page).click();

    // Approved and not yet reported on: the page says it's scanning, and offers to cancel.
    await expect(documentStatus(page)).toHaveText('Scanning…', { timeout: 5000 });
    await expect(detectionPanel(page)).toContainText('Scanning…');
    await expect(detectionPanel(page).getByRole('button', { name: 'Cancel detection' })).toBeVisible();

    document.status = 'detected';
    backend.detectionReports[document.id] = { ...C028_REPORT, document_id: document.id };
    await page.getByRole('button', { name: 'Refresh detection' }).click();
    await expect(detectionPanel(page)).toContainText('Verdict: FLAGGED');
  });

  test('MA-010 a rejected document can no longer be edited, and is shown as rejected @MA-010', async ({ page, backend }) => {
    const { fixture, document } = creditReport(backend);
    await page.goto(fixture.documentUrl(document));

    await rejectButton(page).click();

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

    expect(backend.requestsTo('POST', `/documents/${document.id}/detection/cancel`)).toHaveLength(1);
    // The worker has stopped the run and left the document approved; the page hears of it with the snapshot.
    backend.push(document.case_id!);

    await expect(documentStatus(page)).toHaveText('Detection failed');
    await expect(detectionPanel(page)).toContainText('The last detection run did not finish.');
    await expect(detectionPanel(page).getByRole('button', { name: 'Retry detection' })).toBeVisible();
    await expect(detectionPanel(page).getByRole('button', { name: 'Cancel detection' })).toHaveCount(0);
  });

  test('MA-011 detection does not run on a rejected document @MA-011', async ({ page, backend }) => {
    const { fixture, document } = creditReport(backend);
    await page.goto(fixture.documentUrl(document));

    await rejectButton(page).click();

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
    const panel = await openReport(page, backend, {
      ...C028_REPORT,
      verdict: 'clean',
      summary: 'Nothing worth flagging was found.',
      findings: [],
      second_look: null,
      unable_to_assess: [],
    });

    await expect(panel).toContainText('Verdict: CLEAN — Nothing worth flagging was found.');
    await expect(panel).toContainText('Findings (0):');
    await expect(panel).not.toContainText('Could not assess:');
  });

  test('FD-019 no findings with a check that could not run reads INCONCLUSIVE and lists the check @FD-019', async ({ page, backend }) => {
    const panel = await openReport(page, backend, {
      ...C028_REPORT,
      verdict: 'inconclusive',
      summary: 'No findings. The metadata check passed, but the fonts could not be checked.',
      findings: [],
      second_look: null,
      unable_to_assess: [{ specialist: 'layout_rendering', tool: 'font_consistency_tool', reason: 'No extractable text in this PDF' }],
    });

    await expect(panel).toContainText('Verdict: INCONCLUSIVE');
    await expect(panel).toContainText('Findings (0):');
    await expect(panel).toContainText('Could not assess:');
    await expect(panel).toContainText('layout_rendering / font_consistency_tool — No extractable text in this PDF');
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
  test('DI-045 an identity mismatch appears in detection after approval @DI-045', async ({ page, backend }) => {
    // GLDB-16's real passport is TEO KAI MING's, who is not on the certificate of incumbency.
    const fixture = loadFixture(backend, 'harbor-light');
    const passport = fixture.document('Passport_5187');
    passport.status = 'analyzed';

    const caseMatch = backend.caseMatch[fixture.case.id] as {
      documents: { document_id: string; status: string; can_proceed_to_fraud_detection: boolean }[];
    };
    const passportMatch = caseMatch.documents.find((entry) => entry.document_id === passport.id);
    expect(passportMatch).toBeDefined();
    passportMatch!.status = 'mismatch';
    passportMatch!.can_proceed_to_fraud_detection = true;

    await page.goto(fixture.documentUrl(passport));

    await expect(page.locator('.review-message--warning', { hasText: 'Case mismatch' })).toHaveCount(0);
    await approveButton(page).click();

    passport.status = 'detected';
    backend.detectionReports[passport.id] = {
      ...C028_REPORT,
      document_id: passport.id,
      verdict: 'flagged',
      summary: 'Identity field mismatch found after approval.',
      findings: [
        ...C028_REPORT.findings,
        {
          severity: 'high',
          summary: 'ID S7273147P for TEO KAI MING in Passport_5187.jpeg is not listed among IDs S7634564N, S8081681J, S8001781P in C014_CertIncumbency.pdf.',
          rationale: 'The extracted individual identifier differs from the certificate of incumbency.',
          specialists: ['identity_matching'],
        },
      ],
      consulted: [
        ...C028_REPORT.consulted,
        {
          specialist: 'identity_matching',
          title: 'Identity Matching',
          brief: 'Compare extracted names and identifiers with the other documents in this case.',
          tools_called: ['case_identity_match'],
          second_look: false,
        },
      ],
    };
    backend.push(fixture.case.id);
    await page.getByRole('button', { name: 'Refresh detection' }).click();

    const panel = detectionPanel(page);
    await expect(panel).toContainText('Verdict: FLAGGED');
    await expect(panel).toContainText('TEO KAI MING');
    await expect(panel).toContainText('S7634564N');
    await expect(panel).toContainText('Identity Matching');
  });
});
