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

// The checklist is the panel behind the case header's Checklist button: what the case still lacks (Missing), what
// it has (Uploaded), what the case analysis asked for, and the optional documents. The Documents card lists files.
const card = (page: Page) => page.getByTestId('documents-card');
const checklistButton = (page: Page) => page.getByTestId('document-checklist').getByRole('button', { name: /Checklist/ });
const progress = (page: Page) => page.locator('.doc-checklist-head');
const missing = (page: Page) => page.getByTestId('missing-document');
const missingRow = (page: Page, label: string) =>
  missing(page).filter({
    has: page.locator('.doc-checklist-name', { hasText: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')}$`) }),
  });
// An optional document's line: its name, and "uploaded" or "not uploaded" (for screen readers).
const optional = (page: Page, label: string) => page.getByTestId('optional-document').filter({ hasText: label });

/** Opens the checklist panel and keeps it open (a click pins it). */
async function openChecklist(page: Page): Promise<void> {
  await checklistButton(page).click();
  await expect(page.getByRole('dialog', { name: 'Document checklist' })).toBeVisible();
}

/** Closes the checklist, so it doesn't sit over the Documents card. */
async function closeChecklist(page: Page): Promise<void> {
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'Document checklist' })).toHaveCount(0);
}

test.describe('Classify optional documents @sprint2 @SCRUM-114', () => {
  test('DI-031 the required documents the case lacks are listed as missing, the optional ones apart @DI-031', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'harbor-light');
    await page.goto(`${fixture.caseUrl}?section=documents`);
    await openChecklist(page);

    const fulfilled = new Set(fixture.case.fulfilled_requirements);
    await expect(missing(page)).toHaveCount(REQUIRED.length - [...fulfilled].filter((value) => REQUIRED_VALUES.includes(value)).length);
    await expect(progress(page)).toContainText(`of ${REQUIRED.length} required`);
    // The optional documents are not part of the count or the Missing list; each says whether it is in.
    for (const label of ADDITIONAL) {
      await expect(missing(page).filter({ hasText: label })).toHaveCount(0);
    }
    await expect(optional(page, 'Company Proof of Address')).toContainText('not uploaded');
    await expect(optional(page, 'List of Loans')).not.toContainText('not uploaded');
  });

  test('DI-033 uploaded optional documents are mapped to their optional requirement @DI-033', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    await page.goto(`${fixture.caseUrl}?section=documents`);

    // All three optional documents are in, so none reads as not uploaded.
    await openChecklist(page);
    for (const label of ADDITIONAL) {
      await expect(optional(page, label)).not.toContainText('not uploaded');
    }
    await closeChecklist(page);
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
    await openChecklist(page);

    // DI-034: every required document, and only some of the optional ones.
    removeDocuments('Company_UtilityBill');
    await expect(optional(page, 'Company Proof of Address')).toContainText('not uploaded');
    await expect(optional(page, 'List of Loans')).not.toContainText('not uploaded');
    await expect(progress(page)).toContainText(`${REQUIRED.length} of ${REQUIRED.length} required`);
    await expect(missing(page)).toHaveCount(0);

    // DI-032: none of the optional documents at all.
    removeDocuments('LoanFacility', 'LoanOffer', 'FacilityLetter', 'SimS_SourceOfWealth', 'Wealth_Statement_scan');
    for (const label of ADDITIONAL) {
      await expect(optional(page, label)).toContainText('not uploaded');
    }
    await expect(progress(page)).toContainText(`${REQUIRED.length} of ${REQUIRED.length} required`);
    await expect(missing(page)).toHaveCount(0);
    await page.goto('/cases');
    await expect(page.getByTestId('case-row').filter({ hasText: 'Anchorpoint' })).toContainText('Pending Review');
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
    for (const [filename, type] of expected) {
      await openGroupWith(page, filename);
      const row = page.getByTestId('document-row').filter({ hasText: filename });
      await expect(row.locator('.document-type-cell')).toHaveText(new RegExp(type, 'i'));
    }
    await openGroupWith(page, 'C014_ListLoans');
    const loans = page.getByTestId('document-row').filter({ hasText: 'C014_ListLoans' });
    await expect(loans.locator('.document-type-cell')).toHaveText(/list of loans/i);

    await openChecklist(page);
    for (const [, , label] of expected) {
      await expect(missingRow(page, label)).toHaveCount(0);
      await expect(page.getByTestId('received-document').filter({ hasText: label })).toHaveCount(1);
    }
    await expect(optional(page, 'List of Loans')).not.toContainText('not uploaded');
  });

  test('DI-036 an NRIC (front + back) fulfils both identity document and proof of address @DI-036', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    await page.goto(`${fixture.caseUrl}?section=documents`);

    for (const side of ['MuhammadD_NRIC_front.jpg', 'MuhammadD_NRIC_back.jpg']) {
      await openGroupWith(page, side);
      await expect(page.getByTestId('document-row').filter({ hasText: side }).locator('.document-type-cell')).toHaveText(/nric/i);
    }
    await openChecklist(page);
    await expect(missingRow(page, 'Identity Document (NRIC/Passport)')).toHaveCount(0);
    await expect(missingRow(page, 'Personal Proof of Address')).toHaveCount(0);
  });

  test('DI-037 mapped documents are recorded against the correct case @DI-037', async ({ page, backend }) => {
    const harbor = loadFixture(backend, 'harbor-light');
    loadFixture(backend, 'anchorpoint');
    await page.goto(`${harbor.caseUrl}?section=documents`);

    // Every document of the case is listed once, across the groups.
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
    await openChecklist(page);

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
    const missingSide = missingRow(page, 'Identity Document (NRIC/Passport)').locator('.doc-checklist-hint');

    keepAllBut('Passport_6086', 'MuhammadD_NRIC_back');
    await page.goto(`${fixture.caseUrl}?section=documents`);
    await openChecklist(page);
    await expect(missingSide).toHaveText('Front photo present, back missing.');

    keepAllBut('Passport_6086', 'MuhammadD_NRIC_front');
    await page.goto(`${fixture.caseUrl}?section=documents`);
    await openChecklist(page);
    await expect(missingSide).toHaveText('Back photo present, front missing.');
  });

  test('DI-others-06 an address on file with no matching ID is named in the proof of address requirement @DI-others', async ({ page, backend }) => {
    // Sim Shu Ting's address is on file, but her passport is removed.
    const fixture = loadFixture(backend, 'anchorpoint');
    backend.documents = backend.documents.filter((d) => !d.filename.includes('Passport_6086'));
    await page.goto(`${fixture.caseUrl}?section=documents`);
    await openChecklist(page);

    const address = missingRow(page, 'Personal Proof of Address').locator('.doc-checklist-hint');
    await expect(address).toHaveText('Address on file but no matching ID uploaded: Sim Shu Ting');
  });
});

test.describe('The checklist opens on hover and stays open on a click', () => {
  test('hover shows it; a click keeps it after the pointer leaves; a click outside closes it', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'harbor-light');
    await page.goto(`${fixture.caseUrl}?section=documents`);
    const panel = page.getByRole('dialog', { name: 'Document checklist' });

    await checklistButton(page).hover();
    await expect(panel).toBeVisible();
    await card(page).hover();
    await expect(panel).toHaveCount(0);

    await checklistButton(page).click();
    await card(page).hover();
    await expect(panel).toBeVisible();
    await page.locator('.documents-card-title').click();
    await expect(panel).toHaveCount(0);
  });

  test('it is on the Documents tab only', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'harbor-light');
    await page.goto(`${fixture.caseUrl}?section=documents`);
    await expect(page.getByTestId('document-checklist')).toBeVisible();

    await page.goto(`${fixture.caseUrl}?section=summary`);
    await expect(page.getByRole('button', { name: 'Case actions' })).toBeVisible();
    await expect(page.getByTestId('document-checklist')).toHaveCount(0);
  });
});

test.describe('Documents are grouped by where they stand', () => {
  const group = (page: Page, name: string) => card(page).getByTestId('document-group').filter({ hasText: name });

  test('Uploaded and Approved start open, Rejected folded; each has its own colour; a document ready to approve has a yellow tick', async ({ page, backend }) => {
    const theCase = makeCase({ id: 'case-groups', greenlink_case_id: 'GLDB-6004', company_name: 'Group Co' });
    backend.cases.push(theCase);
    const ready = {
      case_id: theCase.id, classification_confidence: 0.97, routing_status: 'accepted',
      extracted_fields: { company_name: { id: 'field-1', value: 'Group Co', confidence: 0.98 } },
    } as const;
    backend.documents.push(
      makeDocument({ ...ready, filename: 'ready.pdf', status: 'analyzed', document_type: 'fatca_crs_declaration', fulfilled_requirements: ['fatca_crs_declaration'] }),
      makeDocument({ ...ready, filename: 'approved.pdf', status: 'approved', document_type: 'certificate_of_incumbency', fulfilled_requirements: ['certificate_of_incumbency'] }),
      makeDocument({ ...ready, filename: 'rejected.pdf', status: 'rejected', document_type: 'credit_report', fulfilled_requirements: [] }),
    );
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=documents`);

    await expect(group(page, 'Uploaded')).toHaveAttribute('aria-expanded', 'true');
    await expect(group(page, 'Approved')).toHaveAttribute('aria-expanded', 'true');
    await expect(group(page, 'Rejected')).toHaveAttribute('aria-expanded', 'false');

    const edge = (name: string) =>
      card(page).locator(`.documents-table-band:has([data-testid="document-group"]:has-text("${name}"))`)
        .evaluate((el) => getComputedStyle(el).getPropertyValue('--group-edge').trim());
    const edges = await Promise.all(['Uploaded', 'Approved', 'Rejected'].map(edge));
    expect(new Set(edges).size).toBe(3);

    const mark = card(page).locator('.row-mark--pending').first();
    await expect(mark).toBeVisible();
    // Yellow, not the green an approved document gets.
    const colour = await mark.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(colour).toBe('rgb(198, 130, 10)');
  });
});

test.describe('A document that stands as proof of address says so', () => {
  const typeCell = (page: Page, filename: string) =>
    page.getByTestId('document-row').filter({ hasText: filename }).locator('.document-type-cell');

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

test.describe('Documents are listed A to Z by type', () => {
  test('within a group, by the type as listed, then filename; unclassified last', async ({ page, backend }) => {
    const theCase = makeCase({ id: 'case-az', greenlink_case_id: 'GLDB-6003', company_name: 'Order Co' });
    backend.cases.push(theCase);
    const ready = {
      case_id: theCase.id, status: 'analyzed', classification_confidence: 0.97, routing_status: 'accepted',
      extracted_fields: { company_name: { id: 'field-1', value: 'Order Co', confidence: 0.98 } },
    } as const;
    backend.documents.push(
      makeDocument({ ...ready, filename: 'z_nric.png', document_type: 'nric', fulfilled_requirements: ['id_document', 'proof_of_address'] }),
      makeDocument({ ...ready, filename: 'b_fatca.pdf', document_type: 'fatca_crs_declaration', fulfilled_requirements: ['fatca_crs_declaration'] }),
      makeDocument({ ...ready, filename: 'a_nric.png', document_type: 'nric', fulfilled_requirements: ['id_document'] }),
      makeDocument({ ...ready, filename: 'c_statement.pdf', document_type: 'bank_statement', fulfilled_requirements: ['proof_of_address'] }),
      makeDocument({ ...ready, filename: 'd_incumbency.pdf', document_type: 'certificate_of_incumbency', fulfilled_requirements: ['certificate_of_incumbency'] }),
      makeDocument({ case_id: theCase.id, filename: 'e_new.pdf', status: 'extracting' }),
    );
    await page.goto(`/cases/${theCase.greenlink_case_id}?section=documents`);
    await openGroup(page, 'Uploaded');

    await expect(card(page).getByTestId('document-row').locator('.document-filename-line')).toHaveText([
      'c_statement.pdf', // Bank Statement (Proof of Address)
      'd_incumbency.pdf', // Certificate of Incumbency
      'b_fatca.pdf', // FATCA/CRS Declaration
      'a_nric.png', // NRIC
      'z_nric.png', // NRIC (Proof of Address)
      'e_new.pdf', // not classified yet
    ]);
  });
});

test.describe('While the case loads', () => {
  test('the section shows a loading placeholder, not a list of everything missing', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'harbor-light');
    let release!: () => void;
    backend.snapshotGate = new Promise((resolve) => { release = resolve; });
    await page.goto(`${fixture.caseUrl}?section=documents`);

    // The whole section is a loading placeholder: no upload box, no checklist, nothing listed as missing.
    await expect(page.getByTestId('documents-skeleton')).toBeVisible();
    await expect(card(page)).toHaveCount(0);
    await expect(page.getByTestId('document-checklist')).toHaveCount(0);

    release();
    await expect(page.getByTestId('documents-skeleton')).toHaveCount(0);
    await expect(card(page).getByTestId('document-group').first()).toBeVisible();
    await openChecklist(page);
    await expect(progress(page)).toContainText(`of ${REQUIRED.length} required`);
  });
});
