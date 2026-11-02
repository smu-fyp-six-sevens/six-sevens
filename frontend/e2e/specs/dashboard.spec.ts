import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import type { CaseRisk } from '../../src/services/uploadService/types';
import { makeCase, makeDocument } from '../support/mockBackend';
import { readRegressionFixture } from '../support/fixtures';

/**
 * The /dashboard: every case (not only the officer's), a loading screen instead of the sample cases on first load,
 * and a refresh button that reads the cases again (no socket covers the whole list).
 */

const casesTab = (page: Page) => page.getByRole('navigation', { name: 'Dashboard sections' }).getByRole('button', { name: /^Cases/ });

async function openCases(page: Page) {
  await page.goto('/dashboard');
  await page.getByLabel('Date range').selectOption('All time');
  await casesTab(page).click();
}

test.describe('Dashboard', () => {
  test("lists every case, whoever created it, and none of the sample cases", async ({ page, backend }) => {
    backend.cases.push(
      makeCase({ id: 'case-a', greenlink_case_id: 'GLDB-7001', company_name: 'Acme Pte Ltd' }),
      makeCase({ id: 'case-b', greenlink_case_id: 'GLDB-7002', company_name: 'Bolt Logistics' }),
    );

    await openCases(page);

    await expect(page.getByText('Acme Pte Ltd')).toBeVisible();
    await expect(page.getByText('Bolt Logistics')).toBeVisible();
    await expect(page.getByText('Meridian Consulting Group')).toHaveCount(0);
  });

  test('the refresh button reads the cases again, without the loading screen', async ({ page, backend }) => {
    backend.cases.push(makeCase({ id: 'case-a', greenlink_case_id: 'GLDB-7001', company_name: 'Acme Pte Ltd' }));
    await openCases(page);
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

    await expect(page.getByRole('heading', { name: 'Recurring findings' })).toBeVisible();
  });

  test('the status filter is the case queue\'s: several statuses at once, with their counts', async ({ page, backend }) => {
    // Two open cases (no documents yet), one failed.
    backend.cases.push(
      makeCase({ id: 'case-a', greenlink_case_id: 'GLDB-7001', company_name: 'Acme Pte Ltd' }),
      makeCase({ id: 'case-b', greenlink_case_id: 'GLDB-7002', company_name: 'Bolt Logistics' }),
      makeCase({ id: 'case-c', greenlink_case_id: 'GLDB-7003', company_name: 'Cedar Holdings', status: 'failed' }),
    );
    backend.documents.push(makeDocument({ case_id: 'case-c', status: 'failed' }));
    await openCases(page);

    await page.getByRole('button', { name: 'Filter by status' }).click();
    const list = page.getByRole('group', { name: 'status filter' });
    const count = (status: string) => list.locator('.header-filter-option', { hasText: status }).locator('.header-filter-count');
    await expect(count('Open')).toHaveText('2');
    await expect(count('Failed')).toHaveText('1');
    await list.getByLabel('Open').uncheck();

    await expect(page.getByText('Cedar Holdings')).toBeVisible();
    await expect(page.getByText('Acme Pte Ltd')).toHaveCount(0);
    await expect(page.getByText('Bolt Logistics')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Filter by status (9 selected)' })).toBeVisible();
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
  const riskRow = (page: Page, level: string) => page.locator('.risk-snapshot-row', { hasText: `${level} risk` });
  const casesInView = (page: Page) => page.locator('.stat-total .stat-number');

  async function openDashboard(page: Page) {
    await page.goto('/dashboard');
    await page.getByLabel('Date range').selectOption('All time');
  }

  async function showOnlyStatus(page: Page, status: string) {
    await page.getByRole('button', { name: /^Filter by status/ }).click();
    const list = page.getByRole('group', { name: 'status filter' });
    await list.getByRole('button', { name: 'Unselect all' }).click();
    await list.getByLabel(status).check();
    await page.keyboard.press('Escape');
  }

  test('CM-050 the dashboard shows the total number of cases @CM-050', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());

    await openDashboard(page);

    await expect(casesInView(page)).toHaveText('5');
    await expect(page.locator('.stat-total')).toContainText('Cases in view');
  });

  test('CM-051 / CM-052 cases are counted by risk level, each drawn as a bar of the whole @CM-051 @CM-052', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());

    await openDashboard(page);

    for (const level of ['Critical', 'High', 'Medium', 'Low']) {
      await expect(riskRow(page, level).locator('strong')).toHaveText('1');
      // One case of five: a fifth of the bar.
      await expect(riskRow(page, level).locator('.risk-snapshot-track i')).toHaveAttribute('style', 'width: 20%;');
    }
  });

  test('CM-053 the counts and chart follow the status filter @CM-053', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());
    await openDashboard(page);
    await expect(riskRow(page, 'Critical').locator('strong')).toHaveText('1');

    await showOnlyStatus(page, 'Open');

    await expect(casesInView(page)).toHaveText('1');
    for (const level of ['Critical', 'High', 'Medium', 'Low']) {
      await expect(riskRow(page, level).locator('strong')).toHaveText('0');
    }
  });

  test('CM-054 a case not yet scored shows a dash that says why, not a risk @CM-054', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());
    await openCases(page);

    const unscored = page.getByRole('link', { name: 'Open case GLDB-8005' });
    await expect(unscored.locator('[data-label="Risk"] .processing-pending')).toHaveAttribute('title', 'Shown once the case has been analysed');
    await expect(unscored.locator('.risk-score')).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Open case GLDB-8001' }).locator('.risk-level')).toHaveText(RISKS['RS-003'].risk.level!);
  });

  test('CM-055 clicking a case opens its details @CM-055', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());
    await openCases(page);

    await page.getByRole('link', { name: 'Open case GLDB-8001' }).click();

    await expect(page).toHaveURL(/\/cases\/GLDB-8001/);
    await expect(page.getByRole('heading', { name: /Brightwave Analytics Pte Ltd/ })).toBeVisible();
  });

  test('CM-056 the full case listing opens from the dashboard @CM-056', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());
    await openDashboard(page);

    await page.getByRole('link', { name: 'Open case queue' }).first().click();

    await expect(page).toHaveURL(/\/cases$/);
    await expect(page.getByText('Tanjong Print Works')).toBeVisible();
  });

  test('CM-057 a newly scored case is counted once the dashboard is refreshed @CM-057', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());
    await openDashboard(page);
    await expect(riskRow(page, 'High').locator('strong')).toHaveText('1');

    Object.assign(backend.cases.find((c) => c.id === '8005')!, { status: 'case_analysed', risk: RISKS['RS-006'].risk });
    await page.getByRole('button', { name: 'Refresh cases' }).click();

    await expect(riskRow(page, 'High').locator('strong')).toHaveText('2');
  });

  test('CM-058 a deleted case leaves the counts once the dashboard is refreshed @CM-058', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());
    await openDashboard(page);
    await expect(casesInView(page)).toHaveText('5');

    backend.cases = backend.cases.filter((c) => c.id !== '8001');
    await page.getByRole('button', { name: 'Refresh cases' }).click();

    await expect(casesInView(page)).toHaveText('4');
    await expect(riskRow(page, 'Critical').locator('strong')).toHaveText('0');
  });

  test('CM-059 when no case matches the filters the list says so @CM-059', async ({ page, backend }) => {
    backend.cases.push(...brightwaveCases());
    await openCases(page);

    await showOnlyStatus(page, 'Finalised');

    await expect(page.getByText('No cases match the current filters.')).toBeVisible();
    await expect(casesInView(page)).toHaveText('0');
    await expect(page.getByRole('link', { name: /^Open case GLDB-/ })).toHaveCount(0);
  });

  test.describe('Accuracy tab', () => {
    const METRICS = readRegressionFixture<{ overall: { by_check: { check_name: string }[] } }>('detection-metrics-sprint5.json');
    const checkRow = (page: Page, check: string) =>
      page.locator('.accuracy-table tbody tr', { has: page.locator('.flag-name', { hasText: new RegExp(`^${check}$`) }) });

    test.beforeEach(async ({ page, backend }) => {
      backend.detectionMetrics = structuredClone(METRICS);
      await openDashboard(page);
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
