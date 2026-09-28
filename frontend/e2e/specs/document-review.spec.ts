import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { loadFixture } from '../support/fixtures';
import type { DocumentRow, MockBackend } from '../support/mockBackend';

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

  test('DI-024 an officer can fill in a flagged field and the value is kept @DI-024', async ({ page, backend }) => {
    const { fixture, cert } = certWithMissingUen(backend);
    await page.goto(fixture.documentUrl(cert));

    await reviewRow(page, 'UEN').locator('input').fill('201634564N');
    await page.getByRole('button', { name: 'Save fields' }).click();

    // Saved and human-verified: the field is no longer flagged, so the whole
    // manual-review section closes (taking its "Changes saved" message with it).
    await expect(reviewSection(page)).toHaveCount(0);
    await expect(fieldRow(page, 'UEN').locator('.field-value')).toHaveText('201634564N');
    const saved = backend.requestsTo('PATCH', `/documents/${cert.id}/extracted-fields`);
    expect(saved).toHaveLength(1);
    expect((saved[0].body as { extracted_fields: Record<string, unknown> }).extracted_fields.uen).toBe('201634564N');

    await page.reload();
    await expect(fieldRow(page, 'UEN').locator('.field-value')).toHaveText('201634564N');
    await expect(fieldRow(page, 'UEN').locator('.confidence-badge')).toHaveText('1.00');
  });

  test('DI-025 once every field is complete the document is no longer flagged and can be approved @DI-025', async ({ page, backend }) => {
    const { fixture, cert } = certWithMissingUen(backend);
    await page.goto(fixture.documentUrl(cert));

    await reviewRow(page, 'UEN').locator('input').fill('201634564N');
    await page.getByRole('button', { name: 'Save fields' }).click();
    await expect(reviewSection(page)).toHaveCount(0);

    await expect(routingOutcome(page)).toHaveText('Accepted');
    await page.getByRole('button', { name: '✓ Approve Document' }).click();

    await expect(page.getByRole('button', { name: '✓ Approve Document' })).toHaveCount(0);
    expect(backend.requestsTo('POST', `/documents/${cert.id}/approve`)).toHaveLength(1);

    await page.goto(fixture.caseUrl);
    await expect(page.locator('.approved-documents-card')).toContainText(cert.filename);
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
    test.fail(
      true,
      'REG-001 (known defect, reported to SCRUM-20/SCRUM-26 owners): Approve Document stays enabled, and ' +
        'POST /documents/{id}/approve accepts, a document with a flagged empty required field.',
    );
    const { fixture, cert } = certWithMissingUen(backend);
    await page.goto(fixture.documentUrl(cert));

    const approve = page.getByRole('button', { name: '✓ Approve Document' });
    // Expected (sheet): "The system prevents the document from proceeding
    // to the next analysis stage" while a required field is missing.
    if (await approve.isEnabled()) {
      await approve.click();
    }
    await expect(page.locator('.document-status')).not.toContainText(/approved/i);
    expect(backend.requestsTo('POST', `/documents/${cert.id}/approve`)).toHaveLength(0);
  });

  test('DI-025 a field that is legitimately empty does not block saving a correction @DI-025', async ({ page, backend }) => {
    test.fail(
      true,
      'REG-002 (known defect, reported to SCRUM-20 owner): Save fields requires every field to be non-empty, ' +
        'including ones extraction was confident are absent (e.g. no former name).',
    );
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
});

export type { DocumentRow };
