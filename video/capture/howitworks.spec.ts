// The portal's documents table and extracted-fields panel, for the "how it works" sections. Real frontend on its
// mock backend; the scanned file is the film's own (an invented December bank statement), file names invented.
// Saves screenshots at 3x and the computed styles the film rebuilds these parts from.
import { test } from '../../frontend/e2e/support/test';
import { loadFixture } from '../../frontend/e2e/support/fixtures';
import { mkdirSync, writeFileSync } from 'node:fs';

const OUT = process.env.CAPTURE_OUT ?? '../public/ui/how';
mkdirSync(OUT, { recursive: true });
test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 3 });

const field = (value: unknown, confidence = 0.97) => ({ value, confidence, id: crypto.randomUUID() });
export const SCAN_FIELDS = {
  bank_name: field('Harbourline Bank'),
  company_name: field('Anchorpoint Insurance Brokers Pte. Ltd.'),
  account_number: field('0123-456789-001'),
  account_type: field('Corporate current account'),
  account_currency: field('SGD'),
  statement_period: field('1 Dec 2025 - 31 Dec 2025'),
  ledger_balance: field(96250.0, 0.93),
  available_balance: field(96250.0, 0.93),
  total_deposits_sgd: field(131400.0, 0.91),
};

const styles = (page: import('@playwright/test').Page, selector: string) =>
  page.locator(selector).first().evaluate((root) => {
    const pick = (el: Element) => {
      const s = getComputedStyle(el); const r = el.getBoundingClientRect();
      return { tag: el.tagName.toLowerCase(), cls: (el as HTMLElement).className, text: (el.childElementCount ? '' : (el.textContent ?? '').trim()).slice(0, 60),
        x: r.x, y: r.y + window.scrollY, w: r.width, h: r.height, font: s.font, color: s.color, bg: s.backgroundColor, border: s.border,
        radius: s.borderRadius, padding: s.padding, letterSpacing: s.letterSpacing, textTransform: s.textTransform };
    };
    return [root, ...Array.from(root.querySelectorAll('*'))].slice(0, 40).map(pick);
  });

test('documents table and scanned statement', async ({ page, backend }) => {
  test.setTimeout(120_000);
  const fx = loadFixture(backend, 'anchorpoint');
  for (const d of fx.documents) d.filename = d.filename.replace(/^MuhammadD_/, 'Director_').replace(/^SimS_/, 'Shareholder_');
  fx.document('Statement_128262').filename = 'Anchorpoint_BankStatement_Jan2026.pdf';
  const scan = fx.document('Wealth_Statement_scan');
  scan.filename = 'Anchorpoint_BankStatement_Dec2025_scan.pdf';
  scan.document_type = 'bank_statement';
  scan.extracted_fields = SCAN_FIELDS as never;

  await page.goto(`${fx.caseUrl}?section=documents`);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/documents.png` });
  const table = {
    header: await styles(page, '.documents-table-header'),
    row: await styles(page, '.documents-table-row'),
  };

  await page.goto(fx.documentUrl(scan));
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/scan-details-full.png`, fullPage: true });
  const fields = {
    table: await styles(page, '.extracted-table'),
    rows: await page.locator('.extracted-table tbody tr, .extracted-table > div').evaluateAll((els) =>
      els.map((el) => { const r = el.getBoundingClientRect(); return { text: (el.textContent ?? '').trim().slice(0, 80), x: r.x, y: r.y + window.scrollY, w: r.width, h: r.height }; })),
  };
  writeFileSync(`${OUT}/styles.json`, JSON.stringify({ table, fields }, null, 2));
});
