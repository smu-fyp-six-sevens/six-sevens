import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { allRowTexts, openGroup, openGroupWith } from '../support/documentGroups';
import { loadFixture } from '../support/fixtures';
import { makeCase, makeDocument } from '../support/mockBackend';

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
const REQUIRED_VALUES = [
  'id_document', 'bank_statement', 'proof_of_address', 'fatca_crs_declaration', 'certificate_of_incorporation',
  'certificate_of_incumbency', 'company_constitution', 'credit_report', 'payslip', 'source_of_wealth', 'notice_of_assessment',
];

// The Documents card is the checklist: what the case still lacks is listed in its Missing group, one row per
// required document, and everything the case has is a file row under the other groups.
const card = (page: Page) => page.locator('.documents-card');
const progress = (page: Page) => card(page).locator('.checklist-progress');
const missingRow = (page: Page, label: string) =>
  page.locator('.checklist-missing-row:not(.checklist-optional-row)').filter({
    has: page.locator('.document-type-cell', { hasText: new RegExp(`^${label.replace(/[()/]/g, '\\$&')}$`) }),
  });
// The optional documents the case has none of yet, listed in their own group.
const additionalNote = (page: Page) => page.locator('.checklist-additional');

test.describe('Classify optional documents @sprint2 @SCRUM-114', () => {
  test('DI-031 the required documents the case lacks are listed as missing, the optional ones in a note of their own @DI-031', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'harbor-light');
    await page.goto(`${fixture.caseUrl}?section=documents`);

    const fulfilled = new Set(fixture.case.fulfilled_requirements);
    await expect(page.locator('.checklist-missing-row:not(.checklist-optional-row)')).toHaveCount(REQUIRED.length - [...fulfilled].filter((value) => REQUIRED_VALUES.includes(value)).length);
    await expect(progress(page)).toContainText(`of ${REQUIRED.length} required`);
    // The optional documents are not part of the count, and only the ones still lacking are named.
    for (const label of ADDITIONAL) {
      await expect(page.locator('.checklist-missing-row:not(.checklist-optional-row)').filter({ hasText: label })).toHaveCount(0);
    }
    await openGroup(page, 'Additional');
    await expect(additionalNote(page)).toContainText('only if the case needs them');
    await expect(additionalNote(page)).toContainText('Company Proof of Address');
    await expect(additionalNote(page)).not.toContainText('List of Loans');
  });

  test('DI-033 uploaded optional documents are mapped to their optional requirement @DI-033', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    await page.goto(`${fixture.caseUrl}?section=documents`);

    // All three optional documents are in, so none is named as still lacking.
    await expect(additionalNote(page)).toHaveCount(0);
    const table = page.locator('.documents-table');
    for (const filename of ['FacilityLetter_AIB_2026-03-09_1.pdf', 'Company_UtilityBill_45040.pdf', 'SimS_SourceOfWealth.pdf']) {
      await openGroupWith(page, filename);
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
    await openGroup(page, 'Additional');
    await expect(additionalNote(page)).toContainText('Company Proof of Address');
    await expect(additionalNote(page)).not.toContainText('List of Loans');
    await expect(progress(page)).toContainText(`${REQUIRED.length} of ${REQUIRED.length} required`);
    await expect(page.locator('.checklist-missing-row:not(.checklist-optional-row)')).toHaveCount(0);

    // DI-032: none of the optional documents at all.
    removeDocuments('LoanFacility', 'LoanOffer', 'FacilityLetter', 'SimS_SourceOfWealth', 'Wealth_Statement_scan');
    await openGroup(page, 'Additional');
    for (const label of ADDITIONAL) {
      await expect(additionalNote(page)).toContainText(label);
    }
    await expect(progress(page)).toContainText(`${REQUIRED.length} of ${REQUIRED.length} required`);
    await expect(page.locator('.checklist-missing-row:not(.checklist-optional-row)')).toHaveCount(0);
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
      await openGroupWith(page, filename);
      const row = page.locator('.documents-table-row').filter({ hasText: filename });
      await expect(row.locator('.document-type-cell')).toHaveText(new RegExp(type, 'i'));
      await expect(missingRow(page, label)).toHaveCount(0);
    }
    await openGroupWith(page, 'C014_ListLoans');
    const loans = page.locator('.documents-table-row').filter({ hasText: 'C014_ListLoans' });
    await expect(loans.locator('.document-type-cell')).toHaveText(/list of loans/i);
    await expect(additionalNote(page)).not.toContainText('List of Loans');
  });

  test('DI-036 an NRIC (front + back) fulfils both identity document and proof of address @DI-036', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    await page.goto(`${fixture.caseUrl}?section=documents`);

    await expect(missingRow(page, 'Identity Document (NRIC/Passport)')).toHaveCount(0);
    await expect(missingRow(page, 'Personal Proof of Address')).toHaveCount(0);

    for (const side of ['MuhammadD_NRIC_front.jpg', 'MuhammadD_NRIC_back.jpg']) {
      await openGroupWith(page, side);
      await expect(page.locator('.documents-table-row').filter({ hasText: side }).locator('.document-type-cell')).toHaveText(/nric/i);
    }
  });

  test('DI-037 mapped documents are recorded against the correct case @DI-037', async ({ page, backend }) => {
    const harbor = loadFixture(backend, 'harbor-light');
    loadFixture(backend, 'anchorpoint');
    await page.goto(`${harbor.caseUrl}?section=documents`);

    // Every document of the case is listed once, across the categories (they open one at a time).
    const rows = await allRowTexts(page);
    expect(rows).toHaveLength(harbor.documents.length);
    for (const document of harbor.documents) {
      expect(rows.filter((text) => text.includes(document.filename))).toHaveLength(1);
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
      await expect(missingRow(page, label), `${label} ${fulfilled.has(value) ? 'is fulfilled' : 'is still missing'}`).toHaveCount(
        fulfilled.has(value) ? 0 : 1,
      );
    }
    await expect(progress(page)).toContainText(`4 of ${REQUIRED.length} required`);
  });

  test('DI-others-05 an NRIC with only one side uploaded says which side is missing @DI-others', async ({ page, backend }) => {
    // Muhammad's NRIC as the case's only identity (Sim's passport removed), one side at a time.
    const fixture = loadFixture(backend, 'anchorpoint');
    const all = [...backend.documents];
    const keepAllBut = (...parts: string[]) => {
      backend.documents = all.filter((d) => !parts.some((part) => d.filename.includes(part)));
    };
    const missingSide = missingRow(page, 'Identity Document (NRIC/Passport)').locator('.checklist-hint');

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

    const address = missingRow(page, 'Personal Proof of Address').locator('.checklist-hint');
    await expect(address).toHaveText('Address on file but no matching ID uploaded: Sim Shu Ting');
  });
});

test.describe('Documents categories open one at a time', () => {
  const groups = (page: Page) => card(page).locator('.documents-table-group--toggle');
  const group = (page: Page, name: string) => card(page).locator('.documents-table-group--toggle', { hasText: name });

  test('the first category opens on load, and opening another closes it', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'harbor-light');
    await page.goto(`${fixture.caseUrl}?section=documents`);

    // Missing comes first, so it is the one open.
    await expect(group(page, 'Missing')).toHaveAttribute('aria-expanded', 'true');
    await expect(groups(page).locator('xpath=self::*[@aria-expanded="true"]')).toHaveCount(1);
    await expect(page.locator('.checklist-missing-row:not(.checklist-optional-row)').first()).toBeVisible();

    await group(page, 'Pending Approval').click();

    await expect(group(page, 'Pending Approval')).toHaveAttribute('aria-expanded', 'true');
    await expect(group(page, 'Missing')).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('.checklist-missing-row:not(.checklist-optional-row)')).toHaveCount(0);
    await expect(groups(page).locator('xpath=self::*[@aria-expanded="true"]')).toHaveCount(1);

    // Opening the open one closes it, leaving none.
    await group(page, 'Pending Approval').click();
    await expect(groups(page).locator('xpath=self::*[@aria-expanded="true"]')).toHaveCount(0);
  });

  test('Additional is never the one opened on load: the next category opens instead, or none', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'harbor-light');
    // Every required document received: no Missing group, so Additional is the first category with rows.
    fixture.case.fulfilled_requirements = [...REQUIRED_VALUES, ...fixture.case.fulfilled_requirements];
    await page.goto(`${fixture.caseUrl}?section=documents`);

    await expect(group(page, 'Missing')).toHaveCount(0);
    await expect(group(page, 'Additional')).toHaveAttribute('aria-expanded', 'false');
    // The next category, Needs Review, is the one open.
    await expect(group(page, 'Needs Review')).toHaveAttribute('aria-expanded', 'true');
  });

  test('each category has its own colour, and a document waiting for approval has a yellow tick', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'harbor-light');
    await page.goto(`${fixture.caseUrl}?section=documents`);

    const edge = (name: string) => group(page, name).evaluate((el) => getComputedStyle(el).getPropertyValue('--group-edge').trim());
    const edges = await Promise.all(['Missing', 'Additional', 'Needs Review', 'Pending Approval'].map(edge));
    expect(new Set(edges).size).toBe(4);

    await group(page, 'Pending Approval').click();
    const mark = card(page).locator('.row-mark--pending').first();
    await expect(mark).toBeVisible();
    // Yellow, not the green an approved document gets.
    const colour = await mark.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(colour).toBe('rgb(198, 130, 10)');
  });
});

test.describe('A document that stands as proof of address says so', () => {
  const typeCell = (page: Page, filename: string) =>
    page.locator('.documents-table-row').filter({ hasText: filename }).locator('.document-type-cell');

  test('a personal bank statement reads "Bank Statement (Proof of Address)", a corporate one just "Bank Statement"', async ({ page, backend }) => {
    const theCase = makeCase({ id: 'case-poa', greenlink_case_id: 'GLDB-6001', company_name: 'Label Co' });
    backend.cases.push(theCase);
    const classified = { case_id: theCase.id, status: 'analyzed', classification_confidence: 0.97, routing_status: 'accepted' } as const;
    backend.documents.push(
      // What the backend works out once the fields are read: a personal statement showing an address is proof of
      // address (and not the company's bank statement); a corporate one is the bank statement.
      makeDocument({ ...classified, filename: 'personal_statement.pdf', document_type: 'bank_statement', fulfilled_requirements: ['proof_of_address'] }),
      makeDocument({ ...classified, filename: 'corporate_statement.pdf', document_type: 'bank_statement', fulfilled_requirements: ['bank_statement'] }),
      makeDocument({ ...classified, filename: 'utility_bill.pdf', document_type: 'proof_of_address', fulfilled_requirements: ['proof_of_address'] }),
      makeDocument({ ...classified, filename: 'nric_front.jpg', document_type: 'nric', fulfilled_requirements: ['id_document', 'proof_of_address'] }),
      makeDocument({ ...classified, filename: 'payslip.pdf', document_type: 'payslip', fulfilled_requirements: ['payslip'] }),
    );
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=documents`);
    await openGroupWith(page, 'personal_statement.pdf');

    await expect(typeCell(page, 'personal_statement.pdf')).toHaveText('Bank Statement (Proof of Address)');
    await expect(typeCell(page, 'corporate_statement.pdf')).toHaveText('Bank Statement');
    // Already named Proof of Address: not said twice.
    await expect(typeCell(page, 'utility_bill.pdf')).toHaveText('Proof of Address');
    await expect(typeCell(page, 'nric_front.jpg')).toHaveText('NRIC (Proof of Address)');
    await expect(typeCell(page, 'payslip.pdf')).toHaveText('Payslip');
  });

  test('a document not yet classified is not labelled', async ({ page, backend }) => {
    const theCase = makeCase({ id: 'case-poa2', greenlink_case_id: 'GLDB-6002', company_name: 'Label Co' });
    backend.cases.push(theCase);
    backend.documents.push(makeDocument({ case_id: theCase.id, filename: 'new.pdf', status: 'extracting' }));
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=documents`);
    await openGroupWith(page, 'new.pdf');

    await expect(typeCell(page, 'new.pdf')).toHaveText('Not classified');
  });
});
