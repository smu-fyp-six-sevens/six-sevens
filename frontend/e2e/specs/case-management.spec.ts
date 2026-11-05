import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { openGroup, openGroupWith } from '../support/documentGroups';
import { makeCase, makeDocument, type CaseRow, type MockBackend } from '../support/mockBackend';
import type { AuditEvent, CaseRisk } from '../../src/services/uploadService/types';
import { loadFixture, readRegressionFixture } from '../support/fixtures';

/**
 * Case Management (Sprint 2) — "Test Cases - Sprint 2.xlsx", Case Management
 * sheet: View Cases (SCRUM-78) CM-001–004, View Case Details (SCRUM-83)
 * CM-005–009, Create Case (SCRUM-96) CM-010–013.
 */

/**
 * What a case analysis leaves on a case, built by the real scoring and additional-documents code
 * (backend/tests/test_regression_case_views.py writes it), so these tests only show states the backend produces.
 */
type CaseView = { risk: CaseRisk; additional_documents?: CaseRow['additional_documents'] };
const CASE_VIEWS = readRegressionFixture<Record<string, CaseView>>('case-views.json');
const CASE_RISKS = readRegressionFixture<Record<string, { risk: CaseRisk }>>('case-risk-sprint4.json');

/**
 * One complete, unremarkable NOA in the given state, so the case's documents status (worked out from its
 * documents, like the backend's compute_documents_status) reads as wanted. A stuck one has made no progress for
 * an hour.
 */
function addDocument(backend: MockBackend, theCase: CaseRow, status: string) {
  const stuck = status === 'stuck';
  const field = (value: unknown) => ({ value, confidence: 0.98 });
  backend.documents.push(makeDocument({
    case_id: theCase.id,
    filename: `${theCase.greenlink_case_id}_NOA.pdf`,
    status: stuck ? 'processing' : status,
    document_type: 'notice_of_assessment',
    classification_confidence: 0.97,
    routing_status: 'accepted',
    processing_started_at: stuck ? new Date(Date.now() - 3_600_000).toISOString() : null,
    extracted_fields: {
      company_name: field(theCase.company_name),
      tax_reference_number: field('201634564N'),
      company_address: field('6 SERANGOON NORTH AVENUE 5, #02-11, SINGAPORE 554910'),
      year_of_assessment: field('2025'),
      noa_date: field('19 Jun 2025'),
      eci: field(334200),
      tax_payable: field(19694.5),
    },
  }));
}

/** The queue: Harbor Light ready for review, Meridian in progress, Platinum with no documents yet. */
function seedQueue(backend: MockBackend) {
  const harbor = makeCase({
    id: 'case-harbor',
    greenlink_case_id: 'GLDB-1014',
    company_name: 'Harbor Light Shipping Pte Ltd',
    status: 'pending_review',
    created_at: '2026-09-01T00:00:00Z',
  });
  const meridian = makeCase({
    id: 'case-meridian',
    greenlink_case_id: 'GLDB-1067',
    company_name: 'Meridian Warehousing Solutions Pte Ltd',
    status: 'processing_documents',
    created_at: '2026-09-02T00:00:00Z',
  });
  const platinum = makeCase({
    id: 'case-platinum',
    greenlink_case_id: 'GLDB-1032',
    company_name: 'Platinum Fitness Group Pte Ltd',
    created_at: '2026-09-03T00:00:00Z',
  });
  backend.cases.push(harbor, meridian, platinum);
  addDocument(backend, harbor, 'analyzed');
  addDocument(backend, meridian, 'extracting');
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

  test('CM-003 / CM-005 a case selected from the queue opens its details page on the summary @CM-003 @CM-005', async ({ page, backend }) => {
    const { harbor } = seedQueue(backend);
    await page.goto('/cases');

    await page.getByRole('button', { name: `Open case ${harbor.greenlink_case_id}, ${harbor.company_name}` }).click();

    await expect(page).toHaveURL(`/cases/${harbor.greenlink_case_id}`);
    // A case opens on its summary dashboard, not on the document list.
    await expect(page.getByRole('main', { name: 'Case summary dashboard' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Upload supporting documents' })).toHaveCount(0);
    await expect(page.locator('.case-title')).toContainText(harbor.company_name!);
    const breadcrumbs = page.getByRole('navigation', { name: 'Breadcrumb' });
    await expect(breadcrumbs).toContainText(harbor.greenlink_case_id);
    await expect(breadcrumbs.getByRole('link', { name: 'Case Queue' })).toBeVisible();
  });

  test('CM-004 cases can be filtered by case status @CM-004', async ({ page, backend }) => {
    seedQueue(backend);
    await page.goto('/cases');

    // One box per stage some case is at, in the order a case moves through them.
    await expect((await openFilter(page, 'status')).getByRole('checkbox')).toHaveCount(3);
    await expect((await openFilter(page, 'status')).locator('label')).toContainText([
      'Awaiting Documents', 'Processing Documents', 'Pending Review',
    ]);

    await showOnly(page, 'status', /Pending Review/);
    await expect(caseCards(page)).toHaveCount(1);
    await expect(caseCards(page)).toContainText('Harbor Light Shipping Pte Ltd');

    await pick(page, 'status', /Processing Documents/);
    await expect(caseCards(page)).toHaveCount(2);

    // Both unticked again is no filter at all.
    await pick(page, 'status', /Pending Review/);
    await pick(page, 'status', /Processing Documents/);
    await expect(caseCards(page)).toHaveCount(3);
  });

  test('CM-004 the Failed filter shows only cases where something failed @CM-004', async ({ page, backend }) => {
    seedQueue(backend);
    const failing = makeCase({ id: 'case-failed', greenlink_case_id: 'GLDB-1099', company_name: 'Failing Co Pte Ltd', status: 'failed' });
    backend.cases.push(failing);
    addDocument(backend, failing, 'failed');
    await page.goto('/cases');

    await showOnly(page, 'status', /Failed/);

    await expect(caseCards(page)).toHaveCount(1);
    await expect(caseCards(page)).toContainText('Failing Co Pte Ltd');
  });

  test('CM-004 a case with a stuck document is flagged next to its status @CM-004', async ({ page, backend }) => {
    seedQueue(backend);
    const stuckCase = makeCase({ id: 'case-stuck', greenlink_case_id: 'GLDB-1100', company_name: 'Stuck Co Pte Ltd', status: 'processing_documents' });
    backend.cases.push(stuckCase);
    addDocument(backend, stuckCase, 'stuck');
    await page.goto('/cases');

    const stuck = caseCards(page).filter({ hasText: 'Stuck Co Pte Ltd' });
    await expect(stuck.locator('.case-row-status')).toContainText('Processing Documents');
    await expect(stuck.locator('.case-row-stuck')).toHaveText('Stuck');
    await expect(caseCards(page).locator('.case-row-stuck')).toHaveCount(1);
  });

  test('CM-004 the Open filter shows only cases with no documents yet @CM-004', async ({ page, backend }) => {
    const { platinum } = seedQueue(backend);
    await page.goto('/cases');

    await showOnly(page, 'status', /Awaiting Documents/);

    await expect(caseCards(page)).toHaveCount(1);
    await expect(caseCards(page)).toContainText(platinum.company_name!);
  });
});

test.describe('Case queue paging and filters @SCRUM-299', () => {
  // 45 cases, GLDB-2001 (the oldest) to GLDB-2045: 30 analysed, 10 failed, 5 processing documents.
  function seedMany(backend: MockBackend) {
    let n = 0;
    for (const [status, documentStatus, count] of [['case_analysed', 'analyzed', 30], ['failed', 'failed', 10], ['processing_documents', 'extracting', 5]] as const) {
      for (let i = 0; i < count; i++) {
        n += 1;
        const theCase = makeCase({
          id: `case-${n}`,
          greenlink_case_id: `GLDB-${2000 + n}`,
          company_name: `Company ${n} Pte Ltd`,
          status,
          created_at: new Date(Date.UTC(2026, 8, 1) + n * 60_000).toISOString(),
          // Every third case is High (one high finding), the next Low (a clean case), the rest not assessed.
          risk: n % 3 === 0 ? CASE_RISKS['RS-005'].risk : n % 3 === 1 ? CASE_RISKS['RS-001'].risk : null,
        });
        backend.cases.push(theCase);
        addDocument(backend, theCase, documentStatus);
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

    await showOnly(page, 'status', /Ready to Finalise/, /Failed/);

    // 30 + 10 = 40 cases, so two pages of the filtered list, not of all 45.
    await expect(pagination(page)).toContainText('of 40');
    await expect(pagination(page)).toContainText('Showing 1–25 of 40');
    expect(lastListRequest(backend)).toContain('status=case_analysed');
    expect(lastListRequest(backend)).toContain('status=failed');
    expect(lastListRequest(backend)).not.toContain('status=processing_documents');

    await pagination(page).getByRole('button', { name: /Next/ }).click();

    await expect(caseCards(page)).toHaveCount(15);
    await expect(pagination(page)).toContainText('Showing 26–40 of 40');
    expect(lastListRequest(backend)).toContain('offset=25');
    expect(lastListRequest(backend)).toContain('status=case_analysed');
    expect(lastListRequest(backend)).toContain('status=failed');

    // Every case on that page is one of the two chosen statuses: the five in progress never appear.
    await expect(page.locator('.case-row-status').filter({ hasText: 'Processing Documents' })).toHaveCount(0);

    await pick(page, 'status', /Processing Documents/);

    await expect(pagination(page)).toContainText('of 45');
  });

  test('changing a filter while on a later page goes back to the first page', async ({ page, backend }) => {
    seedMany(backend);
    await page.goto('/cases?page=2');
    await expect(caseCards(page)).toHaveCount(20);

    await showOnly(page, 'status', /Failed/);

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

    await showOnly(page, 'status', /Failed/);

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
    await showOnly(page, 'status', /Failed/);

    await expect(pagination(page)).toContainText('of 10');
    await expect(page.getByRole('group', { name: 'status filter' }).locator('label', { hasText: /Ready to Finalise/ })).toContainText('30');
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

  test('a case opens on the summary, with its title under the breadcrumbs', async ({ page, backend }) => {
    const theCase = makeCase({ id: 'case-layout', greenlink_case_id: 'GLDB-3001', company_name: 'Layout Co Pte Ltd' });
    backend.cases.push(theCase);
    await page.goto('/cases/GLDB-3001');

    await expect(sectionNav(page).getByRole('button', { name: /^Case Summary/ })).toHaveAttribute('aria-current', 'page');
    await expect(page.getByRole('main', { name: 'Case summary dashboard' })).toBeVisible();
    const crumbs = await page.locator('.case-header .breadcrumbs').boundingBox();
    const title = await page.locator('.case-header h1').boundingBox();
    expect(title!.y).toBeGreaterThanOrEqual(crumbs!.y + crumbs!.height - 1);
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

  test('the section menu is a rail of icons that opens over the page without moving it', async ({ page, backend }) => {
    backend.cases.push(makeCase({ id: 'case-nav', greenlink_case_id: 'GLDB-3002', company_name: 'Nav Co Pte Ltd' }));
    await page.goto('/cases/GLDB-3002');
    // Measure the loaded page: while it loads, the skeleton's frame can be there twice, and its height differs.
    await expect(page.getByRole('region', { name: 'Document counts' })).toBeVisible();
    // Closed: icons only, but every section is still reachable by name.
    await expect(sectionNav(page).getByText('Case Analysis')).toBeHidden();
    const content = page.locator('.case-frame-main');
    const before = await content.boundingBox();

    await page.getByRole('button', { name: 'Expand menu' }).click();

    await expect(sectionNav(page).getByText('Case Analysis')).toBeVisible();
    // Opened over the page: the content is exactly where it was.
    expect(await content.boundingBox()).toEqual(before);

    // Escape closes it again.
    await page.keyboard.press('Escape');
    await expect(sectionNav(page).getByText('Case Analysis')).toBeHidden();

    // Choosing a section goes there and closes the menu.
    await page.getByRole('button', { name: 'Expand menu' }).click();
    await sectionNav(page).getByRole('button', { name: /^Documents/ }).click();
    await expect(page.getByRole('heading', { name: 'Upload supporting documents' })).toBeVisible();
    await expect(sectionNav(page).getByText('Case Analysis')).toBeHidden();
  });

  test('a document page has the same menu, opening over the document', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    await page.goto(fixture.documentUrl(payslip));

    await page.getByRole('button', { name: 'Expand menu' }).click();
    await expect(sectionNav(page).getByText('Case Analysis')).toBeVisible();
    await sectionNav(page).getByRole('button', { name: /^Risk Summary/ }).click();
    await expect(page).toHaveURL(/section=risk/);
  });

  test('the summary shows how long each stage took, from the recorded runs', async ({ page, backend }) => {
    backend.cases.push(makeCase({ id: 'case-time', greenlink_case_id: 'GLDB-3003', company_name: 'Timing Co Pte Ltd' }));
    // The runs as the audit trail records them; the backend times each finished one (audit.case_cost).
    const finished = (type: string, document_id: string | null, duration_ms: number, outcome: AuditEvent['outcome'] = 'success'): AuditEvent => ({
      event_id: `${type}-${document_id}`, type, occurred_at: '2026-09-01T00:00:00Z', actor_type: 'agent', actor: 'worker',
      case_id: 'case-time', document_id, outcome, started_at: null, duration_ms, detail: {},
    });
    backend.auditEvents = [
      finished('document.analyzed', 'd1', 4000),
      finished('document.analyzed', 'd2', 8000),
      finished('case.analysis.completed', null, 125000),
      // A run that failed isn't a stage's time.
      finished('document.failed', 'd3', 90000, 'failed'),
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
  test('selecting a case from the breadcrumb dropdown opens the same section of that case @CM-005', async ({ page, backend }) => {
    const { harbor, meridian } = seedQueue(backend);
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);

    const breadcrumbs = page.getByRole('navigation', { name: 'Breadcrumb' });
    await breadcrumbs.getByRole('button', { name: new RegExp(`${harbor.greenlink_case_id}: switch case`) }).click();
    await page.getByRole('menuitem', { name: meridian.company_name! }).click();

    // Still on Documents, now for the other case.
    await expect(page).toHaveURL(new RegExp(`/cases/${meridian.greenlink_case_id}[?]section=documents`));
    await expect(page.getByRole('navigation', { name: 'Case sections' }).getByRole('button', { name: /^Documents/i })).toHaveAttribute('aria-current', 'page');
    await expect(page.locator('.documents-card')).toBeVisible();
  });

  test('the case dropdown keeps whichever section the officer is on, the summary included @CM-005', async ({ page, backend }) => {
    const { harbor, meridian } = seedQueue(backend);
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=summary`);

    await page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('button', { name: new RegExp(`${harbor.greenlink_case_id}: switch case`) }).click();
    await page.getByRole('menuitem', { name: meridian.company_name! }).click();

    await expect(page).toHaveURL(new RegExp(`/cases/${meridian.greenlink_case_id}[?]section=summary`));
    await expect(page.getByRole('main', { name: 'Case summary dashboard' })).toBeVisible();
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
    await openGroupWith(page, 'C014_CertIncumbency.pdf');
    await expect(table.getByText('C014_CertIncumbency.pdf')).toBeVisible();
    await expect(page.getByText('C067_NOA.pdf')).toHaveCount(0);
  });

  test('CM-007 a scored case shows its risk level and contributing findings @CM-007', async ({ page, backend }) => {
    const harbor = makeCase({ greenlink_case_id: 'GLDB-2002', risk: CASE_VIEWS.scored.risk });
    backend.cases.push(harbor);
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);
    await openSection(page, 'Risk summary');

    const section = page.locator('.summary-card', { has: page.getByRole('heading', { name: 'Customer Risk Summary' }) });
    await expect(section.locator('.risk-badge')).toHaveText('High Risk');
    await expect(section.locator('.severity-gauge-value')).toHaveText('58');
    await expect(section.locator('.risk-score-level')).toHaveText('High Risk · 50–79');
    await expect(section.locator('.severity-gauge')).toHaveAttribute('aria-label', 'Risk score 58 out of 100, High');
    await expect(section).toContainText('2 high · 3 medium · 1 low');

    // The documents are a card of their own, listed collapsed until reviewed (the low-only bank statement
    // included); the cross-document group, with a green "No findings" badge, waits to be saved like the rest.
    const review = page.locator('.summary-card', { has: page.getByRole('heading', { name: 'Documents to Review' }) });
    await expect(section.locator('.risk-document')).toHaveCount(0);
    const documents = review.locator('.risk-document');
    await expect(documents).toHaveCount(3);
    await expect(documents.first()).toContainText('C011_Payslip.pdf');
    await expect(documents.first().locator('.risk-review-tag')).toHaveCount(0);
    await expect(documents.nth(1)).toContainText('C011_BankStatement.pdf');
    await expect(review.getByText(/^Reviewed \(/)).toHaveCount(0);
    await expect(documents.nth(2)).toContainText('Across documents');
    await expect(documents.nth(2).locator('.risk-document-none')).toHaveText('✓ No findings');
    await expect(review.locator('.risk-findings')).toHaveCount(0);

    // Opening a document reviews it beside the document: its findings list highs before mediums (the repeated
    // font finding scores a quarter, 2 points, but still lists above the 3-point medium), and merges the three
    // identical layout findings into one ×3 row.
    await documents.first().getByRole('button').click();
    const dialog = page.getByRole('dialog', { name: 'Review findings on C011_Payslip.pdf' });
    await expect(dialog).toBeVisible();
    const rows = dialog.locator('.risk-findings li');
    await expect(rows).toHaveCount(3);
    await expect(rows.locator('.risk-severity')).toHaveText(['high', 'high', 'medium']);
    await expect(rows.nth(2)).toContainText('×3');
    // The check is a label of its own, and the explanation keeps only what the title doesn't already say.
    await expect(rows.nth(1).locator('.risk-finding-check')).toHaveText('Font consistency');
    await expect(rows.nth(1).locator('.risk-finding-rationale')).toHaveText('The edit hides an underpayment.');

    // There is nothing to show or hide: low findings are listed with the rest.
    await expect(dialog.getByRole('button', { name: /Show .* finding|Show less/ })).toHaveCount(0);

    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();

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

    // A document that needs the officer says why under its name in the document list.
    const row = page.locator('.documents-table-row', { hasText: 'Fashion-Purchase-Order.pdf' });
    await openGroup(page, 'Needs Review');
    await expect(page.locator('.documents-table-row--review')).toHaveCount(1);
    await expect(row.locator('.document-review-reason')).toContainText('could not be recognised');
    await expect(page.locator('.documents-table-group--review')).toContainText('Needs Review');
    // Not confirmed by an officer yet, so it reads Unrecognised, not Other.
    await expect(row.locator('.document-type-cell')).toContainText('Unrecognised');
    // The document that is fine has no reason under its name.
    await openGroup(page, 'Pending Approval');
    await expect(page.locator('.documents-table-row', { hasText: 'C014_CertIncorp.pdf' }).locator('.document-review-reason')).toHaveCount(0);
  });

  test('CM-009 the case audit trail is reached from case details and lists what was recorded for this case @CM-009', async ({ page, backend }) => {
    const { harbor, meridian } = seedQueue(backend);
    const recorded = (caseId: string, type: string, occurred_at: string, filename: string): AuditEvent => ({
      event_id: `${caseId}-${type}`, type, occurred_at, actor_type: 'user', actor: 'officer', case_id: caseId,
      document_id: null, outcome: 'success', started_at: null, duration_ms: null, detail: { filename },
    });
    backend.auditEvents = [
      recorded(harbor.id, 'document.uploaded', '2026-10-21T02:00:00Z', 'C014_CertIncorp.pdf'),
      recorded(meridian.id, 'document.uploaded', '2026-10-21T02:01:00Z', 'C067_NOA.pdf'),
    ];
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);

    await openSection(page, 'Audit trail');

    const section = page.locator('.summary-card', { has: page.getByRole('heading', { name: 'Case Audit Trail' }) });
    await expect(section.getByTestId('audit-entry')).toHaveCount(1);
    await expect(section.getByTestId('audit-entry')).toContainText('Uploaded document');
    expect(backend.requestsTo('GET', new RegExp(`^/audit/events\\?.*case_id=${harbor.id}`))).not.toHaveLength(0);
  });

  test('a case with nothing needing further evidence recommends no documents', async ({ page, backend }) => {
    const harbor = makeCase({ greenlink_case_id: 'GLDB-2003', ...CASE_VIEWS['nothing needed'] });
    backend.cases.push(harbor);
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);
    await openSection(page, 'Additional documents');

    const section = page.locator('.summary-card', {
      has: page.getByRole('heading', { name: 'Additional Documents' }),
    });
    await expect(section).toContainText('No additional documents needed.');
  });

  test('the additional documents card says when no document can change the score', async ({ page, backend }) => {
    // A UEN ACRA has no record of: a registry discrepancy, which no further document can reduce.
    const harbor = makeCase({ greenlink_case_id: 'GLDB-2004', ...CASE_VIEWS['no reducible findings'] });
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
    // A large supplier payment and a 43.3% rise in bank borrowings.
    const harbor = makeCase({ greenlink_case_id: 'GLDB-2005', ...CASE_VIEWS.recommendations });
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
    expect(created[0].body).toEqual({ company_name: 'Ironclad Security Services Pte Ltd', reviews_required: true });
  });

  test('CM-010 a company name of exactly 100 characters is accepted @CM-010', async ({ page, backend }) => {
    seedQueue(backend);
    const hundred = 'A'.repeat(100);

    await createCase(page, hundred);

    const created = backend.requestsTo('POST', '/cases');
    expect(created).toHaveLength(1);
    expect(created[0].body).toEqual({ company_name: hundred, reviews_required: true });
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
    await expect(error.locator('.error-message-title')).toHaveText('Failed to update case.');
    await expect(error.locator('.error-message-hint')).toHaveText(
      'Something went wrong on our side; try again, and tell the team if it keeps happening.',
    );
    await expect(page.getByText(/502|Supabase/)).toHaveCount(0);
  });

  test('no response while saving reads like a server error', async ({ page, backend }) => {
    const ironclad = seedIronclad(backend);
    await page.route(`**/cases/${ironclad.id}`, (route) =>
      route.request().method() === 'PATCH' ? route.abort('connectionrefused') : route.fallback(),
    );

    await renameCase(page, ironclad.greenlink_case_id, NEW_NAME);

    const error = page.locator('.error-message');
    await expect(error.locator('.error-message-title')).toHaveText('Failed to update case.');
    await expect(error.locator('.error-message-hint')).toHaveText(
      'Something went wrong on our side; try again, and tell the team if it keeps happening.',
    );
  });
});

test.describe('Delete case and documents @sprint3 @SCRUM-117', () => {
  function seedScrum117(backend: MockBackend, noaStatus = 'analyzed') {
    const theCase = makeCase({
      id: 'case-117',
      greenlink_case_id: 'GLDB-117',
      company_name: 'scrum-117 test',
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

  test('CM-025 / CM-028 deleting asks first: Cancel keeps everything, Delete removes the case and its links show not-found @CM-025 @CM-028', async ({ page, backend }) => {
    const { theCase, documentUrl } = seedScrum117(backend);
    seedQueue(backend);

    // CM-028: cancelling either confirmation deletes nothing.
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
    await openGroupWith(page, 'C035_NOA.pdf');
    await expect(page.locator('.documents-table').getByText('C035_NOA.pdf')).toBeVisible();

    // CM-025: confirming deletes the case.
    await page.getByRole('button', { name: 'Case actions' }).click();
    await page.getByRole('menuitem', { name: 'Delete case' }).click();
    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toContainText('Delete this case?');
    await dialog.getByRole('button', { name: 'Delete' }).click();

    await expect(page).toHaveURL('/cases');
    await expect(caseCards(page).filter({ hasText: 'scrum-117 test' })).toHaveCount(0);
    expect(backend.requestsTo('DELETE', `/cases/${theCase.id}`)).toHaveLength(1);
    // Both the case's link and a link to one of its documents now show a not-found page.
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=documents`);
    await expect(page.getByRole('heading', { name: "We couldn't find that case" })).toBeVisible();
    await page.goto(documentUrl);
    await expect(page.getByRole('heading', { name: "We couldn't find that document" })).toBeVisible();
    await page.getByRole('button', { name: 'Go to case queue' }).click();
    await expect(page).toHaveURL('/cases');
  });

  test('CM-026 deleting the document that fulfilled a requirement reverts the checklist @CM-026', async ({ page, backend }) => {
    // The real GLDB-02 case: the checklist after the delete is the one the backend computes for the documents left
    // (checklist-variants.json), not one this test sets.
    const fixture = loadFixture(backend, 'anchorpoint');
    const noa = fixture.document('Anchorpoint_IRAS_NOA');
    await page.goto(`${fixture.caseUrl}?section=documents`);
    const missing = (label: string) =>
      page.locator('.checklist-missing-row').filter({ has: page.locator('.document-type-cell', { hasText: new RegExp(`^${label}$`) }) });
    await expect(missing('Notice of Assessment')).toHaveCount(0);

    await page.goto(fixture.documentUrl(noa));
    await deleteDocument(page);
    await page.getByRole('alertdialog').getByRole('button', { name: 'Delete' }).click();

    await expect(page).toHaveURL(`${fixture.caseUrl}?section=documents`);
    await expect(missing('Notice of Assessment')).toHaveCount(1);
    // Only that requirement reverts: the others the case's documents fulfil stay met.
    await expect(missing('Certificate of Incorporation')).toHaveCount(0);
  });

  test('CM-027 a document still processing cannot be deleted @CM-027', async ({ page, backend }) => {
    const { documentUrl } = seedScrum117(backend, 'processing');
    await page.goto(documentUrl);

    await page.getByRole('button', { name: 'Document actions' }).click();

    await expect(page.getByRole('menuitem', { name: 'Delete document' })).toHaveCount(0);
    expect(backend.requestsTo('DELETE', /\/documents\//)).toHaveLength(0);
  });
});

test.describe('Case page: document actions, live updates and cost', () => {
  function caseWithDocument(backend: MockBackend, status: string) {
    const { harbor, meridian } = seedQueue(backend);
    const document = makeDocument({
      id: 'doc-incorp',
      case_id: harbor.id,
      filename: 'C014_CertIncorp.pdf',
      status,
      document_type: 'certificate_of_incorporation',
      classification_confidence: 0.97,
      routing_status: 'accepted',
    });
    backend.documents.push(document);
    return { harbor, meridian, document };
  }

  test('a document can be cancelled while it is being extracted', async ({ page, backend }) => {
    const { harbor, document } = caseWithDocument(backend, 'extracting');
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);

    await openGroupWith(page, document.filename);
    await page.getByRole('button', { name: `Cancel ${document.filename}` }).click();

    await expect.poll(() => backend.requestsTo('POST', `/documents/${document.id}/cancel`).length).toBe(1);
    expect(document.status).toBe('cancelled');
    backend.push(harbor.id);
    // A cancelled document moves to Needs Review.
    await openGroupWith(page, document.filename);
    await expect(page.getByRole('button', { name: `View ${document.filename}` })).toContainText('Cancelled');
    await expect(page.getByRole('button', { name: `Cancel ${document.filename}` })).toHaveCount(0);
  });

  test('selected documents can be moved to another case', async ({ page, backend }) => {
    const { harbor, meridian, document } = caseWithDocument(backend, 'analyzed');
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);

    // The padded cell around the checkbox is the click target.
    await openGroupWith(page, document.filename);
    const select = page.getByRole('checkbox', { name: `Select ${document.filename}` });
    await page.locator('.document-select-cell', { has: select }).click();
    await expect(select).toBeChecked();
    await page.getByRole('button', { name: 'Move selected to another case' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('Move 1 document(s) to another case');
    await expect(dialog.getByRole('button', { name: 'Move' })).toBeDisabled();
    await dialog.getByRole('combobox').selectOption(meridian.id);
    await dialog.getByRole('button', { name: 'Move' }).click();

    await expect(dialog).toHaveCount(0);
    expect(backend.requestsTo('POST', '/documents/bulk-move')[0].body).toEqual({
      document_ids: [document.id],
      case_id: meridian.id,
    });
    await expect(page.locator('.documents-table').getByText(document.filename)).toHaveCount(0);
    await page.goto(`/cases/${meridian.greenlink_case_id}?section=documents`);
    await openGroupWith(page, document.filename);
    await expect(page.locator('.documents-table').getByText(document.filename)).toBeVisible();
  });

  test('after the live connection drops, the page reconnects and catches up on what changed meanwhile', async ({ page, backend }) => {
    const { harbor, document } = caseWithDocument(backend, 'extracting');
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);
    const live = page.locator('.live-badge').first();
    await expect(live).toHaveText('LIVE');

    await backend.dropConnection(harbor.id);
    await expect(live).toHaveText('OFFLINE');
    // Finished while the page was disconnected: no push reached it.
    document.status = 'analyzed';

    await expect(live).toHaveText('LIVE', { timeout: 10_000 });
    await openGroupWith(page, document.filename);
    await expect(page.getByRole('button', { name: `View ${document.filename}` })).not.toContainText('Extracting');
    await expect(page.getByRole('button', { name: `Cancel ${document.filename}` })).toHaveCount(0);
    // Every connection asks for a new single-use ticket.
    expect(backend.requestsTo('POST', '/ws/tickets')).toHaveLength(2);
  });

  test('the Claude Cost tab totals the cost each run recorded, per document and for the case analysis', async ({ page, backend }) => {
    const { harbor, document } = caseWithDocument(backend, 'detected');
    const run = (type: string, occurred_at: string, detail: Record<string, unknown>, document_id: string | null = document.id): AuditEvent => ({
      event_id: `${type}-${occurred_at}`, type, occurred_at, actor_type: 'agent', actor: 'worker', case_id: harbor.id,
      document_id, outcome: 'success', started_at: null, duration_ms: 1000, detail,
    });
    backend.auditEvents = [
      run('document.analyzed', '2026-10-21T02:00:00Z', { run: 'document', llm_cost_usd: 0.0125, llm_calls: 2 }),
      run('document.detected', '2026-10-21T02:05:00Z', { run: 'detection', llm_cost_usd: 0.04, llm_calls: 5 }),
      run('case.analysis.completed', '2026-10-21T02:10:00Z', { run: 'case_analysis', llm_cost_usd: 0.03, llm_calls: 3 }, null),
      // Recorded before runs carried their cost: listed nowhere, and adds nothing.
      run('document.analyzed', '2026-10-20T02:00:00Z', { run: 'document' }),
    ];
    await page.goto(`/cases/${harbor.greenlink_case_id}?section=documents`);

    await openSection(page, 'Claude Cost');

    const card = page.locator('.summary-card', { has: page.getByRole('heading', { name: 'Claude Cost' }) });
    await expect(card.locator('.cost-figure--total')).toContainText('$0.0825');
    await expect(card.locator('.cost-figures')).toContainText('Documents$0.0525');
    await expect(card.locator('.cost-figures')).toContainText('Case analysis$0.0300');
    await expect(card.locator('table').first()).toContainText(`${document.filename}$0.0525`);
    await card.getByText('3 runs').click();
    await expect(card.locator('details tbody tr')).toHaveCount(3);
    await expect(card.locator('details tbody tr').last()).toContainText('Case analysis');
  });
});

/**
 * Reviewing a document's findings in the Risk Summary shows the document in X-ray: once the page has drawn,
 * shaded but for the areas detection flagged, numbered and stepped through place by place in the order of the
 * list beside it (from a bar at its foot, or the arrow keys), with the finding being looked at picked out there.
 */
test.describe('X-ray in the findings review', () => {
  // One blank A4 page: pdf.js rebuilds the missing cross-reference table.
  const BLANK_PDF = [
    '%PDF-1.4',
    '1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj',
    '2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj',
    '3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj',
    'trailer<</Root 1 0 R>>',
    '%%EOF',
  ].join('\n');

  test('steps through every place in the list\'s order with the list following, and Esc closes the card before the review', async ({ page, backend }) => {
    const harbor = makeCase({ greenlink_case_id: 'GLDB-2002', risk: CASE_VIEWS.scored.risk });
    backend.cases.push(harbor);
    const payslipId = CASE_VIEWS.scored.risk!.contributing_findings.find((f) => f.filename === 'C011_Payslip.pdf')!.document_id;
    backend.documents.push(
      makeDocument({ id: payslipId, case_id: harbor.id, filename: 'C011_Payslip.pdf', document_type: 'payslip', status: 'detected' }),
    );
    const report = readRegressionFixture<{ findings: object[] }>('detection-report-c028.json');
    const placedAt = (id: string, severity: string, ...bboxes: number[][]) => ({
      ...report.findings[0], id, severity, summary: `${id} summary`, locations: bboxes.map((bbox) => ({ page: 1, bbox })),
    });
    backend.detectionReports[payslipId] = {
      ...report,
      document_id: payslipId,
      // Higher up the page, the layout finding comes first in reading order, but after the font one in the list.
      findings: [
        placedAt('font_consistency_tool-1', 'high', [400, 400, 460, 412], [400, 600, 460, 612]),
        placedAt('layout_anomalies_tool-1', 'medium', [400, 100, 460, 112]),
      ],
    };
    // A real page to draw, so X-ray starts: the mock's own preview is empty.
    await page.route('**/documents/*/download*', (route) =>
      route.fulfill({ json: { document_id: payslipId, signed_url: 'http://files.test/payslip.pdf', expires_in: 3600 } }),
    );
    await page.route('http://files.test/**', (route) =>
      route.fulfill({ body: BLANK_PDF, contentType: 'application/pdf', headers: { 'access-control-allow-origin': '*' } }),
    );

    await page.goto(`/cases/${harbor.greenlink_case_id}?section=risk`);
    const review = page.locator('.summary-card', { has: page.getByRole('heading', { name: 'Documents to Review' }) });
    await review.locator('.risk-document').first().getByRole('button').click();
    const dialog = page.getByRole('dialog', { name: 'Review findings on C011_Payslip.pdf' });
    const status = dialog.locator('.xray-bar-status');
    const activeRow = dialog.locator('.risk-findings li.risk-finding--active');
    const card = dialog.locator('.finding-card');

    // Numbered and stepped through in the list's order: its rows are the two font findings (high) then the
    // layout one (medium). Once the page has drawn and the scan has passed, the first placed row is picked, at
    // its first place, on the document and in the list.
    await expect(dialog.locator('.xray-stage--ready')).toHaveCount(1);
    await expect(status).toContainText('Finding 1 of 3 · place 1 of 2');
    await expect(activeRow).toContainText('Net pay');
    await expect(card).toContainText('Place 1 of 2');

    // The row steps through its own places too, without walking every button (a word can recur dozens of times).
    const rowPlaces = activeRow.getByRole('group', { name: 'Places on the document' });
    await expect(rowPlaces).toContainText('Place 1 of 2 (Page 1)');
    await rowPlaces.getByRole('button', { name: 'Next place' }).click();
    await expect(status).toContainText('Finding 1 of 3 · place 2 of 2');
    await rowPlaces.getByRole('button', { name: 'Previous place' }).click();
    await expect(status).toContainText('Finding 1 of 3 · place 1 of 2');

    // → goes to the row's other place, its card beside that place, then skips the second row (nothing placed).
    await page.keyboard.press('ArrowRight');
    await expect(status).toContainText('Finding 1 of 3 · place 2 of 2');
    await expect(card).toContainText('Place 2 of 2');
    await page.keyboard.press('ArrowRight');
    await expect(status).toContainText('Finding 3 of 3');
    await expect(activeRow).toContainText('vertically offset');
    await expect(card).toContainText('layout_anomalies_tool-1 summary');

    // Clicking a place opens the card there; a row's "Show on page" opens its finding at its first place.
    await dialog.locator('[data-highlight-id="font_consistency_tool-1"][data-highlight-area="1"]').click();
    await expect(card).toContainText('Place 2 of 2');
    await dialog.locator('.risk-findings li', { hasText: 'vertically offset' }).getByRole('button', { name: /Show on page 1/ }).click();
    await expect(status).toContainText('Finding 3 of 3');
    // Not the row shown, a finding in several places offers one button to its first.
    const netPay = dialog.locator('.risk-findings li', { hasText: 'Net pay' });
    await netPay.getByRole('button', { name: 'Show on document · 2 places' }).click();
    await expect(status).toContainText('Finding 1 of 3 · place 1 of 2');
    await dialog.locator('.risk-findings li', { hasText: 'vertically offset' }).getByRole('button', { name: /Show on page 1/ }).click();

    // "Dim page" off shows the page as printed, the flagged places still outlined; on again, it's dimmed.
    const dim = dialog.getByRole('button', { name: 'Dim page' });
    await expect(dim).toHaveAttribute('aria-pressed', 'true');
    await expect(dialog.locator('.finding-highlight-shade')).not.toHaveCount(0);
    await dim.click();
    await expect(dim).toHaveAttribute('aria-pressed', 'false');
    await expect(dialog.locator('.finding-highlight-shade')).toHaveCount(0);
    await expect(dialog.locator('[data-highlight-id="layout_anomalies_tool-1"]')).toBeVisible();
    await dim.click();
    await expect(dialog.locator('.finding-highlight-shade')).not.toHaveCount(0);

    await page.keyboard.press('Escape');
    await expect(status).toHaveText('2 findings on the page');
    await expect(card).toHaveCount(0);
    await expect(dialog).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
  });
});

/**
 * Sprint 5 regression (SCRUM-323), "Test Cases - Sprint 5.xlsx" Case Management sheet, View Case Overview
 * (SCRUM-299): CM-037 – CM-049, on the captured Anchorpoint case (GLDB-02, 20 extracted documents). The app keeps a
 * case's documents in two sections, Documents and Approved Documents; documents for manual review and rejected ones
 * are cards within Documents, shown while there are any (see the Sprint 5 report on CM-041 / CM-046 / CM-048).
 */
test.describe('View case overview @sprint5 @SCRUM-299', () => {
  const sectionNav = (page: Page) => page.getByRole('navigation', { name: 'Case sections' });
  const openSection = (page: Page, name: RegExp) => sectionNav(page).getByRole('button', { name }).click();
  const documentRow = (page: Page, filename: string) => page.getByRole('button', { name: `View ${filename}` });
  const card = (page: Page, title: string) => page.locator('.documents-card', { has: page.locator('.documents-card-title', { hasText: title }) });

  test('CM-037 the company name is shown when the case opens @CM-037', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');

    await page.goto(fixture.caseUrl);

    await expect(page.getByRole('heading', { level: 1 })).toHaveText(fixture.case.company_name!);
  });

  test('CM-038 a scored case shows its overall risk and score @CM-038', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const { risk } = CASE_VIEWS.scored;
    Object.assign(fixture.case, { status: 'case_analysed', risk });

    await page.goto(fixture.caseUrl);

    const severity = page.locator('.case-overview-risk');
    await expect(severity.locator('strong').first()).toHaveText(`${risk.level} risk`);
    await expect(severity.getByLabel(`Risk score ${risk.score} out of 100`)).toBeVisible();
  });

  test('CM-039 before it is scored the case says so, with no score @CM-039', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');

    await page.goto(fixture.caseUrl);

    const severity = page.locator('.case-overview-risk');
    await expect(severity.locator('strong').first()).toHaveText('Not scored yet');
    await expect(severity).toContainText('Run case analysis after documents are approved and scanned');
    await expect(severity.getByLabel(/^Risk score/)).toHaveCount(0);
  });

  test('CM-040 a long company name is cut with an ellipsis, in full on hover, without widening the page @CM-040', async ({ page, backend }) => {
    const name = 'Brightwave Analytics and Data Consultancy Services Holdings International (Asia Pacific) Pte Ltd';
    backend.cases.push(makeCase({ id: 'case-long', greenlink_case_id: 'GLDB-3040', company_name: name }));

    await page.goto('/cases/GLDB-3040');

    const title = page.getByRole('heading', { level: 1 });
    await expect(title).toHaveText(name);
    await expect(title).toHaveAttribute('title', name);
    await expect(title).toHaveCSS('text-overflow', 'ellipsis');
    await expect(page.getByRole('button', { name: 'Case actions' })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });

  test('CM-041 / CM-042 a document moves from Documents to Approved Documents, or to Rejected, as its status changes @CM-041 @CM-042', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const noa = fixture.document('IRAS_NOA');
    const fatca = fixture.document('FATCA');
    await page.goto(`${fixture.caseUrl}?section=documents`);
    await expect(documentRow(page, noa.filename)).toBeVisible();

    noa.status = 'approved';
    fatca.status = 'rejected';
    backend.push(fixture.case.id);

    await expect(documentRow(page, noa.filename)).toHaveCount(0);
    await expect(card(page, 'Rejected Documents').getByRole('button', { name: `View ${fatca.filename}` })).toBeVisible();
    await openSection(page, /^Approved Documents/);
    await expect(card(page, 'Approved Documents').getByRole('button', { name: `View ${noa.filename}` })).toBeVisible();
    await expect(card(page, 'Approved Documents').getByRole('button', { name: `View ${fatca.filename}` })).toHaveCount(0);
  });

  test('CM-043 each document shows its name, type and status @CM-043', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const noa = fixture.document('IRAS_NOA');

    await page.goto(`${fixture.caseUrl}?section=documents`);

    const row = documentRow(page, noa.filename);
    await expect(row.locator('.document-filename')).toHaveText(noa.filename);
    await expect(row.locator('.document-type-cell')).toHaveText('Notice of Assessment');
    await expect(row.locator('.document-status-cell')).toContainText('Extracted');
  });

  test('CM-044 a document still being processed shows its in-progress status @CM-044', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const noa = fixture.document('IRAS_NOA');
    noa.status = 'extracting';

    await page.goto(`${fixture.caseUrl}?section=documents`);

    await expect(documentRow(page, noa.filename).locator('.document-status-cell')).toContainText('Extracting…');
  });

  test('CM-045 selecting a document opens its details @CM-045', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const noa = fixture.document('IRAS_NOA');
    await page.goto(`${fixture.caseUrl}?section=documents`);

    await documentRow(page, noa.filename).click();

    await expect(page).toHaveURL(new RegExp(`${fixture.documentUrl(noa)}$`));
    await expect(page.getByText(noa.filename).first()).toBeVisible();
  });

  test('CM-046 / CM-047 a section with no documents says so: Approved Documents before any approval @CM-046 @CM-047', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');

    await page.goto(`${fixture.caseUrl}?section=approved`);

    await expect(page.getByText('No approved documents yet.')).toBeVisible();
    await expect(page.getByText('Documents appear here once an officer approves them from the Documents tab.')).toBeVisible();
    // Nothing rejected: Documents shows no Rejected Documents card at all, rather than an empty one.
    await openSection(page, /^Documents/);
    await expect(documentRow(page, fixture.document('IRAS_NOA').filename)).toBeVisible();
    await expect(card(page, 'Rejected Documents')).toHaveCount(0);
  });

  test('CM-048 a case with no documents shows the upload area and empty Approved Documents @CM-048', async ({ page, backend }) => {
    backend.cases.push(makeCase({ id: 'case-empty', greenlink_case_id: 'GLDB-3048', company_name: 'Empty Case Pte Ltd' }));

    await page.goto('/cases/GLDB-3048?section=documents');

    await expect(page.getByRole('heading', { name: 'Upload supporting documents' })).toBeVisible();
    await expect(page.getByRole('button', { name: /^View / })).toHaveCount(0);
    await openSection(page, /^Approved Documents/);
    await expect(page.getByText('No approved documents yet.')).toBeVisible();
  });

  test('CM-049 the open section is kept after a reload @CM-049', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    await page.goto(fixture.caseUrl);
    await openSection(page, /^Approved Documents/);
    await expect(page).toHaveURL(/section=approved/);

    await page.reload();

    await expect(sectionNav(page).getByRole('button', { name: /^Approved Documents/ })).toHaveAttribute('aria-current', 'page');
    await expect(page.getByText('No approved documents yet.')).toBeVisible();
  });
});

/**
 * Sprint 5 regression (SCRUM-323), "Test Cases - Sprint 5.xlsx" Case Management sheet, Export Case Report
 * (SCRUM-82): the download itself. What the PDF holds (CM-075 – CM-085) is the backend's
 * test_regression_case_report.py, on the real report.
 */
test.describe('Export case report @sprint5 @SCRUM-82', () => {
  test('CM-075 / CM-079 the case menu downloads the report as a PDF named by the case, before any analysis @CM-075 @CM-079', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    await page.goto(fixture.caseUrl);

    await page.getByRole('button', { name: 'Case actions' }).click();
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('menuitem', { name: 'Download report' }).click(),
    ]);

    expect(download.suggestedFilename()).toBe('GLDB-02 Case Report.pdf');
    expect(backend.requestsTo('GET', `/cases/${fixture.case.id}/report.pdf`)).toHaveLength(1);
    await expect(page.getByRole('alert')).toHaveCount(0);
  });
});
