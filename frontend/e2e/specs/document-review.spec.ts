import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { loadFixture } from '../support/fixtures';
import type { MockBackend } from '../support/mockBackend';

/**
 * Document review (Sprint 2) — "Test Cases - Sprint 2.xlsx", Document
 * Ingestion sheet: how extracted data is displayed (Extract Text from
 * Documents, SCRUM-9: DI-020, DI-021, DI-030) and manual correction
 * (Validate Extracted Data and Support Manual Correction, SCRUM-20:
 * DI-023 – DI-026).
 *
 * These use real extraction results captured from Supabase (see
 * support/fixtures.ts), so they test how the UI handles real data — not
 * whether the AI extracts the right values, which is deferred until the
 * LangGraph pipeline is in place (DI-018, DI-019, DI-022, DI-027 – DI-029).
 */

const routingOutcome = (page: Page) =>
  page.locator('.details-label', { hasText: 'Routing Outcome' }).locator('xpath=following-sibling::strong');
const fieldRow = (page: Page, label: string) =>
  page.locator('.extracted-table-row').filter({ has: page.locator('.field-name', { hasText: new RegExp(`^${label}$`) }) });
const reviewSection = (page: Page) => page.locator('.details-card--review');
const reviewRow = (page: Page, label: string) =>
  reviewSection(page).locator('.manual-review-row').filter({
    has: page.locator('.manual-review-field', { hasText: new RegExp(`^${label}$`) }),
  });

/**
 * A real Certificate of Incorporation with its UEN unreadable: blank, at low
 * confidence — what extraction returns when a required field is missing or
 * illegible (DI-023's "e.g. Company UEN"). Anchorpoint's certificate has no
 * other empty fields, so the UEN is the only thing needing attention.
 */
function certWithMissingUen(backend: MockBackend, fixtureName: 'anchorpoint' | 'harbor-light' = 'anchorpoint') {
  const fixture = loadFixture(backend, fixtureName);
  const cert = fixture.document(fixtureName === 'anchorpoint' ? 'BizFile_333996' : 'C014_CertIncorp');
  const fields = cert.extracted_fields as Record<string, { value: unknown; confidence: number | null }>;
  fields.uen = { ...fields.uen, value: null, confidence: 0.35 };
  // Captured before SCRUM-26 made these two fields required; a fresh
  // extraction returns them, so supply them as it would.
  fields.incorporating_act ??= { value: 'Companies Act 1967', confidence: 0.98 };
  fields.certificate_date ??= { value: '26 Mar 2016', confidence: 0.98 };
  return { fixture, cert };
}

test.describe('Extract text from documents @sprint2 @SCRUM-9', () => {
  test('DI-020 every extracted field shows its confidence next to it @DI-020', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');

    await page.goto(fixture.documentUrl(payslip));

    const fields = Object.entries(payslip.extracted_fields as Record<string, { confidence: number }>);
    expect(fields.length).toBeGreaterThan(0);
    await expect(page.locator('.extracted-table-row')).toHaveCount(fields.length);
    for (const row of await page.locator('.extracted-table-row').all()) {
      await expect(row.locator('.confidence-badge')).toHaveText(/^\d\.\d\d$/);
    }
  });

  test('DI-021 a field with no value is shown as "—" without failing the document @DI-021', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'harbor-light');
    const cert = fixture.document('C014_CertIncorp');

    await page.goto(fixture.documentUrl(cert));

    await expect(fieldRow(page, 'Company Former Name').locator('.field-value')).toHaveText('—');
    await expect(page.locator('.document-status')).toContainText('Extracted');
    await expect(page.locator('.review-message--error')).toHaveCount(0);
  });

  test('DI-030 array fields list every extracted value @DI-030', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'harbor-light');
    const loans = fixture.document('ListLoans');
    const terms = (loans.extracted_fields as Record<string, { value: { term_name: string; value: string }[] }>)
      .loan_terms.value;

    await page.goto(fixture.documentUrl(loans));

    const trigger = fieldRow(page, 'Loan Terms').locator('.array-value-trigger');
    await expect(trigger).toContainText(`${terms.length} items`);
    await trigger.click();

    const table = page.locator('table').last();
    await expect(table.locator('tbody tr')).toHaveCount(terms.length);
    for (const term of terms) {
      await expect(table).toContainText(term.term_name);
      await expect(table).toContainText(term.value);
    }
  });
});

test.describe('Validate extracted data and support manual correction @sprint2 @SCRUM-20', () => {
  test('DI-023 a document with a missing required field is flagged, and the field identified @DI-023', async ({ page, backend }) => {
    const { fixture, cert } = certWithMissingUen(backend);

    await page.goto(fixture.documentUrl(cert));

    await expect(routingOutcome(page)).toHaveText('Manual Review');
    await expect(reviewSection(page)).toBeVisible();
    const uen = reviewRow(page, 'UEN');
    await expect(uen).toHaveClass(/manual-review-row--needs-attention/);
    await expect(uen.locator('input')).toHaveValue('');
    await expect(uen.locator('input')).toBeEnabled();
    // Fields extraction was confident about stay read-only.
    await expect(reviewRow(page, 'Company Name').locator('input')).toBeDisabled();
  });

  test('DI-024 / DI-025 a filled-in field is saved and kept, and the document goes Manual Review -> Accepted -> Approved @DI-024 @DI-025', async ({ page, backend }) => {
    // Routed to manual review by the classifier as well — saving the fields must clear that too, not only the
    // low field confidence.
    const { fixture, cert } = certWithMissingUen(backend);
    cert.routing_status = 'manual_review';
    await page.goto(fixture.documentUrl(cert));
    await expect(routingOutcome(page)).toHaveText('Manual Review');

    await reviewRow(page, 'UEN').locator('input').fill('201634564N');
    await page.getByRole('button', { name: 'Save fields' }).click();

    // Saved and human-verified: the field is no longer flagged, so the whole manual-review section closes.
    await expect(reviewSection(page)).toHaveCount(0);
    await expect(routingOutcome(page)).toHaveText('Accepted');
    await expect(fieldRow(page, 'UEN').locator('.field-value')).toHaveText('201634564N');
    const saved = backend.requestsTo('PATCH', `/documents/${cert.id}/extracted-fields`);
    expect(saved).toHaveLength(1);
    expect((saved[0].body as { extracted_fields: Record<string, unknown> }).extracted_fields.uen).toBe('201634564N');
    // The values are verified, but a missing field can still be added (or a wrong one deleted) until approval.
    await expect(page.getByRole('button', { name: '+ Add field' })).toBeVisible();
    await expect(page.locator('.delete-field-button').first()).toBeVisible();

    await page.reload();
    await expect(fieldRow(page, 'UEN').locator('.field-value')).toHaveText('201634564N');
    await expect(fieldRow(page, 'UEN').locator('.confidence-badge')).toHaveText('1.00');
    await expect(routingOutcome(page)).toHaveText('Accepted');
    await expect(reviewSection(page)).toHaveCount(0);

    await page.getByRole('button', { name: '✓ Approve Document' }).click();
    await expect(routingOutcome(page)).toHaveText('Approved');
    expect(backend.requestsTo('POST', `/documents/${cert.id}/approve`)).toHaveLength(1);
    await page.goto(`${fixture.caseUrl}?section=approved`);
    await expect(page.locator('.approved-documents-card')).toContainText(cert.filename);
  });

  test('an unrecognised document starts as Unrecognised and only becomes Other once the officer picks it @DI-others', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const doc = fixture.document('Payslip');
    doc.document_type = 'unrecognised';
    doc.classification_confidence = 0;
    doc.routing_status = 'manual_review';
    await page.goto(fixture.documentUrl(doc));

    const typeSelect = page.locator('.details-type-select');
    await expect(typeSelect).toHaveValue('');
    await expect(typeSelect.locator('option:checked')).toHaveText('Unrecognised — select a type…');
    await expect(page.getByRole('button', { name: 'Confirm document type' })).toBeDisabled();

    await typeSelect.selectOption({ label: 'Other' });
    await page.getByRole('button', { name: 'Confirm document type' }).click();

    const saved = backend.requestsTo('PATCH', `/documents/${doc.id}/document-type`);
    expect(saved).toHaveLength(1);
    expect((saved[0].body as { document_type: string }).document_type).toBe('unrecognised');
    await expect(page.locator('.details-label', { hasText: 'Document Type' }).locator('xpath=following-sibling::strong')).toHaveText('Other');
  });

  test('DI-026 fields cannot be saved while a required field is still empty @DI-026', async ({ page, backend }) => {
    const { fixture, cert } = certWithMissingUen(backend);
    await page.goto(fixture.documentUrl(cert));

    await page.getByRole('button', { name: 'Save fields' }).click();

    await expect(page.locator('.save-error')).toHaveText('Please complete all fields before saving.');
    expect(backend.requestsTo('PATCH', `/documents/${cert.id}/extracted-fields`)).toHaveLength(0);
    await expect(routingOutcome(page)).toHaveText('Manual Review');
  });

  test('DI-026 a document with a missing required field cannot be approved @DI-026', async ({ page, backend }) => {
    // REG-001 — fixed on main by SCRUM-26 (block approval for missing
    // required fields); this was an expected-failure test until then.
    const { fixture, cert } = certWithMissingUen(backend);
    await page.goto(fixture.documentUrl(cert));

    await expect(page.getByRole('button', { name: '✓ Approve Document' })).toBeDisabled();
    const requiredWarning = page.locator('.approval-warning', { hasText: 'Please complete the following required field' });
    await expect(requiredWarning).toContainText('Cannot approve yet');
    await expect(requiredWarning.locator('strong')).toHaveText('UEN');
    expect(backend.requestsTo('POST', `/documents/${cert.id}/approve`)).toHaveLength(0);
  });

  test('DI-025 a clean credit report (no defaults on record) needs no review and is approved @DI-025', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const report = fixture.document('Credit_Bureau_Report');
    await page.goto(fixture.documentUrl(report));

    // Every field is complete and confident: nothing is flagged, so it goes straight to approval.
    await expect(routingOutcome(page)).toHaveText('Accepted');
    await expect(reviewSection(page)).toHaveCount(0);
    await expect(page.locator('.approval-warning')).toHaveCount(0);
    await page.getByRole('button', { name: '✓ Approve Document' }).click();

    await expect(routingOutcome(page)).toHaveText('Approved');
    expect(backend.requestsTo('POST', `/documents/${report.id}/approve`)).toHaveLength(1);
    expect(report.status).toBe('approved');
  });

  test('DI-025 a field that is legitimately empty does not block saving a correction @DI-025', async ({ page, backend }) => {
    // Harbor Light's real certificate has no former name, so extraction
    // correctly left Company Former Name empty (confidence 1.0). Fixing the
    // flagged UEN should be enough to save.
    const { fixture, cert } = certWithMissingUen(backend, 'harbor-light');
    await page.goto(fixture.documentUrl(cert));

    await reviewRow(page, 'UEN').locator('input').fill('201634564N');
    await page.getByRole('button', { name: 'Save fields' }).click();

    await expect(page.locator('.save-error')).toHaveCount(0);
    expect(backend.requestsTo('PATCH', `/documents/${cert.id}/extracted-fields`)).toHaveLength(1);
  });

  test('DI-others-07 an officer can add a custom field to a document @DI-others', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    await page.goto(fixture.documentUrl(payslip));

    await page.getByRole('button', { name: '+ Add field' }).click();
    await page.locator('#new-field-name').fill('employer_address');
    await page.locator('#new-field-value').fill('1 Raffles Place');
    await page.getByRole('button', { name: 'Add field', exact: true }).click();

    await expect(fieldRow(page, 'Employer Address').locator('.field-value')).toHaveText('1 Raffles Place');
    expect(backend.requestsTo('POST', `/documents/${payslip.id}/extracted-fields`)).toHaveLength(1);
  });

  test('DI-others-08 an officer can delete a custom field from a document @DI-others', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    await page.goto(fixture.documentUrl(payslip));
    await page.getByRole('button', { name: '+ Add field' }).click();
    await page.locator('#new-field-name').fill('employer_address');
    await page.locator('#new-field-value').fill('1 Raffles Place');
    await page.getByRole('button', { name: 'Add field', exact: true }).click();
    await expect(fieldRow(page, 'Employer Address')).toBeVisible();

    await fieldRow(page, 'Employer Address').locator('.delete-field-button').click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Delete' }).click();

    await expect(fieldRow(page, 'Employer Address')).toHaveCount(0);
    expect(backend.requestsTo('DELETE', `/documents/${payslip.id}/extracted-fields/field-employer_address`)).toHaveLength(1);
  });

  test('deleting a field asks first in a dialog, and Cancel keeps the field @SCRUM-299', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    await page.goto(fixture.documentUrl(payslip));
    await page.getByRole('button', { name: '+ Add field' }).click();
    await page.locator('#new-field-name').fill('employer_address');
    await page.locator('#new-field-value').fill('1 Raffles Place');
    await page.getByRole('button', { name: 'Add field', exact: true }).click();
    await expect(fieldRow(page, 'Employer Address')).toBeVisible();

    await fieldRow(page, 'Employer Address').locator('.delete-field-button').click();

    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toContainText('Employer Address will be removed from this document and cannot be recovered.');
    await dialog.getByRole('button', { name: 'Cancel' }).click();

    await expect(dialog).toHaveCount(0);
    await expect(fieldRow(page, 'Employer Address')).toBeVisible();
    expect(backend.requestsTo('DELETE', /extracted-fields/)).toHaveLength(0);
  });

  test('DI-others-11 a deleted field is not recreated when the other flagged fields are saved @DI-others', async ({ page, backend }) => {
    // A field the document doesn't print, extracted as empty and flagged
    // (a statement's total_credits when only per-month totals are printed).
    const { fixture, cert } = certWithMissingUen(backend);
    const fields = cert.extracted_fields as Record<string, { id?: string; value: unknown; confidence: number | null }>;
    fields.former_names = { id: 'field-former_names', value: null, confidence: 0.4 };
    await page.goto(fixture.documentUrl(cert));

    await fieldRow(page, 'Former Names').locator('.delete-field-button').click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Delete' }).click();
    await expect(fieldRow(page, 'Former Names')).toHaveCount(0);

    await reviewRow(page, 'UEN').locator('input').fill('201634564N');
    await page.getByRole('button', { name: 'Save fields' }).click();

    const saved = backend.requestsTo('PATCH', `/documents/${cert.id}/extracted-fields`);
    expect(saved).toHaveLength(1);
    expect((saved[0].body as { extracted_fields: Record<string, unknown> }).extracted_fields).not.toHaveProperty('former_names');
    await expect(fieldRow(page, 'Former Names')).toHaveCount(0);
  });

  test('DI-others-09 an array field flagged for review can be edited in the review table @DI-others', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'harbor-light');
    const loans = fixture.document('ListLoans');
    (loans.extracted_fields as Record<string, { confidence: number }>).loan_terms.confidence = 0.5;
    await page.goto(fixture.documentUrl(loans));

    const editButton = reviewRow(page, 'Loan Terms').getByRole('button', { name: /^Edit \d+ items?$/ });
    await editButton.click();
    await page.locator('.array-modal-input').first().fill('Edited term');
    await page.locator('.array-modal-save').click();

    await editButton.click();
    await expect(page.locator('.array-modal-input').first()).toHaveValue('Edited term');
    await page.locator('.array-modal-save').click();

    // Saving the review sends the edited list, and the saved value is what the page shows afterwards.
    await page.getByRole('button', { name: 'Save fields' }).click();
    const [saved] = backend.requestsTo('PATCH', `/documents/${loans.id}/extracted-fields`);
    const terms = (saved.body as { extracted_fields: { loan_terms: Record<string, unknown>[] } }).extracted_fields.loan_terms;
    expect(Object.values(terms[0])).toContain('Edited term');
    await page.reload();
    await expect(fieldRow(page, 'Loan Terms').locator('.array-value-trigger')).toContainText(`${terms.length} items`);
  });

  test('DI-others-10 an officer can confirm a low-confidence type as a different type @DI-others', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const payslip = fixture.document('Payslip');
    payslip.document_type = 'payslip';
    payslip.classification_confidence = 0.5;
    payslip.routing_status = 'manual_review';
    payslip.status = 'analyzed';
    await page.goto(fixture.documentUrl(payslip));

    await page.locator('.details-type-select').selectOption('bank_statement');
    await page.getByRole('button', { name: 'Confirm document type' }).click();

    const saved = backend.requestsTo('PATCH', `/documents/${payslip.id}/document-type`);
    expect(saved).toHaveLength(1);
    expect((saved[0].body as { document_type: string }).document_type).toBe('bank_statement');
  });
});

