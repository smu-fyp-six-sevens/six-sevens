import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { loadFixture } from '../support/fixtures';

/**
 * Document checklist (Sprint 2) — "Test Cases - Sprint 2.xlsx", Document
 * Ingestion sheet: Classify Optional Documents (SCRUM-114) DI-031 – DI-034
 * and Map Documents (SCRUM-118) DI-035 – DI-038.
 *
 * The original runs used cases C060/C064, which are no longer in Supabase;
 * these use the real GLDB-02 Anchorpoint case (every required and optional
 * document, two identities) and GLDB-16 Harbor Light (7 requirements still
 * missing) instead — see support/fixtures.ts.
 *
 * Which requirements a case has fulfilled is worked out by the backend
 * (models.compute_fulfilled_requirements, mapper.py, case_status.py) and
 * regression-tested there against the same fixtures
 * (backend/tests/test_regression_checklist.py); this spec checks the page
 * shows that result correctly. When a test removes documents, the mock serves
 * the checklist the backend computed for exactly the documents left
 * (checklist-variants.json), never one the test sets itself.
 */

const REQUIRED = [
  'Identity Document (NRIC/Passport)',
  'Corporate Bank Statement',
  'Personal Proof of Address',
  'FATCA/CRS Declaration',
  'Certificate of Incorporation',
  'Certificate of Incumbency',
  'Company Constitution',
  'Credit Report',
  'Payslips',
  'Source of Corporate Wealth',
  'Notice of Assessment',
];
const ADDITIONAL = ['Company Proof of Address', 'List of Loans', 'Source of Personal Wealth'];

const sidebar = (page: Page) => page.locator('.upload-sidebar');
const requiredList = (page: Page) => sidebar(page).locator('.requirements-list').first();
const additionalList = (page: Page) => sidebar(page).locator('.requirements-list').nth(1);
const requirement = (list: ReturnType<typeof requiredList>, label: string) =>
  list.locator('.requirement-row').filter({ hasText: new RegExp(`^${label.replace(/[()/]/g, '\\$&')}`) });

test.describe('Classify optional documents @sprint2 @SCRUM-114', () => {
  test('DI-031 required and optional documents are shown as separate requirement types @DI-031', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    await page.goto(`${fixture.caseUrl}?section=documents`);

    await expect(sidebar(page).getByText('Required documents')).toBeVisible();
    await expect(sidebar(page).getByText('Present in every case')).toBeVisible();
    await expect(sidebar(page).getByText('Additional Documents')).toBeVisible();
    await expect(sidebar(page).getByText('May be required depending on the case')).toBeVisible();

    await expect(requiredList(page).locator(':scope > li')).toHaveCount(REQUIRED.length);
    await expect(additionalList(page).locator(':scope > li')).toHaveCount(ADDITIONAL.length);
    for (const label of ADDITIONAL) {
      await expect(requirement(additionalList(page), label)).toBeVisible();
      await expect(requirement(requiredList(page), label)).toHaveCount(0);
    }
  });

  test('DI-033 uploaded optional documents are mapped to their optional requirement @DI-033', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    await page.goto(`${fixture.caseUrl}?section=documents`);

    for (const label of ADDITIONAL) {
      await expect(requirement(additionalList(page), label)).toHaveClass(/fulfilled/);
    }
    const table = page.locator('.documents-table');
    for (const filename of ['FacilityLetter_AIB_2026-03-09_1.pdf', 'Company_UtilityBill_45040.pdf', 'SimS_SourceOfWealth.pdf']) {
      await expect(table.getByText(filename)).toBeVisible();
    }
  });

  test('DI-032 / DI-034 missing optional documents leave every required document ticked and the case ready for review @DI-032 @DI-034', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    // The stage the backend gives a case whose documents wait for the officer (the capture predates stages).
    fixture.case.status = 'pending_review';
    const removeDocuments = (...parts: string[]) => {
      backend.documents = backend.documents.filter((d) => !parts.some((part) => d.filename.includes(part)));
      backend.push(fixture.case.id);
    };
    await page.goto(`${fixture.caseUrl}?section=documents`);

    // DI-034: every required document, and only some of the optional ones.
    removeDocuments('Company_UtilityBill');
    await expect(requirement(additionalList(page), 'Company Proof of Address')).not.toHaveClass(/fulfilled/);
    await expect(requirement(additionalList(page), 'List of Loans')).toHaveClass(/fulfilled/);
    await expect(sidebar(page).locator('.section-label').first()).toContainText(`${REQUIRED.length} / ${REQUIRED.length}`);
    for (const label of REQUIRED) {
      await expect(requirement(requiredList(page), label)).toHaveClass(/fulfilled/);
    }

    // DI-032: none of the optional documents at all.
    removeDocuments('LoanFacility', 'LoanOffer', 'FacilityLetter', 'SimS_SourceOfWealth', 'Wealth_Statement_scan');
    for (const label of ADDITIONAL) {
      await expect(requirement(additionalList(page), label)).not.toHaveClass(/fulfilled/);
    }
    await expect(sidebar(page).locator('.section-label').first()).toContainText(`${REQUIRED.length} / ${REQUIRED.length}`);
    await page.goto('/cases');
    await expect(page.locator('.case-row').filter({ hasText: 'Anchorpoint' })).toContainText('Pending Review');
  });
});

test.describe('Map documents @sprint2 @SCRUM-118', () => {
  test('DI-035 each document shows its identified type, and its requirement is ticked @DI-035', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'harbor-light');
    await page.goto(`${fixture.caseUrl}?section=documents`);

    const expected: [string, string, string][] = [
      ['C014_CertIncorp.pdf', 'Certificate Of Incorporation', 'Certificate of Incorporation'],
      ['C014_CertIncumbency.pdf', 'Certificate Of Incumbency', 'Certificate of Incumbency'],
      ['C014_SourceWealth_BS.pdf', 'Source Of Wealth', 'Source of Corporate Wealth'],
      ['Passport_5187.jpeg', 'Passport', 'Identity Document (NRIC/Passport)'],
    ];
    for (const [filename, type, label] of expected) {
      const row = page.locator('.documents-table-row').filter({ hasText: filename });
      await expect(row.locator('.document-type-cell')).toHaveText(new RegExp(type, 'i'));
      await expect(requirement(requiredList(page), label)).toHaveClass(/fulfilled/);
    }
    const loans = page.locator('.documents-table-row').filter({ hasText: 'C014_ListLoans' });
    await expect(loans.locator('.document-type-cell')).toHaveText(/list of loans/i);
    await expect(requirement(additionalList(page), 'List of Loans')).toHaveClass(/fulfilled/);
  });

  test('DI-036 an NRIC (front + back) fulfils both identity document and proof of address @DI-036', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    await page.goto(`${fixture.caseUrl}?section=documents`);

    const identity = requirement(requiredList(page), 'Identity Document (NRIC/Passport)');
    await expect(identity).toHaveClass(/fulfilled/);
    await expect(identity.locator('.requirement-progress')).toHaveText('(2/2)');
    const address = requirement(requiredList(page), 'Personal Proof of Address');
    await expect(address).toHaveClass(/fulfilled/);
    await expect(address.locator('.requirement-progress')).toHaveText('(2/2)');

    for (const side of ['MuhammadD_NRIC_front.jpg', 'MuhammadD_NRIC_back.jpg']) {
      await expect(page.locator('.documents-table-row').filter({ hasText: side }).locator('.document-type-cell')).toHaveText(/nric/i);
    }
  });

  test('DI-037 mapped documents are recorded against the correct case @DI-037', async ({ page, backend }) => {
    const harbor = loadFixture(backend, 'harbor-light');
    loadFixture(backend, 'anchorpoint');
    await page.goto(`${harbor.caseUrl}?section=documents`);

    const rows = page.locator('.documents-table-row');
    await expect(rows).toHaveCount(harbor.documents.length);
    for (const document of harbor.documents) {
      await expect(rows.filter({ hasText: document.filename })).toHaveCount(1);
    }
    await expect(page.getByText('MuhammadD_NRIC_front.jpg')).toHaveCount(0);
  });

  test('DI-038 a requirement a document fulfils is not shown as missing @DI-038', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'harbor-light');
    await page.goto(`${fixture.caseUrl}?section=documents`);

    const fulfilled = new Set(fixture.case.fulfilled_requirements);
    const labelByValue: Record<string, string> = {
      id_document: 'Identity Document (NRIC/Passport)',
      certificate_of_incorporation: 'Certificate of Incorporation',
      certificate_of_incumbency: 'Certificate of Incumbency',
      source_of_wealth: 'Source of Corporate Wealth',
      bank_statement: 'Corporate Bank Statement',
      payslip: 'Payslips',
      fatca_crs_declaration: 'FATCA/CRS Declaration',
      company_constitution: 'Company Constitution',
      proof_of_address: 'Personal Proof of Address',
      credit_report: 'Credit Report',
      notice_of_assessment: 'Notice of Assessment',
    };
    for (const [value, label] of Object.entries(labelByValue)) {
      const row = requirement(requiredList(page), label);
      if (fulfilled.has(value)) {
        await expect(row, `${label} is fulfilled`).toHaveClass(/fulfilled/);
      } else {
        await expect(row, `${label} is still missing`).not.toHaveClass(/fulfilled/);
      }
    }
    await expect(sidebar(page).locator('.section-label').first()).toContainText(`4 / ${REQUIRED.length}`);
  });

  test('DI-others-05 an NRIC with only one side uploaded says which side is missing @DI-others', async ({ page, backend }) => {
    // Muhammad's NRIC as the case's only identity (Sim's passport removed), one side at a time.
    const fixture = loadFixture(backend, 'anchorpoint');
    const all = [...backend.documents];
    const keepAllBut = (...parts: string[]) => {
      backend.documents = all.filter((d) => !parts.some((part) => d.filename.includes(part)));
    };
    const missingSide = requiredList(page).locator('li', { hasText: 'Identity Document (NRIC/Passport)' }).locator('.requirement-unmatched');

    keepAllBut('Passport_6086', 'MuhammadD_NRIC_back');
    await page.goto(`${fixture.caseUrl}?section=documents`);
    await expect(missingSide).toHaveText('Front photo present, back missing.');

    keepAllBut('Passport_6086', 'MuhammadD_NRIC_front');
    await page.goto(`${fixture.caseUrl}?section=documents`);
    await expect(missingSide).toHaveText('Back photo present, front missing.');
  });

  test('DI-others-06 an address on file with no matching ID is named in the proof of address requirement @DI-others', async ({ page, backend }) => {
    // Sim Shu Ting's address is on file, but her passport is removed.
    const fixture = loadFixture(backend, 'anchorpoint');
    backend.documents = backend.documents.filter((d) => !d.filename.includes('Passport_6086'));
    await page.goto(`${fixture.caseUrl}?section=documents`);

    const address = requiredList(page).locator('li', { hasText: 'Personal Proof of Address' }).locator('.requirement-unmatched');
    await expect(address).toHaveText('Address on file but no matching ID uploaded: Sim Shu Ting');
  });
});
