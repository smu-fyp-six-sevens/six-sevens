import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { makeCase, makeDocument, makeRisk, type MockBackend } from '../support/mockBackend';
import type { CaseRiskFinding } from '../../src/services/uploadService/types';

/**
 * The Case Analysis card's earlier runs: listed newest first with who started each, its score and the change from the
 * run before, and opened read-only.
 */

const ADDRESS = 'Registered address differs between the BizFile and the bank statement.';
const PAYSLIP = 'Net pay does not match the salary credits.';
const LATEST = 'run-2';
const EARLIER = 'run-1';

const crossDocument: CaseRiskFinding = {
  document_id: 'doc-bizfile', filename: 'C001_BizFile.pdf', document_type: 'bizfile', finding_id: 'xdoc-1',
  tool: 'cross_document_analysis', severity: 'high', summary: ADDRESS, rationale: '', points: 12, unreviewed: false,
  source: 'cross_document',
};
const payslip: CaseRiskFinding = {
  document_id: 'doc-payslip', filename: 'C011_Payslip.pdf', document_type: 'payslip', finding_id: 'numerical_checks_tool-1',
  tool: 'numerical_checks_tool', severity: 'medium', summary: PAYSLIP, rationale: '', points: 8, unreviewed: false,
};
const risk = makeRisk({
  state: 'scored', level: 'High', score: 48, documents_scanned: 2, documents_total: 2,
  contributing_findings: [crossDocument, payslip],
});

function analysis(runId: string, overrides: Record<string, unknown> = {}) {
  return {
    run_id: runId, case_id: 'case-analysis', status: 'complete', fingerprint: 'f', started_at: '2026-10-27T02:00:00Z',
    finished_at: '2026-10-27T02:03:00Z', error: null, outlook: null, summary: null,
    risk, supporting_documents: [], additional_documents: null, consistent_checks: [],
    audit: { models: {}, prompt_version: '', hypotheses_tried: 0, findings_discarded: 0, tool_calls: [] },
    trace: [], stale: false,
    findings: [{
      id: 'xdoc-1', hypothesis_id: 'h1', summary: ADDRESS, rationale: 'The two addresses differ.', severity: 'high',
      category: 'cross_document_variance', citations: [], document_ids: ['doc-bizfile'], probe_summary: '', breach_flags: [],
    }],
    ...overrides,
  };
}

const latest = analysis(LATEST, { summary: 'The latest summary, in prose.' });
const earlier = analysis(EARLIER, {
  started_at: '2026-10-26T02:00:00Z', summary: 'The case was rated High risk. An older summary in prose.', stale: true,
});
const runs = [
  { run_id: LATEST, status: 'complete', started_at: '2026-10-27T02:00:00Z', finished_at: null, started_by: 'demo',
    risk_level: 'High', risk_score: 48, score_change: -24, finding_count: 1, stale: false },
  { run_id: EARLIER, status: 'complete', started_at: '2026-10-26T02:00:00Z', finished_at: null, started_by: 'bob',
    risk_level: 'High', risk_score: 72, score_change: null, finding_count: 3, stale: true },
];

async function openAnalysis(page: Page, backend: MockBackend) {
  const theCase = Object.assign(
    makeCase({ id: 'case-analysis', greenlink_case_id: 'GLDB-4004', documents_status: 'analyzed', risk }),
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
}

test.describe('Case analysis earlier runs', () => {
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
    // The earlier run shows its own result, not the latest one.
    await expect(page.locator('.analysis-summary')).toHaveText(earlier.summary as string);
    await expect(rows.nth(1).locator('.analysis-run-viewing')).toHaveText('Showing');

    await page.getByRole('button', { name: 'Back to the latest' }).click();
    await expect(page.locator('.analysis-summary')).toHaveText(latest.summary as string);
    await expect(page.getByText(/Earlier run from/)).toHaveCount(0);
  });
});
