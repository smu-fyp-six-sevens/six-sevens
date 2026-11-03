import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { makeCase, makeDocument } from '../support/mockBackend';

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
    await expect(count('Open')).toHaveText('2');
    await expect(count('Failed')).toHaveText('1');
    await list.getByLabel('Open').uncheck();

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
