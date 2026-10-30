import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { makeAnalysis, makeCase, makeDocument, type MockBackend } from '../support/mockBackend';
import type {
  AnalysisSummary,
  CaseAnalysis,
  CaseAnalysisRunItem,
  CaseRisk,
  CaseRiskFinding,
} from '../../src/services/uploadService/types';

/**
 * The Case Analysis card: the structured summary (level, headline, cited reasons, checked and fine, would help), and
 * the case's earlier runs, listed newest first with who started each and the score change, each opened read-only.
 */

const ADDRESS = 'Registered address differs between the BizFile and the bank statement.';
const PAYSLIP = 'Net pay does not match the salary credits.';
const LATEST = 'run-2';
const EARLIER = 'run-1';

// A contributing finding with every field the type asks for; tests set only what they look at.
function contributing(overrides: Partial<CaseRiskFinding> & Pick<CaseRiskFinding, 'document_id' | 'finding_id'>): CaseRiskFinding {
  return {
    filename: 'document.pdf', document_type: null, tool: null, check_code: null, severity: 'medium', summary: '',
    rationale: '', points: 0, unreviewed: false, flag_id: null, category: null, source: 'document', repeat_of: null,
    also_on: 0, ...overrides,
  };
}

const crossDocument = contributing({
  document_id: 'doc-bizfile', filename: 'C001_BizFile.pdf', document_type: 'bizfile', finding_id: 'xdoc-1',
  tool: 'cross_document_analysis', severity: 'high', summary: ADDRESS, points: 12, source: 'cross_document',
});
const payslip = contributing({
  document_id: 'doc-payslip', filename: 'C011_Payslip.pdf', document_type: 'payslip', finding_id: 'numerical_checks_tool-1',
  tool: 'numerical_checks_tool', severity: 'medium', summary: PAYSLIP, points: 8,
});
const risk: CaseRisk = {
  state: 'scored', level: 'High', score: 48, points: 20, documents_scanned: 2, documents_total: 2,
  contributing_findings: [crossDocument, payslip], unassessed_checks: [], reasons: [],
};

function analysis(runId: string, overrides: Partial<CaseAnalysis> = {}): CaseAnalysis {
  return makeAnalysis({
    case_id: 'case-analysis', run_id: runId, started_at: '2026-10-27T02:00:00Z', finished_at: '2026-10-27T02:03:00Z',
    risk,
    findings: [{
      id: 'xdoc-1', hypothesis_id: 'h1', summary: ADDRESS, rationale: 'The two addresses differ.', severity: 'high',
      category: 'cross_document_variance', citations: [], document_ids: ['doc-bizfile'], probe_summary: '', breach_flags: [], confidence: 0.8,
    }],
    ...overrides,
  });
}

const SUMMARY: AnalysisSummary = {
  headline: "Two documents disagree on the company's address, and the payslip doesn't match the bank credits.",
  reasons: [
    { text: "The address on file isn't the same everywhere.", finding_id: 'xdoc-1', document_id: 'doc-bizfile' },
    { text: "Declared pay isn't reflected in the account.", finding_id: 'numerical_checks_tool-1', document_id: 'doc-payslip' },
  ],
  would_help: 'A recent utility bill would help.',
  source: 'model',
};
const EARLIER_SUMMARY: AnalysisSummary = { headline: 'The case was rated High risk.', reasons: [], would_help: null, source: 'model' };

const latest = analysis(LATEST, {
  structured_summary: SUMMARY,
  consistent_checks: ['UEN is the same on 3 documents', 'The name is the same on C001_BizFile.pdf and C002_NRIC.pdf'],
  clean_checks: ['metadata_tool'],
});
const earlier = analysis(EARLIER, { started_at: '2026-10-26T02:00:00Z', structured_summary: EARLIER_SUMMARY, stale: true });
const runs: CaseAnalysisRunItem[] = [
  { run_id: LATEST, status: 'complete', started_at: '2026-10-27T02:00:00Z', finished_at: null, started_by: 'demo',
    risk_level: 'High', risk_score: 48, score_change: -24, finding_count: 1, stale: false },
  { run_id: EARLIER, status: 'complete', started_at: '2026-10-26T02:00:00Z', finished_at: null, started_by: 'bob',
    risk_level: 'High', risk_score: 72, score_change: null, finding_count: 3, stale: true },
];

async function openAnalysis(page: Page, backend: MockBackend) {
  const theCase = Object.assign(
    makeCase({ id: 'case-analysis', greenlink_case_id: 'GLDB-4004', risk }),
    { analysis: { run_id: LATEST, status: 'complete', stale: false, finding_count: 1, finished_at: '2026-10-27T02:03:00Z' } },
  );
  backend.cases.push(theCase);
  backend.documents.push(
    makeDocument({ id: 'doc-bizfile', case_id: theCase.id, filename: 'C001_BizFile.pdf', document_type: 'bizfile', status: 'detected' }),
    makeDocument({ id: 'doc-payslip', case_id: theCase.id, filename: 'C011_Payslip.pdf', document_type: 'payslip', status: 'detected' }),
  );
  await page.route(/\/cases\/case-analysis\/analysis$/, (route) => route.fulfill({ json: latest }));
  await page.route(/\/cases\/case-analysis\/analysis\/runs$/, (route) => route.fulfill({ json: runs }));
  await page.route(/\/cases\/case-analysis\/analysis\/runs\/run-1$/, (route) => route.fulfill({ json: earlier }));
  await page.route(/\/finding-reviews$/, (route) => route.fulfill({ status: 503, json: { detail: 'off' } }));
  await page.goto(`/cases/${theCase.greenlink_case_id}?section=analysis`);
  return page.locator('.analysis-brief');
}

test.describe('Case analysis summary and earlier runs', () => {
  test('the summary is a level, a headline, cited reasons, checked-and-fine chips and what would help', async ({ page, backend }) => {
    const brief = await openAnalysis(page, backend);

    await expect(brief.locator('.analysis-brief-level')).toHaveText('High');
    await expect(brief.locator('.analysis-brief-headline strong')).toHaveText(SUMMARY.headline);
    await expect(brief.locator('.analysis-brief-reasons li')).toHaveText([
      "The address on file isn't the same everywhere.",
      "Declared pay isn't reflected in the account.",
    ]);
    await expect(brief.locator('.analysis-brief-dot')).toHaveClass([/--high/, /--medium/]);
    await expect(brief.locator('.analysis-brief-chips li')).toHaveText(['Metadata ✓', 'UEN ✓', 'Name ✓']);
    await expect(brief.locator('.analysis-brief-help a')).toHaveText('A recent utility bill would help.');
    // No score restated and no finding text pasted back in.
    await expect(brief).not.toContainText('48');
    await expect(brief).not.toContainText(ADDRESS);
  });

  test('a reason opens the finding it cites: on this card, or on the Risk Summary tab', async ({ page, backend }) => {
    const brief = await openAnalysis(page, backend);

    await brief.getByRole('button', { name: "The address on file isn't the same everywhere." }).click();
    await expect(page.locator('#analysis-finding-xdoc-1')).toBeInViewport();

    await brief.getByRole('link', { name: "Declared pay isn't reflected in the account." }).click();
    await expect(page).toHaveURL(/section=risk#risk-finding-doc-payslip-numerical_checks_tool-1$/);
    await expect(page.locator('#risk-finding-doc-payslip-numerical_checks_tool-1')).toHaveClass(/risk-finding--linked/);
  });

  test('"Would help" goes to the Additional Documents card', async ({ page, backend }) => {
    const brief = await openAnalysis(page, backend);

    await brief.getByRole('link', { name: 'A recent utility bill would help.' }).click();

    await expect(page).toHaveURL(/section=additional/);
  });

  test('earlier runs are listed newest first and open read-only, with their own summary', async ({ page, backend }) => {
    await openAnalysis(page, backend);

    const rows = page.getByTestId('analysis-run');
    await expect(rows).toHaveCount(2);
    await expect(rows.first()).toContainText('by K. Lim');
    await expect(rows.first()).toContainText('Latest');
    await expect(rows.first().locator('.analysis-run-change')).toHaveText('▼ 24');
    await expect(rows.first()).toContainText('1 cross-document finding');
    await expect(rows.nth(1)).toContainText('Out of date');
    await expect(rows.nth(1)).toContainText('3 cross-document findings');

    await rows.nth(1).getByRole('button', { name: 'Open' }).click();

    await expect(page.getByText(/Earlier run from .*read-only\./)).toBeVisible();
    await expect(page.locator('.analysis-brief-headline strong')).toHaveText(EARLIER_SUMMARY.headline);
    await expect(rows.nth(1).locator('.analysis-run-viewing')).toHaveText('Showing');

    await page.getByRole('button', { name: 'Back to the latest' }).click();
    await expect(page.locator('.analysis-brief-headline strong')).toHaveText(SUMMARY.headline);
    await expect(page.getByText(/Earlier run from/)).toHaveCount(0);
  });
});
