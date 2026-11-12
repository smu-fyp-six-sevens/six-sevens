// The portal, shown with the film's story: case GLDB-02's January bank statement, whose closing balance a bad
// actor changed from 182,400.00 to 812,400.00 (section 3). The frontend and its mock backend are real; only the
// detection report is written for the story, in the shape of the backend's real one (detection-report-c028.json).
// Screens are captured at 3x so they stay sharp in a 4K render; element boxes are saved for the film's overlays.
import { test } from '../../frontend/e2e/support/test';
import { loadFixture, readRegressionFixture } from '../../frontend/e2e/support/fixtures';
import { DETECTION_STEPS } from './traceSteps';
import { mkdirSync, writeFileSync } from 'node:fs';

const OUT = process.env.CAPTURE_OUT ?? '../public/ui/story';
mkdirSync(OUT, { recursive: true });
const CASE_VIEWS = readRegressionFixture<Record<string, Record<string, unknown>>>('case-views.json');
const C028 = readRegressionFixture<Record<string, any>>('detection-report-c028.json');

const STATEMENT_FILE = 'Anchorpoint_BankStatement_Jan2026.pdf';

function storyReport(documentId: string) {
  const r = structuredClone(C028);
  const [font, editor] = [r.findings[0], r.findings[2]];
  r.document_id = documentId;
  r.document_type = 'bank_statement';
  r.summary =
    "The closing balance does not match the statement's own transactions, and that one figure is set in a different weight from the rest of the page. Together these suggest the balance was edited after the statement was issued.";
  r.second_look = { ...r.second_look, reason: 'A restyled figure alone could be a template quirk.' };
  r.consulted[0].brief = 'Bank statement with printed figures: check fonts and layout.';
  r.consulted[1].brief = "Check the balances and the statement's internal consistency.";
  r.consulted[1].tools_called = ['bank_statement_totals_tool'];
  const totals = {
    ...structuredClone(font),
    id: 'bank_statement_totals_tool-1', tool: 'bank_statement_totals_tool', check_code: 'BS.TOTALS', severity: 'high',
    deviation_type: undefined, detected: undefined, expected: undefined, specialists: ['content_validation'],
    agent_confidences: [{ confidence: 0, specialist: 'content_validation' }], fingerprint: 'story-totals-1',
    summary: 'Closing balance 812,400.00 does not reconcile: the opening balance plus the month\'s transactions gives 182,400.00.',
    rationale: 'Rated high.', locations: [{ bbox: [400, 520, 470, 532], page: 1, text: '812,400.00' }],
  };
  const restyled = {
    ...font, fingerprint: 'story-font-1',
    detected: { ...font.detected, font: 'Helvetica', bold: false }, expected: { ...font.expected, font: 'Helvetica-Bold', bold: true },
    summary: "Font deviation on the closing balance: font 'Helvetica' vs. expected 'Helvetica-Bold' (1 occurrence).",
    locations: [{ bbox: [400, 520, 470, 532], page: 1, text: '812,400.00' }],
  };
  const saved = {
    ...editor, fingerprint: 'story-editor-1', value: 'PDF Editor Pro 11',
    reason: 'a PDF editor can change existing content, and a bank statement should reach us exactly as the bank generated it',
    summary: 'producer: a PDF editor can change existing content, and a bank statement should reach us exactly as the bank generated it',
  };
  r.findings = [totals, restyled, saved];
  return r;
}

test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 3 });

const boxes = (page: import('@playwright/test').Page, selector: string) =>
  page.locator(selector).evaluateAll((els) =>
    els.map((el) => {
      const r = el.getBoundingClientRect();
      return { text: (el.textContent ?? '').trim().slice(0, 80), x: r.x + window.scrollX, y: r.y + window.scrollY, w: r.width, h: r.height };
    }));

test('the statement, caught', async ({ page, backend }) => {
  test.setTimeout(120_000);
  const fx = loadFixture(backend, 'anchorpoint');
  Object.assign(fx.case, CASE_VIEWS['scored']);
  // Invented names only on screen.
  for (const d of fx.documents) d.filename = d.filename.replace(/^MuhammadD_/, 'Director_').replace(/^SimS_/, 'Shareholder_');
  const doc = fx.document('Statement_128262');
  doc.filename = STATEMENT_FILE;
  doc.status = 'detected';
  backend.detectionReports[doc.id] = storyReport(doc.id);
  backend.traces[doc.id] = { document_id: doc.id, started_at: 0, steps: DETECTION_STEPS };

  await page.goto(fx.documentUrl(doc));
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/detection-full.png`, fullPage: true });
  const detection = {
    panel: await boxes(page, '.detection-verdict'),
    label: await boxes(page, '.detection-verdict-label'),
    summary: await boxes(page, '.detection-verdict-summary'),
    findings: await boxes(page, '.detection-findings > li'),
    page: await page.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight })),
  };

  await page.getByRole('button', { name: 'Document actions' }).click();
  await page.getByRole('menuitem', { name: 'Agent trace' }).click();
  for (const label of ['Lead agent', 'Specialists', 'Layout specialist']) {
    await page.getByRole('button', { name: `Expand ${label}` }).first().click({ timeout: 2000 }).catch(() => {});
  }
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/trace.png` });
  const trace = { rows: await boxes(page, '.trace-row-main') };

  await page.keyboard.press('Escape');
  await page.goto(`${fx.caseUrl}?section=summary`);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/case-summary.png` });

  writeFileSync(`${OUT}/boxes.json`, JSON.stringify({ viewport: { w: 1440, h: 900 }, dpr: 3, detection, trace }, null, 2));
});
