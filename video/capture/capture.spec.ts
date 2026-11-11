// Screens of the real portal for the film. See README.md.
import { test, expect } from '../../frontend/e2e/support/test';
import { loadFixture } from '../../frontend/e2e/support/fixtures';
import { browseFiles, pdf, png, jpg } from '../../frontend/e2e/support/files';
import { makeCase } from '../../frontend/e2e/support/mockBackend';
import { writeFileSync, mkdirSync } from 'node:fs';

const OUT = process.env.CAPTURE_OUT ?? '../public/ui';
mkdirSync(OUT, { recursive: true });

test('case documents (GLDB-02, 20 documents)', async ({ page, backend }) => {
  const fx = loadFixture(backend, 'anchorpoint');
  await page.goto(`${fx.caseUrl}?section=documents`);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/case-documents.png` });
  await page.screenshot({ path: `${OUT}/case-documents-full.png`, fullPage: true });
  await page.goto(fx.caseUrl);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/case-overview.png` });
  await page.screenshot({ path: `${OUT}/case-overview-full.png`, fullPage: true });
  writeFileSync(`${OUT}/case.json`, JSON.stringify({
    case: fx.case.greenlink_case_id, company: fx.case.company_name,
    documents: fx.documents.map((d) => ({ filename: d.filename, type: d.document_type })),
  }, null, 2));
});

test('upload box with the whole package staged', async ({ page, backend }) => {
  const fx = loadFixture(backend, 'anchorpoint');
  const empty = makeCase({ id: 'case-capture', greenlink_case_id: fx.case.greenlink_case_id + 'X', company_name: fx.case.company_name });
  backend.cases.push(empty);
  await page.goto(`/cases/${empty.greenlink_case_id}?section=documents`);
  await expect(page.getByRole('heading', { name: 'Upload supporting documents' })).toBeVisible();
  await page.screenshot({ path: `${OUT}/upload-empty.png` });
  const files = fx.documents.map((d) =>
    d.filename.endsWith('.png') ? png(d.filename) : d.filename.endsWith('.jpg') ? jpg(d.filename) : pdf(d.filename));
  await browseFiles(page, files);
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/upload-staged.png` });
  await page.screenshot({ path: `${OUT}/upload-staged-full.png`, fullPage: true });
  // Measurements of one staged file row and the upload box, for rebuilding them in the film.
  const measure = await page.evaluate(() => {
    const pick = (el: Element | null) => {
      if (!el) return null;
      const s = getComputedStyle(el); const r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width, h: r.height, color: s.color, bg: s.backgroundColor, border: s.border,
        radius: s.borderRadius, font: s.font, padding: s.padding, gap: s.gap, shadow: s.boxShadow, html: el.outerHTML.slice(0, 1500) };
    };
    return {
      box: pick(document.querySelector('.upload-box')),
      row: pick(document.querySelector('.file-row')),
      name: pick(document.querySelector('.file-row .f-name')),
      button: pick(document.querySelector('.upload-btn')),
      body: { bg: getComputedStyle(document.body).backgroundColor, font: getComputedStyle(document.body).font },
    };
  });
  writeFileSync(`${OUT}/upload-measure.json`, JSON.stringify(measure, null, 2));
});
