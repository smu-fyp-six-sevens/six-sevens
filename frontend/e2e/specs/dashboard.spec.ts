import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import type { CaseRisk } from '../../src/services/uploadService/types';
import { makeCase, makeDocument } from '../support/mockBackend';
import { readRegressionFixture } from '../support/fixtures';

/**
 * The /dashboard: every case (not only the officer's), a loading screen instead of the sample cases on first load,
 * and a refresh button that reads the cases again (no socket covers the whole list).
 */

// Every case since the start, on the Overview: Needs attention names each case waiting on an officer.
async function openAllTime(page: Page) {
  await page.goto('/dashboard');
  await page.getByRole('button', { name: 'Date range' }).click();
  await page.getByRole('button', { name: 'All time' }).click();
}

test.describe('Dashboard', () => {
  test("lists every case, whoever created it, and none of the sample cases", async ({ page, backend }) => {
    backend.cases.push(
      makeCase({ id: 'case-a', greenlink_case_id: 'GLDB-7001', company_name: 'Acme Pte Ltd' }),
      makeCase({ id: 'case-b', greenlink_case_id: 'GLDB-7002', company_name: 'Bolt Logistics' }),
    );

    await openAllTime(page);

    await expect(page.getByText('Acme Pte Ltd')).toBeVisible();
    await expect(page.getByText('Bolt Logistics')).toBeVisible();
    await expect(page.getByText('Meridian Consulting Group')).toHaveCount(0);
  });

  test('the refresh button reads the cases again, without the loading screen', async ({ page, backend }) => {
    backend.cases.push(makeCase({ id: 'case-a', greenlink_case_id: 'GLDB-7001', company_name: 'Acme Pte Ltd' }));
    await openAllTime(page);
    await expect(page.getByText('Acme Pte Ltd')).toBeVisible();
    const before = backend.requestsTo('GET', /^\/cases\?/).length;

    backend.cases.push(makeCase({ id: 'case-c', greenlink_case_id: 'GLDB-7003', company_name: 'Cedar Holdings' }));
    await page.getByRole('button', { name: 'Refresh cases' }).click();

    await expect(page.getByText('Cedar Holdings')).toBeVisible();
    await expect(page.getByText('Acme Pte Ltd')).toBeVisible();
    expect(backend.requestsTo('GET', /^\/cases\?/).length).toBe(before + 1);
    await expect(page.getByRole('status', { name: /Loading cases/ })).toHaveCount(0);
  });

  test('a reload stays on the open tab', async ({ page, backend }) => {
    backend.cases.push(makeCase({ id: 'case-a', greenlink_case_id: 'GLDB-7001', company_name: 'Acme Pte Ltd' }));
    await page.goto('/dashboard');
    await page.getByRole('navigation', { name: 'Dashboard sections' }).getByRole('button', { name: /^Flags/ }).click();
    await expect(page).toHaveURL(/tab=flags/);

    await page.reload();

    await expect(page.getByRole('heading', { name: 'Flags by check' })).toBeVisible();
  });

  test('the status filter is the case queue\'s: several statuses at once, with their counts', async ({ page, backend }) => {
    // Two open cases (no documents yet), one failed.
    backend.cases.push(
      makeCase({ id: 'case-a', greenlink_case_id: 'GLDB-7001', company_name: 'Acme Pte Ltd' }),
      makeCase({ id: 'case-b', greenlink_case_id: 'GLDB-7002', company_name: 'Bolt Logistics' }),
      makeCase({ id: 'case-c', greenlink_case_id: 'GLDB-7003', company_name: 'Cedar Holdings', status: 'failed' }),
    );
    backend.documents.push(makeDocument({ case_id: 'case-c', status: 'failed' }));
    await openAllTime(page);

    await page.getByRole('button', { name: 'Filter by status' }).click();
    const list = page.getByRole('group', { name: 'status filter' });
    const count = (status: string) => list.locator('.header-filter-option', { hasText: status }).locator('.header-filter-count');
    await expect(count('Awaiting Documents')).toHaveText('2');
    await expect(count('Failed')).toHaveText('1');
    await list.getByLabel('Awaiting Documents').uncheck();

    await expect(page.getByText('Cedar Holdings')).toBeVisible();
    await expect(page.getByText('Acme Pte Ltd')).toHaveCount(0);
    await expect(page.getByText('Bolt Logistics')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Filter by status (9 selected)' })).toBeVisible();
  });

  test('a custom range picked on the calendar narrows the dashboard to it', async ({ page, backend }) => {
    backend.cases.push(makeCase({ id: 'case-a', greenlink_case_id: 'GLDB-7001', company_name: 'Acme Pte Ltd', created_at: new Date().toISOString() }));
    await page.goto('/dashboard');
    await expect(page.getByText('Acme Pte Ltd')).toBeVisible();

    await page.getByRole('button', { name: 'Date range' }).click();
    await page.getByRole('button', { name: /^Custom range/ }).click();
    // The first two days of the left-hand month (last month): before the case was created today.
    const days = page.locator('.range-calendar-month').first().locator('.range-calendar-day');
    await days.nth(0).click();
    await days.nth(1).click();
    await page.getByRole('button', { name: 'Apply' }).click();

    await expect(page.getByRole('button', { name: 'Date range' })).not.toHaveText(/Last 30 days/);
    await expect(page.getByText('Acme Pte Ltd')).toHaveCount(0);
  });

  test('compare picks the first period, then a second one outside it, and shows both', async ({ page, backend }) => {
    backend.cases.push(makeCase({ id: 'case-a', greenlink_case_id: 'GLDB-7001', company_name: 'Acme Pte Ltd', created_at: new Date().toISOString() }));
    await page.goto('/dashboard');
    const compare = page.getByRole('button', { name: 'Compare two periods' });

    // Step 1 opens on the period in view (the last 30 days): keep it.
    await compare.click();
    await page.getByRole('button', { name: 'Next', exact: true }).click();

    // Step 2: the first period's days can't be picked; two months back is clear of them.
    await expect(page.getByText('First period:')).toBeVisible();
    await expect(page.locator('.range-calendar-day.today')).toBeDisabled();
    await page.getByRole('button', { name: 'Previous month' }).click();
    const days = page.locator('.range-calendar-month').first().locator('.range-calendar-day');
    await days.nth(0).click();
    await days.nth(1).click();
    await page.getByRole('button', { name: 'Compare', exact: true }).click();

    await expect(page.locator('.compare-banner')).toContainText('Comparison');
    await expect(page.locator('.compare-banner-overlap')).toHaveCount(0);
    // The banner says how to read the figures: this period's, against the earlier comparison.
    await expect(page.locator('.compare-banner-note')).toContainText('Pills show the change since');

    await page.getByRole('button', { name: 'Stop comparing' }).click();
    await expect(page.locator('.compare-banner')).toHaveCount(0);
    await expect(compare).toHaveText(/^Compare/);
  });

  test('a comparison picked after the first period becomes this period, so changes read as now against before', async ({ page, backend }) => {
    backend.cases.push(makeCase({ id: 'case-a', greenlink_case_id: 'GLDB-7001', company_name: 'Acme Pte Ltd', created_at: new Date().toISOString() }));
    await page.goto('/dashboard');
    const compare = page.getByRole('button', { name: 'Compare two periods' });

    // First: two days a couple of months back. Then two days in the month the second step opens on, later than them.
    await compare.click();
    await page.getByRole('button', { name: 'Previous month' }).click();
    await page.getByRole('button', { name: 'Previous month' }).click();
    const first = page.locator('.range-calendar-month').first().locator('.range-calendar-day:not(:disabled)');
    await first.nth(0).click();
    await first.nth(1).click();
    await page.getByRole('button', { name: 'Next', exact: true }).click();
    const second = page.locator('.range-calendar-month').first().locator('.range-calendar-day:not(:disabled)');
    await second.nth(0).click();
    await second.nth(1).click();
    await page.getByRole('button', { name: 'Compare', exact: true }).click();

    // The later pick is "This period"; the earlier one is the comparison the pills count from.
    const [thisPeriod, comparison] = await page.locator('.compare-banner-period strong').allTextContents();
    const note = page.locator('.compare-banner-note');
    await expect(note).toContainText(`Big numbers are ${thisPeriod}`);
    await expect(note).toContainText(`Pills show the change since ${comparison}`);
    await expect(page.getByRole('button', { name: 'Date range' })).toHaveText(new RegExp(thisPeriod));
  });
});

const NOW = new Date().toISOString();

test.describe('Dashboard case pipeline', () => {
  test('every stage is a step with its count, and Failed stands apart from the line', async ({ page, backend }) => {
    for (const number of [1, 2, 3]) {
      backend.cases.push(makeCase({ greenlink_case_id: `GLDB-710${number}`, status: 'pending_review', created_at: NOW }));
    }
    backend.cases.push(makeCase({ greenlink_case_id: 'GLDB-7104', status: 'documents_incomplete', created_at: NOW }));
    await page.goto('/dashboard');

    const steps = page.locator('.case-pipeline-step');
    await expect(steps).toHaveCount(10);
    const count = (stage: string) => steps.filter({ hasText: stage }).locator('.case-pipeline-count');
    await expect(count('Pending Review')).toHaveText('3');
    await expect(count('Missing Documents')).toHaveText('1');
    await expect(count('Failed')).toHaveText('0');
    await expect(steps.last()).toContainText('Failed');
    await expect(steps.last()).toHaveClass(/case-pipeline-step--apart/);
    await expect(page.locator('.case-pipeline')).toContainText('4 cases in view');
  });
});

/**
 * Sprint 5 regression (SCRUM-323), "Test Cases - Sprint 5.xlsx" Case Management sheet, Overall Case Dashboard
 * (SCRUM-297): CM-050 – CM-063, and the Agentic Evaluation sheet's precision and recall cases AE-013 – AE-015.
 * Each scored case carries a risk the backend computed (case-risk-sprint4.json), and the Accuracy tab is served the
 * figures GET /detection-metrics computes for the sheet's reviews (detection-metrics-sprint5.json, from
 * test_regression_detection_metrics.py): 10 spelling findings, 4 marked wrong; 2 font findings; a numbers check on
 * target; and a metadata check with nothing real to measure recall against.
 */
test.describe('Overall case dashboard @sprint5 @SCRUM-297', () => {
  const RISKS = readRegressionFixture<Record<string, { risk: CaseRisk }>>('case-risk-sprint4.json');
  const scored = (id: string, company: string, rsCase: string) =>
    makeCase({ id, greenlink_case_id: `GLDB-${id}`, company_name: company, status: 'case_analysed', risk: RISKS[rsCase].risk });
  // One case at each level, and one not yet analysed.
  const brightwaveCases = () => [
    scored('8001', 'Brightwave Analytics Pte Ltd', 'RS-003'),
    scored('8002', 'Northwind Freight Pte Ltd', 'RS-005'),
    scored('8003', 'Lumen Dental Clinic', 'RS-004'),
    scored('8004', 'Keppel Bay Traders', 'RS-001'),
    makeCase({ id: '8005', greenlink_case_id: 'GLDB-8005', company_name: 'Tanjong Print Works' }),
  ];
  // The Overview's Risk overview: each level's count, and its segment of the bar (titled with its cases).
  const riskCount = (page: Page, level: string) =>
    page.locator('.risk-overview-legend li', { hasText: new RegExp(`${level}$`) }).locator('strong');
  const riskSegment = (page: Page, level: string) => page.locator(`.risk-overview-bar i[title^="${level}:"]`);
  const pipelineTotal = (page: Page) => page.locator('.case-pipeline .panel-heading p');
  const attentionRow = (page: Page, caseNumber: string) => page.getByRole('button', { name: new RegExp(`^Open case ${caseNumber}:`) });

  async function showOnlyStatus(page: Page, status: string) {
    await page.getByRole('button', { name: /^Filter by status/ }).click();
    const list = page.getByRole('group', { name: 'status filter' });
    await list.getByRole('button', { name: 'Unselect all' }).click();
    await list.getByLabel(status).check();
    await page.keyboard.press('Escape');
  }

  test('CM-050 the dashboard shows the total number of cases @CM-050', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());

    await openAllTime(page);

    await expect(pipelineTotal(page)).toHaveText(/^5 cases in view/);
  });

  test('CM-051 / CM-052 cases are counted by risk level, each drawn as its share of the bar @CM-051 @CM-052', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());

    await openAllTime(page);

    for (const level of ['Critical', 'High', 'Medium', 'Low', 'Not scored']) {
      await expect(riskCount(page, level)).toHaveText('1');
      // One case of five each: equal segments.
      await expect(riskSegment(page, level)).toHaveCSS('flex-grow', '1');
    }
    await expect(riskSegment(page, 'Critical')).toHaveAttribute('title', 'Critical: GLDB-8001');
  });

  test('CM-053 the counts and chart follow the status filter @CM-053', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());
    await openAllTime(page);
    await expect(riskCount(page, 'Critical')).toHaveText('1');

    await showOnlyStatus(page, 'Awaiting Documents');

    await expect(pipelineTotal(page)).toHaveText(/^1 case in view/);
    for (const level of ['Critical', 'High', 'Medium', 'Low']) {
      await expect(riskCount(page, level)).toHaveText('0');
      await expect(riskSegment(page, level)).toHaveCount(0);
    }
    await expect(riskCount(page, 'Not scored')).toHaveText('1');
  });

  test('CM-054 a case not yet scored says so, not a risk @CM-054', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());
    await openAllTime(page);

    const unscored = attentionRow(page, 'GLDB-8005');
    await expect(unscored.locator('.attention-risk')).toHaveText('Not scored');
    await expect(unscored.locator('.attention-risk [title]')).toHaveAttribute('title', 'Shown once the case has been analysed');
    await expect(unscored.locator('.risk-score')).toHaveCount(0);
    await expect(attentionRow(page, 'GLDB-8001').locator('.risk-level')).toHaveText(RISKS['RS-003'].risk.level!);
  });

  test('CM-055 clicking a case opens its details @CM-055', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());
    await openAllTime(page);

    await attentionRow(page, 'GLDB-8001').click();

    await expect(page).toHaveURL(/\/cases\/GLDB-8001/);
    await expect(page.getByRole('heading', { name: /Brightwave Analytics Pte Ltd/ })).toBeVisible();
  });

  test('CM-056 the full case listing opens from the dashboard @CM-056', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());
    await openAllTime(page);

    await page.getByRole('link', { name: 'Open case queue' }).click();

    await expect(page).toHaveURL(/\/cases$/);
    await expect(page.getByText('Tanjong Print Works')).toBeVisible();
  });

  test('CM-057 a newly scored case is counted once the dashboard is refreshed @CM-057', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());
    await openAllTime(page);
    await expect(riskCount(page, 'High')).toHaveText('1');

    Object.assign(backend.cases.find((c) => c.id === '8005')!, { status: 'case_analysed', risk: RISKS['RS-006'].risk });
    await page.getByRole('button', { name: 'Refresh cases' }).click();

    await expect(riskCount(page, 'High')).toHaveText('2');
    await expect(riskCount(page, 'Not scored')).toHaveText('0');
  });

  test('CM-058 a deleted case leaves the counts once the dashboard is refreshed @CM-058', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());
    await openAllTime(page);
    await expect(pipelineTotal(page)).toHaveText(/^5 cases in view/);

    backend.cases = backend.cases.filter((c) => c.id !== '8001');
    await page.getByRole('button', { name: 'Refresh cases' }).click();

    await expect(pipelineTotal(page)).toHaveText(/^4 cases in view/);
    await expect(riskCount(page, 'Critical')).toHaveText('0');
  });

  test('CM-059 when no case matches the filters the dashboard says so @CM-059', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());
    await openAllTime(page);

    await showOnlyStatus(page, 'Finalised');

    await expect(pipelineTotal(page)).toHaveText(/^0 cases in view/);
    await expect(page.getByText('Nothing is waiting on an officer in this date and status selection.')).toBeVisible();
    await page.getByRole('navigation', { name: 'Dashboard sections' }).getByRole('button', { name: /^Cases/ }).click();
    await expect(page.getByText('No cases in this date and status selection.')).toBeVisible();
  });

  test.describe('Accuracy tab', () => {
    const METRICS = readRegressionFixture<{ overall: { by_check: { check_name: string }[] } }>('detection-metrics-sprint5.json');
    const checkRow = (page: Page, check: string) =>
      page.locator('.accuracy-table tbody tr', { has: page.locator('.flag-name', { hasText: new RegExp(`^${check}$`) }) });

    test.beforeEach(async ({ page, backend }) => {
      backend.detectionMetrics = structuredClone(METRICS);
      await openAllTime(page);
      await page.getByRole('navigation', { name: 'Dashboard sections' }).getByRole('button', { name: /^Accuracy/ }).click();
    });

    test('CM-060 each check shows its counts and precision: spelling 4 wrong of 10 @CM-060', async ({ page }) => {
      const spelling = checkRow(page, 'Spelling');

      await expect(spelling.locator('.review-mix-total')).toHaveText('10 reviews');
      await expect(spelling.locator('.review-mix-counts')).toContainText('6 correct');
      await expect(spelling.locator('.review-mix-counts')).toContainText('4 wrong');
      await expect(spelling.locator('[data-label="Precision"]')).toHaveText('60%');
    });

    test('CM-061 / AE-015 a check under target is flagged and shown in red; one meeting both is On target @CM-061 @AE-015', async ({ page }) => {
      await expect(checkRow(page, 'Spelling').locator('[data-label="Flags"]')).toContainText('Below target');
      await expect(checkRow(page, 'Spelling').locator('[data-label="Precision"] .rate--below')).toHaveText('60%');
      await expect(checkRow(page, 'Numerical checks').locator('[data-label="Flags"]')).toContainText('On target');
      await expect(checkRow(page, 'Numerical checks').locator('.rate--below')).toHaveCount(0);
    });

    test('CM-062 a check with fewer than 5 reviews is marked Few reviews @CM-062', async ({ page }) => {
      const font = checkRow(page, 'Font consistency');

      await expect(font.locator('.review-mix-total')).toHaveText('2 reviews');
      await expect(font.locator('[data-label="Flags"]')).toHaveText('Few reviews');
    });

    test('AE-013 precision and recall are shown for each check and the whole system @AE-013', async ({ page }) => {
      await expect(page.locator('.accuracy-table tbody tr')).toHaveCount(METRICS.overall.by_check.length);
      const overall = page.getByRole('region', { name: 'Detection accuracy overall' });
      await expect(overall.locator('.accuracy-card').nth(0)).toContainText('59%');
      await expect(overall.locator('.accuracy-card').nth(0)).toContainText('13 correct of 22 reviewed findings');
      await expect(overall.locator('.accuracy-card').nth(1)).toContainText('93%');
      await expect(overall.locator('.accuracy-card').nth(1)).toContainText('13 caught of 14 real issues');
      await expect(checkRow(page, 'Numerical checks').locator('[data-label="Recall"]')).toHaveText('83%');
    });

    test('AE-014 a check with no real issues shows recall as Not measured, never 0% or 100% @AE-014', async ({ page }) => {
      const metadata = checkRow(page, 'Metadata');

      await expect(metadata.locator('[data-label="Recall"]')).toHaveText('Not measured');
      await expect(metadata.locator('[data-label="Precision"]')).toHaveText('0%');
    });
  });
});
