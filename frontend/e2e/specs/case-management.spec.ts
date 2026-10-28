import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { makeCase, makeDocument, makeRisk, type MockBackend } from '../support/mockBackend';
import type { CaseRiskFinding } from '../../src/services/uploadService/types';

/**
 * Case Management (Sprint 2) — "Test Cases - Sprint 2.xlsx", Case Management
 * sheet: View Cases (SCRUM-78) CM-001–004, View Case Details (SCRUM-83)
 * CM-005–009, Create Case (SCRUM-96) CM-010–013.
 */

function seedQueue(backend: MockBackend) {
  const harbor = makeCase({
    id: 'case-harbor',
    greenlink_case_id: 'GLDB-1014',
    company_name: 'Harbor Light Shipping Pte Ltd',
    documents_status: 'analyzed',
    created_at: '2026-09-01T00:00:00Z',
  });
  const meridian = makeCase({
    id: 'case-meridian',
    greenlink_case_id: 'GLDB-1067',
    company_name: 'Meridian Warehousing Solutions Pte Ltd',
    documents_status: 'in_progress',
    created_at: '2026-09-02T00:00:00Z',
  });
  const platinum = makeCase({
    id: 'case-platinum',
    greenlink_case_id: 'GLDB-1032',
    company_name: 'Platinum Fitness Group Pte Ltd',
    documents_status: 'no_documents',
    created_at: '2026-09-03T00:00:00Z',
  });
  backend.cases.push(harbor, meridian, platinum);
  return { harbor, meridian, platinum };
}

const caseCards = (page: Page) => page.locator('.case-row');
const searchBox = (page: Page) => page.getByRole('combobox', { name: 'Search case number or company name' });
const searchOptions = (page: Page) => page.getByRole('listbox').getByRole('option');
// Enter without picking a suggestion lists every match in the queue (/cases?q=…).
async function searchQueue(page: Page, query: string) {
  await searchBox(page).fill(query);
  await searchBox(page).press('Enter');
}
// A header filter stays open while its boxes are ticked. With no filter every box is ticked.
async function openFilter(page: Page, filter: 'status' | 'risk') {
  const list = page.getByRole('group', { name: `${filter} filter` });
  if (!(await list.isVisible())) await page.getByRole('button', { name: new RegExp(`^Filter by ${filter}`) }).click();
  return list;
}
// Ticks or unticks one box.
async function pick(page: Page, filter: 'status' | 'risk', label: RegExp) {
  await (await openFilter(page, filter)).getByRole('checkbox', { name: label }).click();
}
// Leaves only these ticked, by unticking the rest.
async function showOnly(page: Page, filter: 'status' | 'risk', ...wanted: RegExp[]) {
  const boxes = (await openFilter(page, filter)).getByRole('checkbox');
  for (let i = 0; i < (await boxes.count()); i++) {
    const name = (await boxes.nth(i).getAttribute('aria-label')) ?? '';
    if (!wanted.some((pattern) => pattern.test(name))) await boxes.nth(i).uncheck();
  }
}
// The case page shows one section at a time; pick it from the left-hand nav.
const openSection = (page: Page, label: string) =>
  page.getByRole('navigation', { name: 'Case sections' }).getByRole('button', { name: new RegExp(`^${label}`, 'i') }).click();

test.describe('View cases @sprint2 @SCRUM-78', () => {
  test('CM-001 cases can be searched by company name or case ID @CM-001', async ({ page, backend }) => {
    const { harbor, meridian } = seedQueue(backend);
    await page.goto('/cases');
    await expect(caseCards(page)).toHaveCount(3);

    // The header search suggests matching cases as you type...
    await searchBox(page).fill('meridian');
    await expect(searchOptions(page).filter({ hasText: meridian.company_name! })).toHaveCount(1);

    await searchBox(page).fill(harbor.greenlink_case_id);
    await expect(searchOptions(page).filter({ hasText: harbor.company_name! })).toHaveCount(1);

    // ...and picking one opens it.
    await searchOptions(page).filter({ hasText: harbor.company_name! }).click();
    await expect(page.locator('.case-title')).toContainText(harbor.company_name!);

    // "See all results" filters the queue.
    await page.goto('/cases');
    await searchBox(page).fill('meridian');
    await searchOptions(page).filter({ hasText: /See all/ }).click();
    await expect(caseCards(page)).toHaveCount(1);
    await expect(caseCards(page)).toContainText(meridian.company_name!);
  });

  test('CM-002 only cases matching the search are displayed @CM-002', async ({ page, backend }) => {
    seedQueue(backend);
    await page.goto('/cases');

    await searchQueue(page, 'pte ltd');
    await expect(caseCards(page)).toHaveCount(3);

    await searchQueue(page, 'fitness');
    await expect(caseCards(page)).toHaveCount(1);
    await expect(caseCards(page)).toContainText('Platinum Fitness Group Pte Ltd');
    await expect(page.getByText('Showing 1–1 of 1')).toBeVisible();

    await searchQueue(page, 'GLDB-0000');
    await expect(caseCards(page)).toHaveCount(0);
    await expect(page.getByText('No case matches "GLDB-0000"')).toBeVisible();
  });

  test('CM-003 a case can be selected from the case queue @CM-003', async ({ page, backend }) => {
    const { meridian } = seedQueue(backend);
    await page.goto('/cases');

    await page.getByRole('button', { name: `Open case ${meridian.greenlink_case_id}, ${meridian.company_name}` }).click();

    await expect(page).toHaveURL(`/cases/${meridian.greenlink_case_id}`);
    // A case opens on its summary dashboard, not on the document list.
    await expect(page.getByRole('main', { name: 'Case summary dashboard' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Upload supporting documents' })).toHaveCount(0);
  });

  test('CM-004 cases can be filtered by case status @CM-004', async ({ page, backend }) => {
    seedQueue(backend);
    await page.goto('/cases');

    await showOnly(page, 'status', /Ready for review/);
    await expect(caseCards(page)).toHaveCount(1);
    await expect(caseCards(page)).toContainText('Harbor Light Shipping Pte Ltd');

    await pick(page, 'status', /In progress/);
    await expect(caseCards(page)).toHaveCount(2);

    // Both unticked again is no filter at all.
    await pick(page, 'status', /Ready for review/);
    await pick(page, 'status', /In progress/);
    await expect(caseCards(page)).toHaveCount(3);
  });

  test('CM-004 the Documents failed chip shows only cases with a failed document @CM-004', async ({ page, backend }) => {
    seedQueue(backend);
    backend.cases.push(makeCase({ id: 'case-failed', greenlink_case_id: 'GLDB-1099', company_name: 'Failing Co Pte Ltd', documents_status: 'failed' }));
    await page.goto('/cases');

    await showOnly(page, 'status', /Documents failed/);

    await expect(caseCards(page)).toHaveCount(1);
    await expect(caseCards(page)).toContainText('Failing Co Pte Ltd');
  });

  test('CM-004 the Processing stuck chip shows only cases with a stuck document @CM-004', async ({ page, backend }) => {
    seedQueue(backend);
    backend.cases.push(makeCase({ id: 'case-stuck', greenlink_case_id: 'GLDB-1100', company_name: 'Stuck Co Pte Ltd', documents_status: 'stuck' }));
    await page.goto('/cases');

    await showOnly(page, 'status', /Processing stuck/);

    await expect(caseCards(page)).toHaveCount(1);
    await expect(caseCards(page)).toContainText('Stuck Co Pte Ltd');
  });

  test('CM-004 the Documents pending chip shows only cases with no documents yet @CM-004', async ({ page, backend }) => {
    const { platinum } = seedQueue(backend);
    await page.goto('/cases');

    await showOnly(page, 'status', /Documents pending/);

    await expect(caseCards(page)).toHaveCount(1);
    await expect(caseCards(page)).toContainText(platinum.company_name!);
  });
});

test.describe('Case queue paging and filters @SCRUM-299', () => {
  // 45 cases, GLDB-2001 (the oldest) to GLDB-2045: 30 ready for review, 10 failed, 5 in progress.
  function seedMany(backend: MockBackend) {
    let n = 0;
    for (const [documents_status, count] of [['analyzed', 30], ['failed', 10], ['in_progress', 5]] as const) {
      for (let i = 0; i < count; i++) {
        n += 1;
        backend.cases.push(makeCase({
          id: `case-${n}`,
          greenlink_case_id: `GLDB-${2000 + n}`,
          company_name: `Company ${n} Pte Ltd`,
          documents_status,
          created_at: new Date(Date.UTC(2026, 8, 1) + n * 60_000).toISOString(),
          // Every third case is High, the next Low, the rest not assessed.
          risk: n % 3 === 0 ? makeRisk({ state: 'scored', level: 'High', score: 60 })
            : n % 3 === 1 ? makeRisk({ state: 'scored', level: 'Low', score: 10 }) : makeRisk(),
        }));
      }
    }
  }

  const pagination = (page: Page) => page.getByRole('navigation', { name: 'Case pages' });
  const lastListRequest = (backend: MockBackend) => backend.requestsTo('GET', /^\/cases\?/).at(-1)?.path ?? '';

  test('the queue shows 25 cases a page, newest first, and pages through the rest', async ({ page, backend }) => {
    seedMany(backend);
    await page.goto('/cases');

    await expect(caseCards(page)).toHaveCount(25);
    await expect(pagination(page)).toContainText('of 45');
    await expect(caseCards(page).first()).toContainText('GLDB-2045');
    await expect(pagination(page)).toContainText('Showing 1–25 of 45');

    await pagination(page).getByRole('button', { name: /Next/ }).click();

    await expect(page).toHaveURL(/page=2/);
    await expect(caseCards(page)).toHaveCount(20);
    await expect(caseCards(page).first()).toContainText('GLDB-2020');
    await expect(pagination(page)).toContainText('Showing 26–45 of 45');
    expect(lastListRequest(backend)).toContain('offset=25');

    await pagination(page).getByRole('button', { name: /Previous/ }).click();

    await expect(page).not.toHaveURL(/page=/);
    await expect(caseCards(page)).toHaveCount(25);
  });

  test('several status filters at once narrow the total, and the filtered cases are paged', async ({ page, backend }) => {
    seedMany(backend);
    await page.goto('/cases');
    await expect(caseCards(page)).toHaveCount(25);

    await showOnly(page, 'status', /Ready for review/, /Documents failed/);

    // 30 + 10 = 40 cases, so two pages of the filtered list, not of all 45.
    await expect(pagination(page)).toContainText('of 40');
    await expect(pagination(page)).toContainText('Showing 1–25 of 40');
    expect(lastListRequest(backend)).toContain('status=analyzed');
    expect(lastListRequest(backend)).toContain('status=failed');
    expect(lastListRequest(backend)).not.toContain('status=in_progress');

    await pagination(page).getByRole('button', { name: /Next/ }).click();

    await expect(caseCards(page)).toHaveCount(15);
    await expect(pagination(page)).toContainText('Showing 26–40 of 40');
    expect(lastListRequest(backend)).toContain('offset=25');
    expect(lastListRequest(backend)).toContain('status=analyzed');
    expect(lastListRequest(backend)).toContain('status=failed');

    // Every case on that page is one of the two chosen statuses: the five in progress never appear.
    await expect(page.locator('.case-row-status').filter({ hasText: 'In progress' })).toHaveCount(0);

    await pick(page, 'status', /In progress/);

    await expect(pagination(page)).toContainText('of 45');
  });

  test('changing a filter while on a later page goes back to the first page', async ({ page, backend }) => {
    seedMany(backend);
    await page.goto('/cases?page=2');
    await expect(caseCards(page)).toHaveCount(20);

    await showOnly(page, 'status', /Documents failed/);

    await expect(page).not.toHaveURL(/page=/);
    await expect(pagination(page)).toContainText('of 10');
    await expect(caseCards(page)).toHaveCount(10);
    await expect(pagination(page)).toContainText('Showing 1–10 of 10');
    await expect(pagination(page).getByRole('button', { name: /Next/ })).toHaveCount(0);
  });

  test('several risk levels at once narrow the total, and combine with a status filter', async ({ page, backend }) => {
    seedMany(backend);
    await page.goto('/cases');
    await expect(caseCards(page)).toHaveCount(25);

    await showOnly(page, 'risk', /^High$/, /^Low$/);

    // 15 High + 15 Low = 30 cases: two pages of the filtered list.
    await expect(pagination(page)).toContainText('of 30');
    await expect(pagination(page)).toContainText('Showing 1–25 of 30');
    expect(lastListRequest(backend)).toContain('risk=High');
    expect(lastListRequest(backend)).toContain('risk=Low');
    expect(lastListRequest(backend)).not.toContain('risk=unscored');

    await pagination(page).getByRole('button', { name: /Next/ }).click();

    await expect(caseCards(page)).toHaveCount(5);
    expect(lastListRequest(backend)).toContain('offset=25');
    expect(lastListRequest(backend)).toContain('risk=High');

    await showOnly(page, 'status', /Documents failed/);

    // The ten failed cases are 31-40: High is 33, 36, 39 and Low is 31, 34, 37, 40.
    await expect(pagination(page)).toContainText('of 7');
    await expect(caseCards(page)).toHaveCount(7);
    expect(lastListRequest(backend)).toContain('status=failed');
    expect(lastListRequest(backend)).toContain('risk=High');
    expect(lastListRequest(backend)).not.toContain('offset=25');
  });

  test('the Risk and Created headers sort, each click reversing the last', async ({ page, backend }) => {
    seedMany(backend);
    await page.goto('/cases');
    await expect(caseCards(page).first()).toContainText('GLDB-2045');

    await page.getByRole('button', { name: /^Sort by created/ }).click();

    await expect(caseCards(page).first()).toContainText('GLDB-2001');
    expect(lastListRequest(backend)).toContain('sort=oldest');

    await page.getByRole('button', { name: /^Sort by risk/ }).click();

    // High first, newest first among the High ones: 45 is the newest case, and 45 % 3 is 0.
    await expect(caseCards(page).first()).toContainText('GLDB-2045');
    await expect(caseCards(page).first()).toContainText('High');
    expect(lastListRequest(backend)).toContain('sort=risk');

    await page.getByRole('button', { name: /^Sort by risk/ }).click();

    await expect(page.getByRole('button', { name: 'Sort by risk (low to high)' })).toBeVisible();
    expect(lastListRequest(backend)).toContain('sort=lowest_risk');
    await expect(caseCards(page).first()).toContainText('Not assessed');
  });

  test('the account menu holds sign out', async ({ page, backend }) => {
    seedMany(backend);
    await page.goto('/cases');
    await expect(page.getByRole('button', { name: 'Sign out' })).toHaveCount(0);

    await page.getByRole('button', { name: 'Account menu' }).click();
    await expect(page.getByRole('menuitem', { name: 'Sign out' })).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(page.getByRole('menuitem', { name: 'Sign out' })).toHaveCount(0);
  });

  test('the status filter counts stay the whole queue while a filter is on', async ({ page, backend }) => {
    seedMany(backend);
    await page.goto('/cases');
    await showOnly(page, 'status', /Documents failed/);

    await expect(pagination(page)).toContainText('of 10');
    await expect(page.getByRole('group', { name: 'status filter' }).locator('label', { hasText: /Ready for review/ })).toContainText('30');
  });

  test('a page past the end falls back to the last page', async ({ page, backend }) => {
    seedMany(backend);
    await page.goto('/cases?page=9');

    await expect(page).toHaveURL(/page=2/);
    await expect(caseCards(page)).toHaveCount(20);
  });
});

test.describe('Case page layout @SCRUM-299', () => {
  const sectionNav = (page: Page) => page.getByRole('navigation', { name: 'Case sections' });

  test('a case opens on the summary, under a header that stays in view', async ({ page, backend }) => {
    const theCase = makeCase({ id: 'case-layout', greenlink_case_id: 'GLDB-3001', company_name: 'Layout Co Pte Ltd' });
    backend.cases.push(theCase);
    await page.goto('/cases/GLDB-3001');

    await expect(sectionNav(page).getByRole('button', { name: /^Case Summary/ })).toHaveAttribute('aria-current', 'page');
    await expect(page.getByRole('main', { name: 'Case summary dashboard' })).toBeVisible();
    await expect(page.locator('.case-header')).toHaveCSS('position', 'sticky');
  });

  test('opening a case from the queue shows its header at once, without waiting for the lookup', async ({ page, backend }) => {
    const meridian = makeCase({ id: 'case-fast', greenlink_case_id: 'GLDB-3010', company_name: 'Fast Open Pte Ltd' });
    backend.cases.push(meridian);
    await page.goto('/cases');
    await expect(caseCards(page)).toHaveCount(1);
    // The server's answer takes three seconds; the page must not wait for it.
    await page.route('**/cases/by-number/**', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 3000));
      await route.fallback();
    });

    await page.getByRole('button', { name: `Open case ${meridian.greenlink_case_id}, ${meridian.company_name}` }).click();

    await expect(page.getByRole('heading', { name: 'Fast Open Pte Ltd' })).toBeVisible({ timeout: 1500 });
    await expect(sectionNav(page).getByRole('button', { name: /^Case Summary/ })).toBeVisible();
  });

  test('the section menu collapses to its icons, and stays collapsed after a reload', async ({ page, backend }) => {
    backend.cases.push(makeCase({ id: 'case-nav', greenlink_case_id: 'GLDB-3002', company_name: 'Nav Co Pte Ltd' }));
    await page.goto('/cases/GLDB-3002');
    await expect(sectionNav(page).getByText('Case Analysis')).toBeVisible();

    await page.getByRole('button', { name: 'Collapse menu' }).click();

    await expect(sectionNav(page).getByText('Case Analysis')).toBeHidden();
    // Still reachable by name, and still works.
    await sectionNav(page).getByRole('button', { name: /^Documents/ }).click();
    await expect(page.getByRole('heading', { name: 'Upload supporting documents' })).toBeVisible();

    await page.reload();

    await expect(page.getByRole('button', { name: 'Expand menu' })).toBeVisible();
    await page.getByRole('button', { name: 'Expand menu' }).click();
    await expect(sectionNav(page).getByText('Case Analysis')).toBeVisible();
  });

  test('the summary shows how long each stage took, from the recorded runs', async ({ page, backend }) => {
    backend.cases.push(makeCase({ id: 'case-time', greenlink_case_id: 'GLDB-3003', company_name: 'Timing Co Pte Ltd' }));
    const when = '2026-09-01T00:00:00Z';
    backend.timings['case-time'] = [
      { type: 'document.analyzed', document_id: 'd1', occurred_at: when, duration_ms: 4000 },
      { type: 'document.analyzed', document_id: 'd2', occurred_at: when, duration_ms: 8000 },
      { type: 'case.analysis.completed', document_id: null, occurred_at: when, duration_ms: 125000 },
    ];
    await page.goto('/cases/GLDB-3003');

    const stage = (name: string) => page.locator('.case-overview-timing .case-overview-timing-values > div', { hasText: name });
    await expect(stage('Classify + extract')).toContainText('6.0s');
    await expect(stage('Classify + extract')).toContainText('average of 2 documents');
    await expect(stage('Case analysis')).toContainText('2m 05s');
    // Nothing recorded for detection yet.
    await expect(stage('Fraud detection')).toContainText('—');
    // The mock reports no spend, and the summary shows that rather than leaving the card blank.
    await expect(page.locator('.case-overview-cost')).toContainText('$0.0000');
  });
});

test.describe('View case details @sprint2 @SCRUM-83', () => {
  test('CM-005 a selected case opens its details page @CM-005', async ({ page, backend }) => {
    const { harbor } = seedQueue(backend);
    await page.goto('/cases');

    await page.getByRole('button', { name: `Open case ${harbor.greenlink_case_id}, ${harbor.company_name}` }).click();

    await expect(page.locator('.case-title')).toContainText(harbor.company_name!);
    const breadcrumbs = page.getByRole('navigation', { name: 'Breadcrumb' });
    await expect(breadcrumbs).toContainText(harbor.greenlink_case_id);
    await expect(breadcrumbs.getByRole('link', { name: 'Case Queue' })).toBeVisible();
  });

  test('selecting a case from the breadcrumb dropdown opens its summary @CM-005', async ({ page, backend }) => {
    const { harbor } = seedQueue(backend);
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);

    const breadcrumbs = page.getByRole('navigation', { name: 'Breadcrumb' });
    await breadcrumbs.getByRole('button', { name: new RegExp(`${harbor.greenlink_case_id}: switch case`) }).click();
    await page.getByRole('menuitem', { name: harbor.company_name! }).click();

    await expect(page.getByRole('main', { name: 'Case summary dashboard' })).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Case sections' }).getByRole('button', { name: /^Case Summary/i })).toHaveAttribute('aria-current', 'page');
  });

  test('the breadcrumb dropdown lists 50 cases, then loads the rest on request', async ({ page, backend }) => {
    for (let index = 0; index < 60; index += 1) {
      backend.cases.push(makeCase({ id: `case-${index}`, greenlink_case_id: `GLDB-${5000 + index}`, company_name: `Company ${index}` }));
    }
    await page.goto('/cases/GLDB-5000?section=documents');

    await page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('button', { name: /GLDB-5000: switch case/ }).click();
    const menu = page.getByRole('menu');
    await expect(menu.getByRole('menuitem', { name: /^GLDB-/ })).toHaveCount(50);
    await menu.getByRole('menuitem', { name: 'Load more cases' }).click();
    await expect(menu.getByRole('menuitem', { name: /^GLDB-/ })).toHaveCount(60);
    await expect(menu.getByRole('menuitem', { name: 'Load more cases' })).toHaveCount(0);
  });

  test('CM-006 case details show only the selected case\'s information @CM-006', async ({ page, backend }) => {
    const { harbor, meridian } = seedQueue(backend);
    backend.documents.push(
      makeDocument({ case_id: harbor.id, filename: 'C014_CertIncumbency.pdf', status: 'analyzed' }),
      makeDocument({ case_id: meridian.id, filename: 'C067_NOA.pdf', status: 'analyzed' }),
    );

    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);

    await expect(page.locator('.case-title')).toContainText(harbor.company_name!);
    await expect(page.locator('.case-title')).not.toContainText(meridian.company_name!);
    const table = page.locator('.documents-table');
    await expect(table.getByText('C014_CertIncumbency.pdf')).toBeVisible();
    await expect(page.getByText('C067_NOA.pdf')).toHaveCount(0);
  });

  test('CM-007 the customer risk summary is accessible from case details @CM-007', async ({ page, backend }) => {
    const { harbor } = seedQueue(backend);
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);
    await openSection(page, 'Risk summary');

    const section = page.locator('.summary-card', { has: page.getByRole('heading', { name: 'Customer Risk Summary' }) });
    await expect(section).toBeVisible();
    // Nothing uploaded yet, so there's nothing to score.
    await expect(section).toContainText('No documents to score yet.');
  });

  test('CM-007 a case still being scanned shows how many documents are done @CM-007', async ({ page, backend }) => {
    const harbor = makeCase({
      greenlink_case_id: 'GLDB-2001',
      risk: makeRisk({ state: 'pending', documents_scanned: 2, documents_total: 3 }),
    });
    backend.cases.push(harbor);
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);
    await openSection(page, 'Risk summary');

    const section = page.locator('.summary-card', { has: page.getByRole('heading', { name: 'Customer Risk Summary' }) });
    await expect(section).toContainText('2 of 3 documents scanned.');
    await expect(section.locator('.risk-badge')).toHaveCount(0);
  });

  test('CM-007 a scored case shows its risk level and contributing findings @CM-007', async ({ page, backend }) => {
    const finding = (overrides: Partial<CaseRiskFinding>): CaseRiskFinding => ({
      document_id: 'doc-payslip',
      filename: 'C011_Payslip.pdf',
      document_type: 'payslip',
      finding_id: 'font_consistency_tool-1',
      tool: 'font_consistency_tool',
      severity: 'high',
      summary: 'Net pay is in a different font from the rest of the payslip.',
      rationale: 'Only the figure that matters differs.',
      points: 8,
      unreviewed: false,
      ...overrides,
    });
    const harbor = makeCase({
      greenlink_case_id: 'GLDB-2002',
      risk: makeRisk({
        state: 'scored',
        level: 'High',
        score: 57,
        points: 14,
        documents_scanned: 2,
        documents_total: 2,
        reasons: ['Rated High from the combined severity of 4 finding(s).'],
        contributing_findings: [
          finding({}),
          // A repeated high scores a quarter (2 points), less than the first medium, but still lists above it.
          finding({
            finding_id: 'date_consistency_tool-2',
            tool: 'date_consistency_tool',
            summary: 'Account 5 was opened at age 18.8, below the minimum of 21.',
            rationale: 'Account 5 was opened at age 18.8, below the minimum of 21. This is impossible for this product.',
            points: 2,
          }),
          ...[1, 2, 3].map((n) =>
            finding({
              finding_id: `layout_anomalies_tool-${n}`,
              tool: 'layout_anomalies_tool',
              severity: 'medium',
              summary: 'This label and amount on the same row are vertically offset by 20 pt.',
              points: n === 1 ? 3 : 0.75,
            }),
          ),
          finding({
            document_id: 'doc-statement',
            filename: 'C011_BankStatement.pdf',
            document_type: 'bank_statement',
            finding_id: 'spelling_tool-1',
            tool: 'spelling_tool',
            severity: 'low',
            summary: 'Possible misspelling "Statment".',
            points: 1,
          }),
        ],
        unassessed_checks: [
          { document_id: 'doc-statement', filename: 'C011_BankStatement.pdf', tool: 'metadata_tool', reason: 'No metadata' },
        ],
      }),
    });
    backend.cases.push(harbor);
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);
    await openSection(page, 'Risk summary');

    const section = page.locator('.summary-card', { has: page.getByRole('heading', { name: 'Customer Risk Summary' }) });
    await expect(section.locator('.risk-badge')).toHaveText('High risk');
    await expect(section.locator('.risk-score-value')).toHaveText('57');
    await expect(section.locator('.risk-score-level')).toHaveText('High risk · 50–79');
    await expect(section.locator('.risk-gauge')).toHaveAttribute('aria-label', 'Score 57 out of 100, High risk');
    await expect(section).toContainText('2 high · 3 medium · 1 low');

    // The documents are a card of their own. Those with findings, none reviewed yet, are listed collapsed
    // (the low-only bank statement included); the cross-document group, with nothing to review, is folded
    // into Reviewed with a green "No findings" badge, still there to report a miss.
    const review = page.locator('.summary-card', { has: page.getByRole('heading', { name: 'Documents to Review' }) });
    await expect(section.locator('.risk-document')).toHaveCount(0);
    const documents = review.locator('.risk-document');
    await expect(documents).toHaveCount(3);
    await expect(documents.first()).toContainText('C011_Payslip.pdf');
    await expect(documents.first().locator('.risk-review-tag')).toHaveText('Not reviewed');
    await expect(documents.nth(1)).toContainText('C011_BankStatement.pdf');
    await expect(review.getByText('Reviewed (1)')).toBeVisible();
    await expect(documents.nth(2)).toBeHidden();
    await review.getByText('Reviewed (1)').click();
    await expect(documents.nth(2)).toContainText('Across documents');
    await expect(documents.nth(2).locator('.risk-document-none')).toHaveText('✓ No findings');
    await expect(review.locator('.risk-findings')).toHaveCount(0);

    // Expanding lists highs before mediums, and merges the three identical layout findings into one ×3 row.
    await documents.first().getByRole('button').click();
    const rows = review.locator('.risk-findings li');
    await expect(rows).toHaveCount(3);
    await expect(rows.locator('.risk-severity')).toHaveText(['high', 'high', 'medium']);
    await expect(rows.nth(2)).toContainText('×3');
    // The check is a label of its own, and the explanation keeps only what the title doesn't already say.
    await expect(rows.nth(1).locator('.risk-finding-check')).toHaveText('Date consistency');
    await expect(rows.nth(1).locator('.risk-finding-rationale')).toHaveText('This is impossible for this product.');

    // There is nothing to show or hide: low findings are listed with the rest.
    await expect(review.getByRole('button', { name: /Show .* finding|Show less/ })).toHaveCount(0);

    await section.getByText("Couldn't verify (1)").click();
    await expect(section).toContainText('C011_BankStatement.pdf — Metadata: No metadata');
  });

  test('CM-008 flagged anomalies are accessible from case details @CM-008', async ({ page, backend }) => {
    const { harbor } = seedQueue(backend);
    backend.documents.push(
      makeDocument({
        case_id: harbor.id,
        filename: 'Fashion-Purchase-Order.pdf',
        status: 'analyzed',
        document_type: 'unrecognised',
        classification_confidence: 0.4,
        routing_status: 'manual_review',
      }),
      makeDocument({
        case_id: harbor.id,
        filename: 'C014_CertIncorp.pdf',
        status: 'analyzed',
        document_type: 'certificate_of_incorporation',
        classification_confidence: 0.97,
        routing_status: 'accepted',
        extracted_fields: { company_name: { id: 'field-1', value: 'Harbor Light Shipping Pte Ltd', confidence: 0.98 } },
      }),
    );

    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);

    // Flagged items sit under the extracted files on the Documents tab.
    const flagged = page.locator('.summary-card', {
      has: page.getByRole('heading', { name: 'Flagged Items for Manual Review' }),
    });
    await expect(flagged.locator('.flagged-item')).toHaveCount(1);
    await expect(flagged).toContainText('Fashion-Purchase-Order.pdf');
    // Not confirmed by an officer yet, so it reads Unrecognised, not Other.
    await expect(flagged.locator('.flagged-item-meta')).toContainText('Unrecognised');
    await expect(flagged).toContainText('Document type could not be confidently recognised.');
    await expect(flagged).not.toContainText('C014_CertIncorp.pdf');
  });

  test('CM-009 the case audit trail is accessible from case details @CM-009', async ({ page, backend }) => {
    const { harbor } = seedQueue(backend);
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);
    await openSection(page, 'Audit trail');

    const section = page.locator('.summary-card', { has: page.getByRole('heading', { name: 'Case Audit Trail' }) });
    await expect(section).toBeVisible();
    // Audit events aren't recorded yet — the section shows its placeholder.
    await expect(section).toContainText('No audit events available yet.');
  });

  test('a case with nothing needing further evidence recommends no documents', async ({ page, backend }) => {
    const { harbor } = seedQueue(backend);
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);
    await openSection(page, 'Additional documents');

    const section = page.locator('.summary-card', {
      has: page.getByRole('heading', { name: 'Additional Documents' }),
    });
    await expect(section).toContainText('No additional documents needed.');
  });

  test('the additional documents card says when no document can change the score', async ({ page, backend }) => {
    const harbor = makeCase({
      greenlink_case_id: 'GLDB-2004',
      additional_documents: {
        items: [
          {
            title: 'UEN Registration Confirmation Letter',
            why: 'Confirms the UEN printed on the incorporation certificate.',
            source: 'finding',
            addresses: ['60172ada:external_validation_tool-1'],
            affects_score: false,
            score_if_supports: null,
            level_if_supports: null,
            recommendation: null,
          },
        ],
        reason: 'no_reducible_findings',
        current_score: 65,
        current_level: 'High',
        best_case_score: 65,
        best_case_level: 'High',
      },
    });
    backend.cases.push(harbor);
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);
    await openSection(page, 'Additional documents');

    const section = page.locator('.summary-card', {
      has: page.getByRole('heading', { name: 'Additional Documents' }),
    });
    await expect(section).toContainText('No document can change this score.');
    await expect(section).toContainText('UEN Registration Confirmation Letter');
    await expect(section).toContainText('No effect on score');
    await expect(section).not.toContainText('Best case');
  });

  test('recommended documents show why and what to request', async ({ page, backend }) => {
    const harbor = makeCase({
      greenlink_case_id: 'GLDB-2003',
      supporting_document_recommendations: [
        {
          document_type: 'purchase_order',
          title: 'Purchase Order',
          reason: 'C001_BankStatement.pdf shows 1 large supplier payment (of at least 20,000.00).',
          request: 'Request a purchase order for this transaction:',
          source_document_id: 'doc-statement',
          source_filename: 'C001_BankStatement.pdf',
          items: [
            {
              date: '11-Jun-26',
              description: 'REMITTANCE TRANSFER OF FUNDS RTF UNITED PAPER SUPPLY PTE. LTD. SGD 22,506',
              amount: -22506.48,
              prior_amount: null,
              counterparty: 'UNITED PAPER SUPPLY PTE. LTD.',
              reason: 'Payment of 22,506.48 on 11-Jun-26 to UNITED PAPER SUPPLY PTE. LTD.',
              breakdown: [],
            },
          ],
        },
        {
          document_type: 'bank_loan_evidence',
          title: 'Bank Loan Statements or Bank Loan Repayment Schedules',
          reason:
            'C001_Financials.pdf shows a 43.3% increase in total bank borrowings compared with the prior year, exceeding the 40% threshold.',
          request: 'Request bank loan statements or bank loan repayment schedules for this amount:',
          source_document_id: 'doc-financials',
          source_filename: 'C001_Financials.pdf',
          items: [
            {
              date: null,
              description: 'Total bank borrowings',
              amount: 1850790,
              prior_amount: 1291530,
              counterparty: null,
              reason: 'Total bank borrowings: 1,850,790.00 (prior year: 1,291,530.00).',
              breakdown: [
                {
                  date: null,
                  description: 'Bank borrowings, current portion',
                  amount: 516090,
                  prior_amount: 343330,
                  counterparty: null,
                  reason: 'Bank borrowings, current portion: 516,090.00 (prior year: 343,330.00).',
                  breakdown: [],
                },
                {
                  date: null,
                  description: 'Bank borrowings, non-current portion',
                  amount: 1334700,
                  prior_amount: 948200,
                  counterparty: null,
                  reason: 'Bank borrowings, non-current portion: 1,334,700.00 (prior year: 948,200.00).',
                  breakdown: [],
                },
              ],
            },
          ],
        },
      ],
    });
    backend.cases.push(harbor);
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);
    await openSection(page, 'Additional documents');

    const section = page.locator('.summary-card', {
      has: page.getByRole('heading', { name: 'Additional Documents' }),
    });
    await expect(section).toContainText('2 requests');
    await expect(section).toContainText('Purchase Order');
    await expect(section).toContainText('shows 1 large supplier payment');
    await expect(section).toContainText('Request a purchase order for this transaction:');
    await expect(section).toContainText('Payment of 22,506.48 on 11-Jun-26 to UNITED PAPER SUPPLY PTE. LTD.');
    await expect(section).toContainText('Bank Loan Statements or Bank Loan Repayment Schedules');
    await expect(section).toContainText('Total bank borrowings: 1,850,790.00 (prior year: 1,291,530.00).');
    // The portions are tabled with the total they make up as the last row.
    await expect(section.locator('.supporting-document-breakdown tbody tr')).toHaveText([
      /current portion\s*343,330\.00\s*516,090\.00\s*\+172,760\.00 \(\+50\.3%\)/,
      /non-current portion\s*948,200\.00\s*1,334,700\.00\s*\+386,500\.00 \(\+40\.8%\)/,
      /Total bank borrowings\s*1,291,530\.00\s*1,850,790\.00\s*\+559,260\.00 \(\+43\.3%\)/,
    ]);
    await expect(section.getByRole('button', { name: 'View' })).toHaveCount(0);
  });
});

test.describe('Create case @sprint2 @SCRUM-96', () => {
  async function createCase(page: Page, companyName: string) {
    // The queue's New case button is hidden for now, so the form is opened by its address.
    await page.goto('/create-case');
    await expect(page.getByRole('heading', { name: 'Create Case' })).toBeVisible();
    await page.getByLabel(/^company name/i).fill(companyName);
    await page.getByRole('button', { name: 'Create Case' }).click();
    await expect(page).toHaveURL('/cases');
  }

  test('CM-010 a new case can be created with the required information @CM-010', async ({ page, backend }) => {
    seedQueue(backend);

    await createCase(page, 'Ironclad Security Services Pte Ltd');

    const created = backend.requestsTo('POST', '/cases');
    expect(created).toHaveLength(1);
    expect(created[0].body).toEqual({ company_name: 'Ironclad Security Services Pte Ltd' });
  });

  test('CM-010 a company name of exactly 100 characters is accepted @CM-010', async ({ page, backend }) => {
    seedQueue(backend);
    const hundred = 'A'.repeat(100);

    await createCase(page, hundred);

    const created = backend.requestsTo('POST', '/cases');
    expect(created).toHaveLength(1);
    expect(created[0].body).toEqual({ company_name: hundred });
  });

  test('CM-010 typing past 100 characters stops the company name at 100 @CM-010', async ({ page, backend }) => {
    seedQueue(backend);
    await page.goto('/create-case');

    const input = page.getByLabel(/^company name/i);
    await input.pressSequentially('A'.repeat(101));

    await expect(input).toHaveValue('A'.repeat(100));
    await page.getByRole('button', { name: 'Create Case' }).click();
    expect(backend.requestsTo('POST', '/cases')).toHaveLength(1);
  });

  test('CM-010 a case cannot be created without a company name @CM-010', async ({ page, backend }) => {
    seedQueue(backend);
    await page.goto('/create-case');

    await page.getByRole('button', { name: 'Create Case' }).click();

    await expect(page.getByText('Company name is required.')).toBeVisible();
    expect(backend.requestsTo('POST', '/cases')).toHaveLength(0);
  });

  test('CM-011 a new case gets a case ID different from existing cases @CM-011', async ({ page, backend }) => {
    const existing = Object.values(seedQueue(backend)).map((c) => c.greenlink_case_id);

    await createCase(page, 'Ironclad Security Services Pte Ltd');

    const newCard = caseCards(page).filter({ hasText: 'Ironclad Security Services Pte Ltd' });
    await expect(newCard).toHaveCount(1);
    const newId = (await newCard.locator('.case-row-id').innerText()).trim();
    expect(newId).toMatch(/^GLDB-\d+$/);
    expect(existing).not.toContain(newId);
  });

  test('CM-012 the created case\'s information is stored and can be reopened @CM-012', async ({ page, backend }) => {
    seedQueue(backend);

    await createCase(page, 'Ironclad Security Services Pte Ltd');
    await caseCards(page).filter({ hasText: 'Ironclad Security Services Pte Ltd' }).locator('.case-row-open').click();

    await expect(page.locator('.case-title')).toContainText('Ironclad Security Services Pte Ltd');
    await page.reload();
    await expect(page.locator('.case-title')).toContainText('Ironclad Security Services Pte Ltd');
  });

  test('CM-013 a newly created case appears in the queue and can be found by its case ID @CM-013', async ({ page, backend }) => {
    seedQueue(backend);

    await createCase(page, 'Ironclad Security Services Pte Ltd');

    const created = backend.cases.find((c) => c.company_name === 'Ironclad Security Services Pte Ltd')!;
    await searchBox(page).fill(created.greenlink_case_id);
    await expect(searchOptions(page).filter({ hasText: created.greenlink_case_id })).toContainText('Ironclad Security Services Pte Ltd');
  });
});

/**
 * Case Management (Sprint 3) — "Test Cases - Sprint 3.xlsx", Case Management
 * sheet: Update Case (SCRUM-116) CM-014–023, Delete Case/Documents
 * (SCRUM-117) CM-024–028.
 */

function seedIronclad(backend: MockBackend) {
  const ironclad = makeCase({
    id: 'case-ironclad',
    greenlink_case_id: 'GLDB-01',
    company_name: 'IRONCLAD SECURITY',
    documents_status: 'analyzed',
  });
  backend.cases.push(ironclad);
  return ironclad;
}

const NEW_NAME = 'IRONCLAD SECURITY SERVICES PTE LTD';
const caseHeader = (page: Page) => page.locator('.case-title');
const companyNameInput = (page: Page) => page.locator('#case-company-name');
const saveCase = (page: Page) => page.getByRole('button', { name: 'Save', exact: true }).click();

async function openEditMode(page: Page, caseId: string) {
  await page.goto(`/cases/${caseId}?section=documents`);
  await page.getByRole('button', { name: 'Case actions' }).click();
  await page.getByRole('menuitem', { name: 'Edit case details' }).click();
}

async function renameCase(page: Page, caseId: string, name: string) {
  await openEditMode(page, caseId);
  await companyNameInput(page).fill(name);
  await saveCase(page);
}

test.describe('Update case @sprint3 @SCRUM-116', () => {
  test('CM-014 a case opens in edit mode with the company name editable @CM-014', async ({ page, backend }) => {
    const ironclad = seedIronclad(backend);

    await openEditMode(page, ironclad.greenlink_case_id);

    await expect(companyNameInput(page)).toHaveValue('IRONCLAD SECURITY');
    await expect(companyNameInput(page)).toBeEditable();
  });

  test('CM-015 an edited company name is saved @CM-015', async ({ page, backend }) => {
    const ironclad = seedIronclad(backend);

    await renameCase(page, ironclad.greenlink_case_id, NEW_NAME);

    await expect(caseHeader(page)).toContainText(NEW_NAME);
    const saved = backend.requestsTo('PATCH', `/cases/${ironclad.id}`);
    expect(saved).toHaveLength(1);
    expect(saved[0].body).toEqual({ company_name: NEW_NAME });
  });

  test('CM-016 cancelling an edit discards the change @CM-016', async ({ page, backend }) => {
    const ironclad = seedIronclad(backend);
    ironclad.company_name = NEW_NAME;

    await openEditMode(page, ironclad.greenlink_case_id);
    await companyNameInput(page).fill('IRONCLAD SECURITY');
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();

    await expect(caseHeader(page)).toContainText(NEW_NAME);
    expect(backend.requestsTo('PATCH', `/cases/${ironclad.id}`)).toHaveLength(0);
  });

  test('CM-017 the updated name shows on the case queue @CM-017', async ({ page, backend }) => {
    const ironclad = seedIronclad(backend);

    await renameCase(page, ironclad.greenlink_case_id, NEW_NAME);
    await expect(caseHeader(page)).toContainText(NEW_NAME);
    await page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('link', { name: 'Case Queue' }).click();

    await expect(caseCards(page).filter({ hasText: 'GLDB-01' })).toContainText(NEW_NAME);
  });

  test('CM-018 the updated name shows when the case is opened again @CM-018', async ({ page, backend }) => {
    const ironclad = seedIronclad(backend);

    await renameCase(page, ironclad.greenlink_case_id, NEW_NAME);
    await expect(caseHeader(page)).toContainText(NEW_NAME);
    await page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('link', { name: 'Case Queue' }).click();
    await page.getByRole('button', { name: `Open case GLDB-01, ${NEW_NAME}` }).click();

    await expect(caseHeader(page)).toContainText(NEW_NAME);
  });

  test.describe('signed out and back in', () => {
    test.use({ loggedIn: false });

    test('CM-019 the updated name persists after signing out and in @CM-019', async ({ page, backend }) => {
      const ironclad = seedIronclad(backend);
      const signIn = async () => {
        await page.goto('/login');
        await page.getByLabel('Username').fill(backend.credentials.username);
        await page.getByLabel('Password').fill(backend.credentials.password);
        await page.getByRole('button', { name: /Sign in/ }).click();
        await expect(page).toHaveURL('/cases');
      };

      await signIn();
      await renameCase(page, ironclad.greenlink_case_id, NEW_NAME);
      await expect(caseHeader(page)).toContainText(NEW_NAME);
      await page.getByRole('button', { name: 'Account menu' }).click();
      await page.getByRole('menuitem', { name: 'Sign out' }).click();
      await expect(page).toHaveURL(/\/login/);
      await signIn();
      await page.goto(`/cases/${ironclad.greenlink_case_id}?section=documents`);

      await expect(caseHeader(page)).toContainText(NEW_NAME);
    });
  });

  test.describe('who is signed in', () => {
    test.use({ loggedIn: false });

    for (const [username, shown] of [
      ['greenlink-tester', 'Loan Officer · J. Tan'],
      ['demo', 'Loan Officer · K. Lim'],
      ['bob', 'Loan Officer · B. Smith'],
      ['carol', 'Loan Officer · C. Wong'],
      ['someone-new', 'Loan Officer · someone-new'],
    ] as const) {
      test(`the header names ${username} as "${shown}"`, async ({ page, backend }) => {
        backend.credentials = { username, password: 'pw' };
        await page.goto('/login');
        await page.getByLabel('Username').fill(username);
        await page.getByLabel('Password').fill('pw');
        await page.getByRole('button', { name: /Sign in/ }).click();

        await expect(page).toHaveURL('/cases');
        await expect(page.locator('.officer-tag')).toContainText(shown);
      });
    }
  });

  for (const [id, value] of [['CM-020', ''], ['CM-021', '   ']] as const) {
    test(`${id} a ${value ? 'blank' : 'empty'} company name is not saved @${id}`, async ({ page, backend }) => {
      const ironclad = seedIronclad(backend);

      await renameCase(page, ironclad.greenlink_case_id, value);

      await expect(page.getByRole('alert')).toHaveText('Company name is required.');
      expect(backend.requestsTo('PATCH', `/cases/${ironclad.id}`)).toHaveLength(0);
    });
  }

  test('CM-022 the company name stops at 100 characters @CM-022', async ({ page, backend }) => {
    const ironclad = seedIronclad(backend);

    await openEditMode(page, ironclad.greenlink_case_id);
    await companyNameInput(page).fill('');
    await companyNameInput(page).pressSequentially('A'.repeat(110));
    await expect(companyNameInput(page)).toHaveValue('A'.repeat(100));
    await saveCase(page);

    const saved = backend.requestsTo('PATCH', `/cases/${ironclad.id}`);
    expect(saved).toHaveLength(1);
    expect(saved[0].body).toEqual({ company_name: 'A'.repeat(100) });
  });

  test('CM-023 correcting an invalid name clears the error and saves @CM-023', async ({ page, backend }) => {
    const ironclad = seedIronclad(backend);

    await renameCase(page, ironclad.greenlink_case_id, '');
    await expect(page.getByRole('alert')).toHaveText('Company name is required.');
    await companyNameInput(page).fill(NEW_NAME);
    await saveCase(page);

    await expect(page.getByRole('alert')).toHaveCount(0);
    await expect(caseHeader(page)).toContainText(NEW_NAME);
  });

  test('a server error while saving says what failed and to try again, not the status or JSON', async ({ page, backend }) => {
    const ironclad = seedIronclad(backend);
    await page.route(`**/cases/${ironclad.id}`, (route) =>
      route.request().method() === 'PATCH'
        ? route.fulfill({ status: 502, json: { detail: 'Failed to save the case. Check Supabase table access.' } })
        : route.fallback(),
    );

    await renameCase(page, ironclad.greenlink_case_id, NEW_NAME);

    const error = page.locator('.error-message');
    await expect(error.locator('.error-message-title')).toHaveText("Couldn't update case.");
    await expect(error.locator('.error-message-hint')).toHaveText(
      'Something went wrong on our side. Try again, and tell the team if it keeps happening.',
    );
    await expect(page.getByText(/502|Supabase/)).toHaveCount(0);
  });
});

test.describe('Delete case and documents @sprint3 @SCRUM-117', () => {
  function seedScrum117(backend: MockBackend, noaStatus = 'analyzed') {
    const theCase = makeCase({
      id: 'case-117',
      greenlink_case_id: 'GLDB-117',
      company_name: 'scrum-117 test',
      documents_status: 'analyzed',
      fulfilled_requirements: ['notice_of_assessment'],
    });
    const noa = makeDocument({
      id: 'doc-noa',
      case_id: theCase.id,
      filename: 'C035_NOA.pdf',
      status: noaStatus,
      document_type: 'notice_of_assessment',
      classification_confidence: 0.97,
      routing_status: 'accepted',
      fulfilled_requirements: ['notice_of_assessment'],
      extracted_fields: { company_name: { id: 'field-1', value: 'scrum-117 test', confidence: 0.98 } },
    });
    backend.cases.push(theCase);
    backend.documents.push(noa);
    return { theCase, noa, documentUrl: `/cases/${theCase.greenlink_case_id}/documents/${noa.id}` };
  }

  async function deleteDocument(page: Page) {
    await page.getByRole('button', { name: 'Document actions' }).click();
    await page.getByRole('menuitem', { name: 'Delete document' }).click();
  }

  test('CM-024 a document is deleted after confirming, and its link then says it no longer exists @CM-024', async ({ page, backend }) => {
    const { theCase, noa, documentUrl } = seedScrum117(backend);
    await page.goto(documentUrl);

    await deleteDocument(page);
    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toContainText('Delete this document?');
    await expect(dialog).toContainText('C035_NOA.pdf');
    await dialog.getByRole('button', { name: 'Delete' }).click();

    await expect(page).toHaveURL(`/cases/${theCase.greenlink_case_id}?section=documents`);
    expect(backend.requestsTo('DELETE', `/documents/${noa.id}`)).toHaveLength(1);
    await expect(page.getByText('C035_NOA.pdf')).toHaveCount(0);
    await page.goto(documentUrl);
    // A 404 reads as a sentence, not as the status and FastAPI's JSON.
    await expect(page.getByText('This case or document no longer exists.')).toBeVisible();
    await expect(page.getByText(/404|"detail"/)).toHaveCount(0);
  });

  test('CM-025 a case is deleted after confirming, and its link then shows the case not-found page @CM-025', async ({ page, backend }) => {
    const { theCase } = seedScrum117(backend);
    seedQueue(backend);
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=documents`);

    await page.getByRole('button', { name: 'Case actions' }).click();
    await page.getByRole('menuitem', { name: 'Delete case' }).click();
    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toContainText('Delete this case?');
    await dialog.getByRole('button', { name: 'Delete' }).click();

    await expect(page).toHaveURL('/cases');
    await expect(caseCards(page).filter({ hasText: 'scrum-117 test' })).toHaveCount(0);
    expect(backend.requestsTo('DELETE', `/cases/${theCase.id}`)).toHaveLength(1);
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=documents`);
    await expect(page.getByRole('heading', { name: "We couldn't find that case" })).toBeVisible();
    await page.getByRole('button', { name: 'Go to case queue' }).click();
    await expect(page).toHaveURL('/cases');
  });

  test('CM-029 a document link into a deleted case shows the not-found page @CM-029', async ({ page, backend }) => {
    const { theCase, documentUrl } = seedScrum117(backend);
    seedQueue(backend);
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=documents`);

    await page.getByRole('button', { name: 'Case actions' }).click();
    await page.getByRole('menuitem', { name: 'Delete case' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Delete' }).click();
    await expect(page).toHaveURL('/cases');

    await page.goto(documentUrl);
    await expect(page.getByRole('heading', { name: "We couldn't find that document" })).toBeVisible();
    await page.getByRole('button', { name: 'Go to case queue' }).click();
    await expect(page).toHaveURL('/cases');
  });

  test('CM-026 deleting the document that fulfilled a requirement reverts the checklist @CM-026', async ({ page, backend }) => {
    const { theCase, documentUrl } = seedScrum117(backend);
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=documents`);
    const noaRequirement = page.locator('.requirement-row').filter({ hasText: /^Notice of Assessment/ });
    await expect(noaRequirement).toHaveClass(/fulfilled/);

    await page.goto(documentUrl);
    // What the real backend's checklist recomputation returns once the NOA is gone.
    theCase.fulfilled_requirements = [];
    await deleteDocument(page);
    await page.getByRole('alertdialog').getByRole('button', { name: 'Delete' }).click();

    await expect(page).toHaveURL(`/cases/${theCase.greenlink_case_id}?section=documents`);
    await expect(noaRequirement).not.toHaveClass(/fulfilled/);
  });

  test('CM-027 a document still processing cannot be deleted @CM-027', async ({ page, backend }) => {
    const { documentUrl } = seedScrum117(backend, 'processing');
    await page.goto(documentUrl);

    await page.getByRole('button', { name: 'Document actions' }).click();

    await expect(page.getByRole('menuitem', { name: 'Delete document' })).toHaveCount(0);
    expect(backend.requestsTo('DELETE', /\/documents\//)).toHaveLength(0);
  });

  test('CM-028 cancelling the confirmation keeps the document and the case @CM-028', async ({ page, backend }) => {
    const { theCase, documentUrl } = seedScrum117(backend);
    await page.goto(documentUrl);

    await deleteDocument(page);
    await page.getByRole('alertdialog').getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByRole('alertdialog')).toHaveCount(0);
    await expect(page).toHaveURL(documentUrl);

    await page.goto(`/cases/${theCase.greenlink_case_id}?section=documents`);
    await page.getByRole('button', { name: 'Case actions' }).click();
    await page.getByRole('menuitem', { name: 'Delete case' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByRole('alertdialog')).toHaveCount(0);

    expect(backend.requestsTo('DELETE', /./)).toHaveLength(0);
    await expect(page.locator('.documents-table').getByText('C035_NOA.pdf')).toBeVisible();
  });
});

test.describe('Case summary progress', () => {
  // One document with one finding, its review saved; the case analysis either up to date or out of date.
  async function openSummary(page: Page, backend: MockBackend, stale: boolean) {
    const finding: CaseRiskFinding = {
      document_id: 'doc-payslip', filename: 'C011_Payslip.pdf', document_type: 'payslip',
      finding_id: 'numerical_checks_tool-1', tool: 'numerical_checks_tool', severity: 'high',
      summary: 'Net pay does not add up.', rationale: '', points: 8, unreviewed: false,
    };
    const harbor = Object.assign(
      makeCase({
        greenlink_case_id: 'GLDB-3003',
        documents_status: 'analyzed',
        risk: makeRisk({ state: 'scored', level: 'High', score: 57, documents_scanned: 1, documents_total: 1, contributing_findings: [finding] }),
      }),
      { analysis: { run_id: 'run-1', status: 'complete', stale, finding_count: 1, finished_at: '2026-10-27T00:00:00Z' } },
    );
    backend.cases.push(harbor);
    backend.documents.push(makeDocument({ id: 'doc-payslip', case_id: harbor.id, filename: 'C011_Payslip.pdf', document_type: 'payslip', status: 'detected' }));
    const metrics = { correct: 1, wrong: 0, missed: 0, precision: 1, recall: 1, by_check: [] };
    await page.route(/\/finding-reviews$/, (route) => route.fulfill({ json: {
      reviews: [{
        id: 'review-1', case_id: harbor.id, document_id: 'doc-payslip', scope: 'document', kind: 'verdict',
        finding_id: finding.finding_id, check_name: 'numerical_checks_tool', check_code: null, summary: finding.summary,
        verdict: 'correct', note: null, reviewer: 'bob', created_at: '2026-10-27T00:00:00Z', updated_at: '2026-10-27T00:00:00Z',
      }],
      documents: {}, cross_document: metrics, case: metrics, checks: { document: [], case: [] },
    } }));
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=summary`);
    return page.locator('.case-overview-step');
  }

  test('reviewing every finding completes the review once the case analysis is up to date', async ({ page, backend }) => {
    const steps = await openSummary(page, backend, false);
    await expect(steps.filter({ hasText: 'Review Findings' })).toHaveClass(/case-overview-step--done/);
    await expect(steps.filter({ hasText: 'Review Findings' })).toContainText('1 of 1 document reviewed');
    await expect(steps.filter({ hasText: 'Finalise Case' })).toContainText('Ready to finalise');
  });

  test('an out-of-date case analysis keeps the review open, however many of its findings are reviewed', async ({ page, backend }) => {
    const steps = await openSummary(page, backend, true);
    await expect(steps.filter({ hasText: 'Case Analysis' })).toContainText('Out of date · rerun needed');
    await expect(steps.filter({ hasText: 'Review Findings' })).toHaveClass(/case-overview-step--idle/);
    await expect(steps.filter({ hasText: 'Review Findings' })).toContainText('Waiting for the analysis rerun');
    await expect(steps.filter({ hasText: 'Finalise Case' })).toContainText('After all findings are reviewed');
  });
});
