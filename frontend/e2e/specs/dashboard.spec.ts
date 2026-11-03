import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { makeCase, makeDocument } from '../support/mockBackend';

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
    await expect(count('Awaiting Documents')).toHaveText('2');
    await expect(count('Failed')).toHaveText('1');
    await list.getByLabel('Awaiting Documents').uncheck();

    await expect(page.getByText('Cedar Holdings')).toBeVisible();
    await expect(page.getByText('Acme Pte Ltd')).toHaveCount(0);
    await expect(page.getByText('Bolt Logistics')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Filter by status (9 selected)' })).toBeVisible();
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
