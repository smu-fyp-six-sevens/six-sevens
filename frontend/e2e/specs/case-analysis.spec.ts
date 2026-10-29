import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { loadFixture, readRegressionFixture } from '../support/fixtures';
import { makeAnalysis, makeCase, makeDocument, type CaseRow, type MockBackend } from '../support/mockBackend';
import type { CaseAnalysis, CaseAnalysisTraceStep, CaseRisk } from '../../src/services/uploadService/types';

/**
 * Case analysis and the case risk score (Sprint 4) — "Test Cases - Sprint 4.xlsx":
 *   Multi-Agent sheet, Trigger case analysis (SCRUM-52) MA-013 – MA-018,
 *     Auto-trigger scoring (SCRUM-53) MA-019 – MA-023, Handle invalid agent output (SCRUM-54) MA-026 / MA-027
 *   Risk Scoring sheet, SCRUM-84 RS-001 – RS-005 and SCRUM-90 RS-006 – RS-008
 *
 * The risks and scoring traces shown here are the ones the real scoring produces for each test case's
 * documents and findings (from the live run where there was one) — backend/tests/test_regression_case_risk.py
 * writes them and keeps them in sync. The run itself (scoring after the cross-document check, one run at a
 * time) is covered by backend/tests/test_regression_case_analysis.py.
 */

const CASE_RISK = readRegressionFixture<Record<string, { risk: CaseRisk; scoring: Record<string, unknown> | null }>>(
  'case-risk-sprint4.json',
);

const runButton = (page: Page) => page.getByRole('button', { name: /^(Run case analysis|Run again|Analysing…)$/ });
const analysisCard = (page: Page) =>
  page.locator('.summary-card', { has: page.getByRole('heading', { name: 'Case Analysis' }) });
const tracePanel = (page: Page) =>
  page.locator('.debug-card', { has: page.getByRole('heading', { name: 'Case analysis trace (debug)' }) });
const riskCard = (page: Page) =>
  page.locator('.summary-card', { has: page.getByRole('heading', { name: 'Customer Risk Summary' }) });
const reviewCard = (page: Page) =>
  page.locator('.summary-card', { has: page.getByRole('heading', { name: 'Documents to Review' }) });
const sectionStatus = (page: Page, label: string) =>
  page.getByRole('navigation', { name: 'Case sections' }).getByRole('button', { name: new RegExp(`^${label}`, 'i') });

/** The C001 set the tester used, all approved; `scanning` are still in detection. */
function c001Case(backend: MockBackend, { scanning = 0 }: { scanning?: number } = {}): CaseRow {
  const theCase = makeCase({
    id: 'case-c001',
    greenlink_case_id: 'GLDB-1001',
    company_name: 'Golden Harvest Trading Pte Ltd',
    risk: null,
  });
  backend.cases.push(theCase);
  ['C001_CertIncorp.pdf', 'C001_NOA.pdf', 'C001_BankStatement_2026-06_to_2026-08.pdf'].forEach((filename, index) =>
    backend.documents.push(
      makeDocument({
        id: `doc-c001-${index + 1}`,
        case_id: theCase.id,
        filename,
        document_type: ['certificate_of_incorporation', 'notice_of_assessment', 'bank_statement'][index],
        status: index < scanning ? 'detecting' : 'detected',
        routing_status: 'accepted',
        classification_confidence: 0.97,
      }),
    ),
  );
  return theCase;
}

/** The case's run as the case-worker leaves it, then the snapshot that tells the open page. */
function finishRun(
  backend: MockBackend,
  theCase: CaseRow,
  { risk, ...overrides }: Omit<Partial<CaseAnalysis>, 'risk'> & { risk?: CaseRisk },
) {
  const run = makeAnalysis({
    ...backend.analyses[theCase.id], case_id: theCase.id, status: 'complete', finished_at: new Date().toISOString(),
    ...overrides, risk: risk ?? null,
  });
  backend.analyses[theCase.id] = run;
  theCase.analysis = {
    run_id: run.run_id,
    status: run.status,
    stale: false,
    finished_at: run.finished_at,
    finding_count: run.findings.length,
  };
  if (risk) theCase.risk = risk;
  backend.push(theCase.id);
}

function scoringTrace(testCase: string): CaseAnalysisTraceStep[] {
  const scoring = CASE_RISK[testCase].scoring;
  return [
    { step: 'run_start', at: 0 },
    { step: 'deterministic', at: 0.01 },
    { step: 'scoring', at: 0.02, ...scoring },
    { step: 'done', at: 0.03, status: 'complete' },
  ];
}

test.describe('Trigger case analysis @sprint4 @SCRUM-52', () => {
  test('MA-013 the Run Case Analysis button is hidden while a document is still being scanned @MA-013', async ({ page, backend }) => {
    const theCase = c001Case(backend, { scanning: 1 });

    await page.goto(`/cases/${theCase.greenlink_case_id}?section=analysis`);

    await expect(analysisCard(page)).toContainText('Not ready yet.');
    await expect(runButton(page)).toHaveCount(0);
    await expect(sectionStatus(page, 'Case Analysis')).toContainText('Not Ready');
  });

  test('MA-014 the Run Case Analysis button shows once every document has been scanned @MA-014', async ({ page, backend }) => {
    const theCase = c001Case(backend);

    await page.goto(`/cases/${theCase.greenlink_case_id}?section=analysis`);

    await expect(runButton(page)).toHaveText('Run case analysis');
    await expect(runButton(page)).toBeEnabled();
    await expect(sectionStatus(page, 'Case Analysis')).toContainText('Ready to Run');
  });

  test('MA-015 clicking Run Case Analysis starts the analysis for this case @MA-015', async ({ page, backend }) => {
    const theCase = c001Case(backend);
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=analysis`);

    await runButton(page).click();

    await expect.poll(() => backend.requestsTo('POST', `/cases/${theCase.id}/analysis/run`).length).toBe(1);
    backend.push(theCase.id);
    await expect(runButton(page)).toHaveText('Analysing…');
    await expect(runButton(page)).toBeDisabled();
    await expect(sectionStatus(page, 'Case Analysis')).toContainText('Running');
    await expect(tracePanel(page)).toContainText(`run_id: ${backend.analyses[theCase.id].run_id}`);
    await expect(tracePanel(page)).toContainText('status: running');
  });

  test('MA-016 the button cannot start a second run while one is going @MA-016', async ({ page, backend }) => {
    const theCase = c001Case(backend);
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=analysis`);
    await runButton(page).click();
    await expect.poll(() => backend.requestsTo('POST', `/cases/${theCase.id}/analysis/run`).length).toBe(1);
    backend.push(theCase.id);

    await expect(runButton(page)).toBeDisabled();
    await runButton(page).click({ force: true });
    await runButton(page).click({ force: true });

    expect(backend.requestsTo('POST', `/cases/${theCase.id}/analysis/run`)).toHaveLength(1);
    expect(Object.keys(backend.analyses)).toEqual([theCase.id]);
  });

  test('MA-017 clicking again before the page hears the run started shows that it is already running @MA-017', async ({ page, backend }) => {
    const theCase = c001Case(backend);
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=analysis`);

    // No snapshot in between: the button is clickable again as soon as the first request answers.
    await runButton(page).click();
    await expect(runButton(page)).toBeEnabled();
    const firstRun = backend.analyses[theCase.id].run_id;
    await runButton(page).click();

    await expect(analysisCard(page).getByRole('alert')).toContainText('An analysis is already running for this case.');
    expect(backend.requestsTo('POST', `/cases/${theCase.id}/analysis/run`)).toHaveLength(2);
    // Still the one run.
    expect(backend.analyses[theCase.id].run_id).toBe(firstRun);
  });

  test('MA-018 a case analysis that fails says so @MA-018', async ({ page, backend }) => {
    const theCase = c001Case(backend);
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=analysis`);
    await runButton(page).click();
    await expect.poll(() => backend.requestsTo('POST', `/cases/${theCase.id}/analysis/run`).length).toBe(1);

    finishRun(backend, theCase, { status: 'failed', error: 'RuntimeError: scoring unavailable' });

    await expect(analysisCard(page)).toContainText('The analysis did not finish.');
    await expect(analysisCard(page)).toContainText('RuntimeError: scoring unavailable');
    await expect(sectionStatus(page, 'Case Analysis')).toContainText('Did Not Finish');
    await expect(runButton(page)).toHaveText('Run again');
    await expect(tracePanel(page)).toContainText('status: failed');
  });
  test('a run whose documents changed since it ran says it is out of date and offers to run again @SCRUM-52', async ({ page, backend }) => {
    const theCase = c001Case(backend);
    // The backend marks a run stale when its fingerprint no longer matches the case's documents (state.py), on
    // both reads: the case summary's overview and GET .../analysis.
    const run = makeAnalysis({ case_id: theCase.id, risk: CASE_RISK['RS-005'].risk, stale: true });
    backend.analyses[theCase.id] = run;
    theCase.analysis = { run_id: run.run_id, status: 'complete', stale: true, finished_at: run.finished_at, finding_count: 0 };

    await page.goto(`/cases/${theCase.greenlink_case_id}?section=analysis`);

    await expect(analysisCard(page)).toContainText('Out of date.');
    await expect(analysisCard(page)).toContainText('The documents changed since the last run: run again to update the score.');
    await expect(sectionStatus(page, 'Case Analysis')).toContainText('Out of Date');
    await expect(runButton(page)).toHaveText('Run again');
    await expect(tracePanel(page)).toContainText('status: complete (stale)');
  });
});

test.describe('Auto-trigger scoring @sprint4 @SCRUM-53', () => {
  test('MA-019 while the cross-document check runs the trace shows the run going and no scoring @MA-019', async ({ page, backend }) => {
    const theCase = c001Case(backend);
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=analysis`);
    await runButton(page).click();
    await expect.poll(() => backend.requestsTo('POST', `/cases/${theCase.id}/analysis/run`).length).toBe(1);
    backend.analyses[theCase.id].trace = [{ step: 'run_start', at: 0 }, { step: 'dossier', at: 0.01 }, { step: 'deterministic', at: 0.02 }];

    await tracePanel(page).getByRole('button', { name: 'Refresh case analysis trace' }).click();

    await expect(tracePanel(page)).toContainText('status: running');
    await expect(tracePanel(page)).toContainText('finished_at: —');
    await expect(tracePanel(page).getByText('Scoring', { exact: true })).toHaveCount(0);
  });

  test('MA-020 / MA-021 after the check, the scoring includes the cross-document findings @MA-020 @MA-021', async ({ page, backend }) => {
    const theCase = c001Case(backend);
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=analysis`);
    await runButton(page).click();
    await expect.poll(() => backend.requestsTo('POST', `/cases/${theCase.id}/analysis/run`).length).toBe(1);

    finishRun(backend, theCase, { risk: CASE_RISK['RS-003'].risk, trace: scoringTrace('RS-003') });

    const trace = tracePanel(page);
    await expect(trace).toContainText('status: complete');
    // The live run's figures (RS-003): the cross-document finding is scored with the rest, marked "(case)".
    await expect(trace).toContainText('Critical · score 89/100 · 60 points from 7 finding(s) on 5/5 documents');
    await expect(trace).toContainText('without cross-document findings: Critical · 87 (1 added)');
    const caseRow = trace.locator('.debug-table tbody tr', { hasText: '(case)' });
    await expect(caseRow).toHaveCount(1);
    await expect(caseRow.locator('td').nth(2)).toHaveText('cross_document_variance');
    await expect(caseRow.locator('td').nth(3)).toHaveText('cross_document_analysis');
  });

  test('MA-023 a failed cross-document check is shown, and the case is not scored @MA-023', async ({ page, backend }) => {
    const theCase = c001Case(backend);
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=analysis`);
    await runButton(page).click();
    await expect.poll(() => backend.requestsTo('POST', `/cases/${theCase.id}/analysis/run`).length).toBe(1);

    finishRun(backend, theCase, {
      status: 'failed',
      error: 'RuntimeError: cross-document check failed',
      trace: [{ step: 'run_start', at: 0 }, { step: 'error', at: 0.01, error: 'RuntimeError: cross-document check failed' }],
    });

    await expect(analysisCard(page)).toContainText('The analysis did not finish.');
    await expect(analysisCard(page)).toContainText('RuntimeError: cross-document check failed');
    await expect(tracePanel(page).getByText('Scoring', { exact: true })).toHaveCount(0);
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=risk`);
    await expect(riskCard(page)).toContainText('Not scored yet.');
    await expect(riskCard(page).locator('.risk-badge')).toHaveCount(0);
  });
});

test.describe('Case risk score @sprint4 @SCRUM-84 @SCRUM-90', () => {
  async function openRisk(page: Page, backend: MockBackend, testCase: string) {
    const theCase = makeCase({ greenlink_case_id: `GLDB-4${testCase.slice(-3)}`, risk: CASE_RISK[testCase].risk });
    backend.cases.push(theCase);
    if (CASE_RISK[testCase].scoring) {
      const run = makeAnalysis({ case_id: theCase.id, risk: CASE_RISK[testCase].risk, trace: scoringTrace(testCase) });
      backend.analyses[theCase.id] = run;
      theCase.analysis = { run_id: run.run_id, status: 'complete', stale: false, finished_at: run.finished_at, finding_count: 0 };
    }
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=risk`);
    return theCase;
  }

  const openTrace = (page: Page, theCase: CaseRow) => page.goto(`/cases/${theCase.greenlink_case_id}?section=analysis`);

  test('RS-001 a clean case is scored Low at 0 with no findings @RS-001', async ({ page, backend }) => {
    await openRisk(page, backend, 'RS-001');

    const card = riskCard(page);
    await expect(card.locator('.risk-badge')).toHaveText('Low risk');
    await expect(card.locator('.risk-score-value')).toHaveText('0');
    await expect(card).toContainText('No contributing findings.');
    await expect(card.locator('.risk-reason')).toHaveText('Rated Low: detection found nothing worth flagging.');
    await expect(card).toContainText('Based on 3 documents');
  });

  test('RS-002 a failed detection run shows no score, says detection failed and how many were scanned @RS-002', async ({ page, backend }) => {
    await openRisk(page, backend, 'RS-002');

    const card = riskCard(page);
    await expect(card).toContainText('Detection failed on a document.');
    await expect(card).toContainText('2 of 3 documents scanned.');
    await expect(card.locator('.risk-score-value')).toHaveCount(0);
    await expect(card.locator('.risk-badge')).toHaveCount(0);
    await expect(sectionStatus(page, 'Risk Summary')).toContainText('Detection Failed');
  });

  test('RS-003 high findings on 3 or more documents are Critical, with the reason @RS-003', async ({ page, backend }) => {
    await openRisk(page, backend, 'RS-003');

    const card = riskCard(page);
    await expect(card.locator('.risk-badge')).toHaveText('Critical risk');
    await expect(card.locator('.risk-score-value')).toHaveText('89');
    await expect(card.locator('.risk-score-level')).toHaveText('Critical risk · 80–100');
    await expect(card.locator('.risk-reason')).toHaveText('Rated Critical: high-severity findings on 4 documents.');
    await expect(sectionStatus(page, 'Risk Summary')).toContainText('Critical · 89');
  });

  test('RS-004 a single medium finding is Medium, worth 3 points @RS-004', async ({ page, backend }) => {
    const theCase = await openRisk(page, backend, 'RS-004');

    const card = riskCard(page);
    await expect(card.locator('.risk-badge')).toHaveText('Medium risk');
    await expect(card.locator('.risk-score-level')).toHaveText('Medium risk · 25–49');
    await expect(card).toContainText('1 medium');
    await openTrace(page, theCase);
    const row = tracePanel(page).locator('.debug-table tbody tr');
    await expect(row).toHaveCount(1);
    await expect(row.locator('td')).toHaveText(['3', 'medium', 'tampering_forensic', 'layout_anomalies_tool', 'TanAhKow_NRIC.pdf']);
  });

  test('RS-005 a single high finding is High at 50 @RS-005', async ({ page, backend }) => {
    await openRisk(page, backend, 'RS-005');

    const card = riskCard(page);
    await expect(card.locator('.risk-badge')).toHaveText('High risk');
    await expect(card.locator('.risk-score-value')).toHaveText('50');
    await expect(card.locator('.risk-reason')).toHaveText('Rated High from the combined severity of 1 finding(s).');
  });

  test('RS-006 each finding shows its document, type, check, severity, summary, rationale and points @RS-006', async ({ page, backend }) => {
    const theCase = await openRisk(page, backend, 'RS-006');

    const payslip = reviewCard(page).locator('.risk-document', { hasText: 'C011_Payslip.pdf' });
    await expect(payslip.locator('.risk-document-meta')).toContainText('Payslip');
    await payslip.getByRole('button').click();
    const row = page.getByRole('dialog', { name: 'Review findings on C011_Payslip.pdf' }).locator('.risk-findings li');
    await expect(row.locator('.risk-severity')).toHaveText('high');
    await expect(row.locator('.risk-finding-check')).toHaveText('Font consistency');
    await expect(row.locator('.risk-finding-summary')).toHaveText('Net pay is in a different font from the rest of the payslip.');
    await expect(row.locator('.risk-finding-rationale')).toHaveText(
      'Only the figure that matters differs, which is how a pasted value looks.',
    );
    // Points are listed per finding in the case analysis trace's scoring table.
    await openTrace(page, theCase);
    await expect(tracePanel(page).locator('.debug-table tbody tr').first().locator('td')).toHaveText([
      '8', 'high', 'tampering_forensic', 'font_consistency_tool', 'C011_Payslip.pdf',
    ]);
  });

  test('RS-007 findings are listed high first, then medium, then low @RS-007', async ({ page, backend }) => {
    const theCase = await openRisk(page, backend, 'RS-006');

    // Every document with findings is still to review, low-only ones included; the empty cross-document group sits under Reviewed.
    const documents = reviewCard(page).locator(':scope > .flagged-list .risk-document');
    await expect(documents).toHaveCount(3);
    await expect(documents.locator('.risk-document-heading strong')).toHaveText([
      'C011_Payslip.pdf', 'C011_BankStatement.pdf', 'C011_CertIncorp.pdf',
    ]);
    await expect(documents.locator('.risk-document-meta .risk-severity')).toHaveText(['1 high', '1 medium', '1 low']);
    await openTrace(page, theCase);
    await expect(tracePanel(page).locator('.debug-table tbody tr td:first-child')).toHaveText(['8', '3', '1']);
  });

  test('RS-008 / CM-007 the risk summary is reached from case details, and an unscored case shows no score and no findings @RS-008 @CM-007 @sprint2 @SCRUM-83', async ({ page, backend }) => {
    const theCase = makeCase({ greenlink_case_id: 'GLDB-4008', risk: null });
    backend.cases.push(theCase);
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=documents`);

    await sectionStatus(page, 'Risk Summary').click();

    const card = riskCard(page);
    await expect(card).toContainText('Not scored yet.');
    await expect(card.locator('.risk-badge')).toHaveCount(0);
    await expect(card).toContainText('Run the case analysis once every approved document has been scanned.');
    await expect(card.locator('.risk-score-value')).toHaveCount(0);
    await expect(card.locator('.risk-document')).toHaveCount(0);
  });

  test('RS-008 / CM-007 a case with 1 of 2 documents scanned shows the count and no findings @RS-008 @CM-007 @sprint2 @SCRUM-83', async ({ page, backend }) => {
    await openRisk(page, backend, 'RS-008');

    const card = riskCard(page);
    await expect(card).toContainText('1 of 2 documents scanned.');
    await expect(sectionStatus(page, 'Risk Summary')).toContainText('1/2 Scanned');
    await expect(card.locator('.risk-badge')).toHaveCount(0);
    await expect(card.locator('.risk-score-value')).toHaveCount(0);
    await expect(card.locator('.risk-document')).toHaveCount(0);
  });
});

test.describe('Handle invalid agent output @sprint4 @SCRUM-54', () => {
  const REASON =
    'Extraction output could not be used: Extraction did not return valid JSON. Its response was: ' +
    'Sure! Here are the fields I found on this payslip: the employee is paid monthly.';

  function withFailedPayslip(backend: MockBackend) {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    Object.assign(payslip, { status: 'failed', failure_reason: REASON, extracted_fields: null, extraction_confidence: null });
    return { fixture, payslip };
  }

  test('MA-026 the document is marked Failed and its page shows why @MA-026', async ({ page, backend }) => {
    const { fixture, payslip } = withFailedPayslip(backend);

    await page.goto(`${fixture.caseUrl}?section=documents`);
    await expect(page.getByRole('button', { name: `View ${payslip.filename}` })).toContainText('Failed');
    await expect(page.getByRole('button', { name: `Retry ${payslip.filename}` })).toBeVisible();

    await page.goto(fixture.documentUrl(payslip));
    await expect(page.locator('.document-status')).toContainText('Failed');
    await expect(page.locator('.details-failure-reason')).toContainText(REASON);
  });

  test('MA-027 the other documents carry on and the failed one can be retried @MA-027', async ({ page, backend }) => {
    const { fixture, payslip } = withFailedPayslip(backend);
    const others = fixture.documents.filter((d) => d.id !== payslip.id);

    await page.goto(fixture.documentUrl(payslip));
    await page.getByRole('button', { name: '↻ Retry Document' }).click();

    await expect.poll(() => backend.requestsTo('POST', `/documents/${payslip.id}/retry`).length).toBe(1);
    expect(payslip.status).toBe('queued');
    expect(payslip.failure_reason).toBeNull();
    // Only the failed document went back into the pipeline.
    expect(others.every((d) => d.status !== 'failed' && d.status !== 'queued')).toBe(true);
    expect(backend.requests.filter((r) => r.method === 'POST' && r.path.endsWith('/retry'))).toHaveLength(1);
  });
});
