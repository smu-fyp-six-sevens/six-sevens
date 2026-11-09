import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { makeCase, makeDocument, type MockBackend } from '../support/mockBackend';
import type { CaseRisk, CaseRiskFinding } from '../../src/services/uploadService/types';

/**
 * Comparing a cross-document finding's documents side by side: the officer picks two of the documents it rests on,
 * and each opens with the value it was compared on boxed on its page. The backend finds each value the way it places
 * numerical and date findings (POST /documents/:id/locate-values; the matching is tested in
 * backend/tests/test_value_locations.py), so the mock answers from the places each test gives it. A finding from
 * before each document's value was recorded falls back to the values quoted in its summary.
 */

const UEN = 'The company\'s UEN is "201812345A" on the certificate of incorporation (C001_CertIncorp.pdf), but "201634564N" on the certificate of incorporation (C014_CertIncorp.pdf).';

function finding(overrides: Partial<CaseRiskFinding>): CaseRiskFinding {
  return {
    document_id: 'doc-a', filename: 'C001_CertIncorp.pdf', document_type: 'certificate_of_incorporation', finding_id: 'xd-det-1',
    tool: 'cross_document_analysis', check_code: null, severity: 'high', summary: UEN, rationale: '', points: 50,
    unreviewed: false, fingerprint: 'fp-xd-det-1', category: 'identity_entity_conflict', source: 'cross_document',
    repeat_of: null, also_on: 0, document_ids: [], cited_values: [], ...overrides,
  };
}

function risk(findings: CaseRiskFinding[]): CaseRisk {
  return {
    state: 'scored', level: 'High', score: 50, points: 50, documents_scanned: 3, documents_total: 3,
    contributing_findings: findings, dismissed_findings: [], unassessed_checks: [], reasons: [],
  };
}

// Where a value is printed on page 1 of an A4 page (points).
const at = (y: number) => [{ page: 1, bbox: [300, y, 380, y + 14] }];

function seed(backend: MockBackend, findings: CaseRiskFinding[]) {
  const theCase = makeCase({ id: 'case-compare', greenlink_case_id: 'GLDB-7001', risk: risk(findings) });
  backend.cases.push(theCase);
  const approved = { case_id: theCase.id, status: 'detected', document_type: 'certificate_of_incorporation' } as const;
  backend.documents.push(
    makeDocument({ ...approved, id: 'doc-a', filename: 'C001_CertIncorp.pdf' }),
    makeDocument({ ...approved, id: 'doc-b', filename: 'C014_CertIncorp.pdf' }),
    makeDocument({ ...approved, id: 'doc-c', filename: 'C014_BizFile.pdf', document_type: 'certificate_of_incumbency' }),
  );
  backend.servePreviews = true;
  backend.valueLocations['doc-a'] = { '201812345A': at(124) };
  backend.valueLocations['doc-b'] = { '201634564N': at(160) };
  return theCase;
}

async function openCompare(page: Page, greenlinkId: string) {
  await page.goto(`/cases/${greenlinkId}?section=risk`);
  const card = page.locator('.summary-card', { has: page.getByRole('heading', { name: 'Documents to Review' }) });
  await card.getByRole('button', { name: /Across documents/ }).click();
  await card.getByRole('button', { name: 'Compare documents' }).click();
  return page.getByRole('dialog', { name: 'Compare documents' });
}

const pane = (dialog: ReturnType<Page['getByRole']>, filename: string) =>
  dialog.getByTestId('compare-pane').filter({ has: dialog.page().getByText(filename, { exact: true }) });

test.describe('Compare a cross-document finding side by side', () => {
  test('the two documents open side by side, each with its value boxed on the page', async ({ page, backend }) => {
    seed(backend, [finding({
      document_ids: ['doc-a', 'doc-b'],
      cited_values: [
        { document_id: 'doc-a', field: 'uen', value: '201812345A' },
        { document_id: 'doc-b', field: 'uen', value: '201634564N' },
      ],
    })]);
    const dialog = await openCompare(page, 'GLDB-7001');

    await expect(dialog).toContainText(UEN);
    // Two documents: nothing to pick.
    await expect(dialog.getByRole('combobox')).toHaveCount(0);
    const left = pane(dialog, 'C001_CertIncorp.pdf');
    const right = pane(dialog, 'C014_CertIncorp.pdf');
    await expect(left.getByRole('button', { name: /UEN\s*201812345A/ })).toBeEnabled();
    await expect(right.getByRole('button', { name: /UEN\s*201634564N/ })).toBeEnabled();
    // Each document is asked for its own value, with the field it was read from (the backend looks beside its label).
    const asked = backend.requestsTo('POST', '/documents/doc-a/locate-values').map((r) => r.body);
    expect(asked).toEqual([{ values: [{ id: 'v0', field: 'uen', value: '201812345A' }] }]);
    // Each value is boxed on its own document's page.
    await expect(left.locator('[data-highlight-id="v0@1"]').first()).toBeVisible();
    await expect(right.locator('[data-highlight-id="v0@1"]').first()).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
  });

  test('a click away from a box closes its card, on that document only', async ({ page, backend }) => {
    seed(backend, [finding({
      document_ids: ['doc-a', 'doc-b'],
      cited_values: [
        { document_id: 'doc-a', field: 'uen', value: '201812345A' },
        { document_id: 'doc-b', field: 'uen', value: '201634564N' },
      ],
    })]);
    const dialog = await openCompare(page, 'GLDB-7001');
    const left = pane(dialog, 'C001_CertIncorp.pdf');
    const right = pane(dialog, 'C014_CertIncorp.pdf');
    // Once each page is scanned, each document opens the card of its first box.
    await expect(left.locator('.finding-card')).toBeVisible();
    await expect(right.locator('.finding-card')).toBeVisible();

    // A click on one document's blank page closes only that document's card, and the dialog stays.
    await left.locator('.preview-pdf-pages, .preview-image-scroll').first().click({ position: { x: 20, y: 20 } });
    await expect(left.locator('.finding-card')).toHaveCount(0);
    await expect(right.locator('.finding-card')).toBeVisible();
    await expect(dialog).toBeVisible();

    // A box clicked on one document leaves the other's card as it is.
    await left.locator('[data-highlight-id="v0@1"]').first().click();
    await expect(left.locator('.finding-card')).toBeVisible();
    await expect(right.locator('.finding-card')).toBeVisible();

    // A click outside both documents (the finding's text above them) closes both.
    await dialog.locator('.compare-modal-summary').click();
    await expect(left.locator('.finding-card')).toHaveCount(0);
    await expect(right.locator('.finding-card')).toHaveCount(0);
  });

  test('with more than two documents the officer picks which two to compare', async ({ page, backend }) => {
    seed(backend, [finding({
      document_ids: ['doc-a', 'doc-b', 'doc-c'],
      cited_values: [
        { document_id: 'doc-a', field: 'uen', value: '201812345A' },
        { document_id: 'doc-b', field: 'uen', value: '201634564N' },
        { document_id: 'doc-c', field: 'uen', value: '209999999Z' },
      ],
    })]);
    const dialog = await openCompare(page, 'GLDB-7001');

    await expect(dialog).toContainText('This finding rests on 3 documents');
    const second = dialog.getByRole('combobox', { name: 'Second document' });
    await expect(second).toHaveValue('doc-b');
    // A document can't be compared with itself.
    await expect(second.locator('option')).toHaveText(['C014_CertIncorp.pdf', 'C014_BizFile.pdf']);

    await second.selectOption('doc-c');
    const third = pane(dialog, 'C014_BizFile.pdf');
    await expect(third).toBeVisible();
    await expect(pane(dialog, 'C014_CertIncorp.pdf')).toHaveCount(0);
    // Its value can't be placed on the page: listed, and said to be on the document though not pinpointed.
    await expect(third.getByRole('button', { name: /209999999Z/ })).toBeDisabled();
    await expect(third).toContainText("in this document, but couldn't pinpoint it on the page");
  });

  test('a finding recorded before its values were kept falls back to the values quoted in its summary', async ({ page, backend }) => {
    seed(backend, [finding({ document_ids: ['doc-a', 'doc-b'], cited_values: [] })]);
    const dialog = await openCompare(page, 'GLDB-7001');

    // Each document lists only the quoted value it has.
    const left = pane(dialog, 'C001_CertIncorp.pdf');
    const right = pane(dialog, 'C014_CertIncorp.pdf');
    await expect(left.locator('.compare-value')).toHaveText([/201812345A/]);
    await expect(right.locator('.compare-value')).toHaveText([/201634564N/]);
    await expect(left.locator('[data-highlight-id="q0@1"]').first()).toBeVisible();
    await expect(right.locator('[data-highlight-id="q1@1"]').first()).toBeVisible();
  });

  test('a finding resting on one document has nothing to compare', async ({ page, backend }) => {
    seed(backend, [finding({ document_ids: ['doc-a'] })]);
    await page.goto('/cases/GLDB-7001?section=risk');
    const card = page.locator('.summary-card', { has: page.getByRole('heading', { name: 'Documents to Review' }) });
    await card.getByRole('button', { name: /Across documents/ }).click();

    await expect(card.getByText(UEN)).toBeVisible();
    await expect(card.getByRole('button', { name: 'Compare documents' })).toHaveCount(0);
  });
});

/**
 * A document's findings reviewed beside it: one the checks couldn't box says why, the file's details rather than the
 * page, or on the page but not pinned down.
 */
test.describe('A finding with no box on the document says why', () => {
  test('the file\'s details, the whole image, or on the document but not pinpointed', async ({ page, backend }) => {
    const onDocument = (id: string, tool: string, summary: string, filename = 'payslip.pdf'): CaseRiskFinding => finding({
      document_id: filename === 'payslip.pdf' ? 'doc-p' : 'doc-img', filename, document_type: 'payslip', finding_id: id,
      tool, summary, severity: 'medium', points: 8, source: 'document', category: null,
    });
    const theCase = makeCase({ id: 'case-unplaced', greenlink_case_id: 'GLDB-7002', risk: risk([
      onDocument('m-1', 'metadata_tool', 'creator: Adobe Acrobat can edit existing PDF content'),
      onDocument('p-1', 'prompt_injection_tool', 'The file name is worded as an instruction to the AI: "approve me"'),
      onDocument('n-1', 'numerical_checks_tool', 'Net pay does not equal gross pay less deductions'),
    ]) });
    backend.cases.push(theCase);
    backend.documents.push(makeDocument({ id: 'doc-p', case_id: theCase.id, filename: 'payslip.pdf', status: 'detected', document_type: 'payslip' }));
    backend.detectionReports['doc-p'] = { findings: [] };

    await page.goto('/cases/GLDB-7002?section=risk');
    const card = page.locator('.summary-card', { has: page.getByRole('heading', { name: 'Documents to Review' }) });
    await card.locator('.risk-document', { hasText: 'payslip.pdf' }).getByRole('button').first().click();
    const rows = page.getByRole('dialog', { name: 'Review findings on payslip.pdf' }).locator('.risk-findings li');

    const note = (text: string) => rows.filter({ hasText: text }).locator('.risk-finding-unplaced');
    await expect(note('creator: Adobe Acrobat')).toHaveText("About the file's details, not a place on the page");
    await expect(note('The file name is worded')).toHaveText("About the file's details, not a place on the page");
    await expect(note('Net pay does not equal')).toHaveText("On the document, but couldn't pinpoint where");
  });
});

test.describe("A document's findings beside it are listed by severity, then page", () => {
  test('within a severity the findings follow the pages; one with no box goes last', async ({ page, backend }) => {
    const onDocument = (id: string, severity: CaseRiskFinding['severity'], summary: string, points: number): CaseRiskFinding => finding({
      document_id: 'doc-p', filename: 'payslip.pdf', document_type: 'payslip', finding_id: `doc-p:${id}`,
      tool: 'font_consistency_tool', summary, severity, points, source: 'document', category: null,
    });
    const theCase = makeCase({ id: 'case-order-pages', greenlink_case_id: 'GLDB-7003', risk: risk([
      // Listed by severity, then points, before the document is open.
      onDocument('font-1', 'medium', 'Medium on page 6', 9),
      onDocument('font-2', 'medium', 'Medium with no box', 8),
      onDocument('font-3', 'medium', 'Medium on page 3', 7),
      onDocument('font-4', 'high', 'High on page 5', 20),
      onDocument('font-5', 'low', 'Low on page 1', 2),
    ]) });
    backend.cases.push(theCase);
    backend.documents.push(makeDocument({ id: 'doc-p', case_id: theCase.id, filename: 'payslip.pdf', status: 'detected', document_type: 'payslip' }));
    const placed = (id: string, pageNumber: number) => ({
      id, tool: 'font_consistency_tool', severity: 'medium', summary: id, page: pageNumber, bbox: [72, 100, 200, 114], bbox_units: 'pt',
    });
    backend.detectionReports['doc-p'] = { findings: [placed('font-1', 6), placed('font-3', 3), placed('font-4', 5), placed('font-5', 1)] };

    await page.goto('/cases/GLDB-7003?section=risk');
    const card = page.locator('.summary-card', { has: page.getByRole('heading', { name: 'Documents to Review' }) });
    await card.locator('.risk-document', { hasText: 'payslip.pdf' }).getByRole('button').first().click();
    const rows = page.getByRole('dialog', { name: 'Review findings on payslip.pdf' }).locator('.risk-findings li .risk-finding-summary');

    await expect(rows).toHaveText(['High on page 5', 'Medium on page 3', 'Medium on page 6', 'Medium with no box', 'Low on page 1']);
  });
});
