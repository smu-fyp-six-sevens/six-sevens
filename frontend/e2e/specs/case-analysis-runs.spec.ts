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
    rationale: '', points: 0, unreviewed: false, fingerprint: `fp-${overrides.finding_id}`, category: null, source: 'document', repeat_of: null,
    also_on: 0, feedback_ids: [], document_ids: [], cited_values: [], ...overrides,
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
  contributing_findings: [crossDocument, payslip], dismissed_findings: [], unassessed_checks: [], reasons: [],
};

function analysis(runId: string, overrides: Partial<CaseAnalysis> = {}): CaseAnalysis {
  return makeAnalysis({
    case_id: 'case-analysis', run_id: runId, started_at: '2026-10-27T02:00:00Z', finished_at: '2026-10-27T02:03:00Z',
    risk,
    findings: [{
      id: 'xdoc-1', hypothesis_id: 'h1', summary: ADDRESS, rationale: 'The two addresses differ.', severity: 'high',
      category: 'cross_document_variance', citations: [], document_ids: ['doc-bizfile'], cited_values: [], probe_summary: '', breach_flags: [], confidence: 0.8,
      fingerprint: 'fp-xdoc-1', feedback_ids: [],
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
  // A check that couldn't run on one document: the Risk Score tab's "Couldn't verify", named in "Checked and fine".
  risk: {
    ...risk,
    unassessed_checks: [{ document_id: 'doc-payslip', filename: 'C011_Payslip.pdf', tool: 'hidden_text_tool', reason: 'No text layer' }],
  },
  consistent_checks: ['UEN is the same on 3 documents', 'The name is the same on C001_BizFile.pdf and C002_NRIC.pdf'],
  clean_checks: ['metadata_tool'],
  adverse_media: {
    status: 'clean', query: 'Acme Trading Pte Ltd',
    summary: 'The web search found nothing concerning about the company in the last month.',
    sources: [], checked_at: '2026-10-27T02:03:00Z', reused: true, former_names: [],
  },
});
const earlier = analysis(EARLIER, {
  started_at: '2026-10-26T02:00:00Z', structured_summary: EARLIER_SUMMARY, stale: true, risk: { ...risk, score: 72 },
});
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
  test('the summary is a headline (the level is on the Risk tab), cited reasons, what checked out and what would help', async ({ page, backend }) => {
    const brief = await openAnalysis(page, backend);

    await expect(brief.locator('.risk-badge')).toHaveCount(0);
    await expect(brief.locator('.analysis-brief-headline strong')).toHaveText(SUMMARY.headline);
    await expect(brief.locator('.analysis-brief-reasons li')).toHaveText([
      "The address on file isn't the same everywhere.",
      "Declared pay isn't reflected in the account.",
    ]);
    await expect(brief.locator('.analysis-brief-dot')).toHaveClass([/--high/, /--medium/]);
    // Checked and fine is one line naming what couldn't be verified; what passed is folded under "See all".
    await expect(brief.locator('.analysis-brief-checked-text')).toHaveText(
      "All except Hidden Text (couldn't verify).Why?",
    );
    // Where and why opens in the text, not a hover.
    await brief.getByRole('button', { name: 'Why?' }).click();
    await expect(brief.locator('.analysis-brief-unverified-why li')).toHaveText(['Hidden Text on C011_Payslip.pdf (no text layer)']);
    await expect(brief.locator('.analysis-brief-chips')).toBeHidden();
    await brief.getByText('See all 3').click();
    await expect(brief.locator('.analysis-brief-chips li')).toHaveText([/^Metadata/, /^UEN/, /^Name/]);
    await expect(brief.locator('.analysis-brief-help a')).toHaveText('A recent utility bill would help.');
    // No score restated and no finding text pasted back in.
    await expect(brief).not.toContainText('48');
    await expect(brief).not.toContainText(ADDRESS);
  });

  test('Risk & Analysis is one page: Overview has the score, the summary and earlier runs; Findings has every finding', async ({ page, backend }) => {
    await openAnalysis(page, backend);
    const tabs = page.getByRole('navigation', { name: 'Risk and analysis' }).getByRole('button');
    const menuItem = page.getByRole('navigation', { name: 'Case sections' }).getByRole('button', { name: /^Risk & Analysis/ });

    // ?section=analysis (older links) lands on Overview: the score, then the case analysis summary.
    await expect(tabs).toHaveText(['Overview', /^Findings2/]);
    await expect(tabs.first()).toHaveAttribute('aria-current', 'page');
    await expect(menuItem).toHaveAttribute('aria-current', 'page');
    // One card: the score with why it is that score beside it, then earlier runs.
    await expect(page.locator('.summary-card h2')).toHaveText(['Case Risk Summary']);
    await expect(page.locator('.risk-overview-score .severity-gauge-value')).toHaveText('48');
    await expect(page.locator('.risk-overview .analysis-brief-why .analysis-brief-headline')).toBeVisible();
    await expect(page.locator('.summary-card .analysis-heading')).toHaveCount(0);
    await expect(page.getByRole('heading', { name: /Previous Runs/ })).toBeVisible();
    // The latest run's cross-document findings are not repeated here.
    await expect(page.locator('#analysis-finding-xdoc-1')).toHaveCount(0);

    await tabs.nth(1).click();
    await expect(page).toHaveURL(/section=findings$/);
    await expect(page.getByRole('heading', { name: 'Case Risk' })).toHaveCount(0);
    await expect(page.locator('.risk-document', { hasText: 'C011_Payslip.pdf' })).toBeVisible();
    await expect(page.locator('.risk-document', { hasText: 'Across documents' })).toBeVisible();
    await expect(menuItem).toHaveAttribute('aria-current', 'page');
  });

  test('the adverse media check shows its result as a due-diligence note', async ({ page, backend }) => {
    const brief = await openAnalysis(page, backend);

    const media = brief.locator('.analysis-brief-media');
    await expect(media).toHaveClass(/analysis-brief-media--clean/);
    await expect(media).toContainText('Media check');
    await expect(media).toContainText('nothing concerning about the company in the last month');
    // When the search ran, and that this run used the case's cached search rather than searching again.
    await expect(media.locator('.analysis-brief-media-when')).toContainText('Searched “Acme Trading Pte Ltd” on 27 Oct 2026');
    await expect(media.locator('.analysis-brief-media-when')).toContainText('(cached)');
  });

  test('an adverse media hit shows the concern and links its sources', async ({ page, backend }) => {
    const flagged = analysis(LATEST, {
      structured_summary: SUMMARY,
      adverse_media: {
        status: 'concerns', query: 'Acme Trading Pte Ltd',
        summary: 'MAS issued an enforcement notice against the company last week.',
        sources: [{ title: 'MAS enforcement notice', url: 'https://mas.gov.sg/notice' }],
        checked_at: '2026-10-27T02:03:00Z', reused: false, former_names: ['Zenith Bay Engineering Pte. Ltd.'],
      },
    });
    const brief = await openAnalysis(page, backend);
    // Registered after openAnalysis's route so this one wins (Playwright tries the newest route first).
    await page.route(/\/cases\/case-analysis\/analysis$/, (route) => route.fulfill({ json: flagged }));
    await page.goto('/cases/GLDB-4004?section=analysis');

    const media = brief.locator('.analysis-brief-media');
    await expect(media).toHaveClass(/analysis-brief-media--concerns/);
    await expect(media).toContainText('MAS issued an enforcement notice');
    await expect(media.locator('.analysis-brief-media-when')).not.toContainText('(cached)');
    // A former name searched too is named, so the officer knows the old name was covered.
    await expect(media.locator('.analysis-brief-media-when')).toContainText('Searched “Acme Trading Pte Ltd” (also as Zenith Bay Engineering Pte. Ltd.) on');
    await expect(media.getByRole('link', { name: 'MAS enforcement notice' })).toHaveAttribute(
      'href', 'https://mas.gov.sg/notice',
    );
  });

  test('a reason opens the finding it cites on the Findings tab: a cross-document one in place, a document one beside it', async ({ page, backend }) => {
    const brief = await openAnalysis(page, backend);

    await brief.getByRole('link', { name: "The address on file isn't the same everywhere." }).click();
    await expect(page).toHaveURL(/section=findings&from=overview#risk-finding-__case__-xdoc-1$/);
    await expect(page.locator('#risk-finding-__case__-xdoc-1')).toBeInViewport();

    await page.goto('/cases/GLDB-4004?section=analysis');
    await brief.getByRole('link', { name: "Declared pay isn't reflected in the account." }).click();
    await expect(page).toHaveURL(/section=findings&from=overview#risk-finding-doc-payslip-numerical_checks_tool-1$/);
    await expect(page.locator('#risk-finding-doc-payslip-numerical_checks_tool-1')).toHaveClass(/risk-finding--linked/);
  });

  test('closing a document opened from a reason returns to Overview, not the Findings tab', async ({ page, backend }) => {
    const brief = await openAnalysis(page, backend);

    await brief.getByRole('link', { name: "Declared pay isn't reflected in the account." }).click();
    const modal = page.getByRole('dialog', { name: 'Review findings on C011_Payslip.pdf' });
    await expect(modal).toBeVisible();

    await modal.getByRole('button', { name: 'Close review' }).click();

    await expect(page).toHaveURL(/section=risk$/);
    await expect(brief.locator('.analysis-brief-headline strong')).toBeVisible();
  });

  test('"Would help" goes to the Additional Documents card', async ({ page, backend }) => {
    const brief = await openAnalysis(page, backend);

    await brief.getByRole('link', { name: 'A recent utility bill would help.' }).click();

    await expect(page).toHaveURL(/section=additional/);
  });

  test('earlier runs are listed newest first and open read-only, with their own summary', async ({ page, backend }) => {
    await openAnalysis(page, backend);

    // Folded until opened, like the rows above it.
    const rows = page.getByTestId('analysis-run');
    await expect(rows).toHaveCount(0);
    await page.getByRole('button', { name: /Previous Runs/ }).click();
    await expect(rows).toHaveCount(2);
    await expect(rows.first()).toContainText('by K. Lim');
    await expect(rows.first()).toContainText('Latest');
    await expect(rows.first().locator('.analysis-run-change')).toContainText('−24');
    await expect(rows.first()).not.toContainText('cross-document');
    await expect(rows.nth(1)).toContainText('Out of date');
    await expect(rows.nth(1).locator('.analysis-run-score')).toHaveText('72');

    await rows.nth(1).getByRole('button', { name: 'Open' }).click();

    await expect(page.getByText(/Earlier run from .*read-only\./)).toBeVisible();
    await expect(page.locator('.analysis-brief-headline strong')).toHaveText(EARLIER_SUMMARY.headline);
    // The same layout as the latest: that run's own score on the left, its summary beside it.
    await expect(page.locator('.risk-overview:not(.risk-overview--single) .risk-overview-score .severity-gauge-value')).toHaveText('72');
    await expect(rows.nth(1).locator('.analysis-run-viewing')).toHaveText('Showing');

    await page.getByRole('button', { name: 'Back to the latest' }).click();
    await expect(page.locator('.analysis-brief-headline strong')).toHaveText(SUMMARY.headline);
    await expect(page.getByText(/Earlier run from/)).toHaveCount(0);
  });
});
