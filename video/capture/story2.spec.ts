// The portal for the film's "how it works" sections 6-8, with the film's story: the January statement's forged
// closing balance and an address that differs between the business profile and the statement. Real frontend on its
// mock backend; the case's risk, review and audit events are written for the story in the backend's real shapes.
// Screens at 3x; element boxes saved for overlays (story2-boxes.json).
import { test } from '../../frontend/e2e/support/test';
import { loadFixture, readRegressionFixture } from '../../frontend/e2e/support/fixtures';
import { makeCase } from '../../frontend/e2e/support/mockBackend';
import { DETECTION_STEPS } from './traceSteps';
import { mkdirSync, writeFileSync } from 'node:fs';

const OUT = process.env.CAPTURE_OUT ?? '../public/ui/story';
mkdirSync(OUT, { recursive: true });
test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 3 });

const CASE_VIEWS = readRegressionFixture<Record<string, { risk: Record<string, any> }>>('case-views.json');
const AUDIT = readRegressionFixture<Record<string, any>[]>('audit-trail-sprint5.json');

const boxes = (page: import('@playwright/test').Page, selector: string) =>
  page.locator(selector).evaluateAll((els) => els.map((el) => {
    const r = el.getBoundingClientRect();
    return { text: (el.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 90), x: r.x, y: r.y, w: r.width, h: r.height };
  }));

function setup(backend: Parameters<typeof loadFixture>[0]) {
  const fx = loadFixture(backend, 'anchorpoint');
  for (const d of fx.documents) d.filename = d.filename.replace(/^MuhammadD_/, 'Director_').replace(/^SimS_/, 'Shareholder_');
  const jan = fx.document('Statement_128262');
  jan.filename = 'Anchorpoint_BankStatement_Jan2026.pdf';
  jan.status = 'detected';
  const biz = fx.document('BizFile_333996');
  biz.filename = 'Anchorpoint_BusinessProfile.pdf';
  const base = CASE_VIEWS['scored'].risk.contributing_findings[0];
  const f = (o: Record<string, unknown>) => ({ ...structuredClone(base), document_ids: [], cited_values: [], feedback_ids: [], ...o });
  const risk = {
    ...structuredClone(CASE_VIEWS['scored'].risk), level: 'High', score: 72, points: 31, documents_scanned: 20, documents_total: 20,
    unassessed_checks: [], dismissed_findings: [],
    reasons: ['Rated High from the combined severity of 4 finding(s).'],
    contributing_findings: [
      f({ document_id: jan.id, filename: jan.filename, document_type: 'bank_statement', finding_id: `${jan.id}:totals-1`, fingerprint: 'story-r1', tool: 'bank_statement_totals_tool', check_code: 'BS.TOTALS', severity: 'high', points: 12,
        summary: 'Closing balance 812,400.00 does not reconcile: the opening balance plus the month\'s transactions gives 182,400.00.', rationale: 'The balance is four times what the transactions support.' }),
      f({ document_id: jan.id, filename: jan.filename, document_type: 'bank_statement', finding_id: `${jan.id}:font-1`, fingerprint: 'story-r2', tool: 'font_consistency_tool', severity: 'medium', points: 4,
        summary: 'The closing balance is set in a different weight from the rest of the statement.', rationale: 'Only the figure that matters differs.' }),
      f({ document_id: jan.id, filename: jan.filename, document_type: 'bank_statement', finding_id: `${jan.id}:meta-1`, fingerprint: 'story-r3', tool: 'metadata_tool', severity: 'medium', points: 3,
        summary: 'The file was last saved by a PDF editor, not by the bank.', rationale: 'A bank statement should reach us exactly as the bank generated it.' }),
      f({ document_id: biz.id, filename: biz.filename, document_type: 'certificate_of_incorporation', finding_id: 'xd-address-1', fingerprint: 'story-r4', tool: 'cross_document_analysis', severity: 'high', points: 12,
        source: 'cross_document', category: 'identity_entity_conflict', document_ids: [biz.id, jan.id],
        summary: 'The registered address is "6 Serangoon North Avenue 5, #02-11" on the business profile (Anchorpoint_BusinessProfile.pdf), but "18 Cross Street, #07-01" on the bank statement (Anchorpoint_BankStatement_Jan2026.pdf).',
        rationale: 'A company has one registered address.' }),
    ],
  };
  const additional_documents = {
    current_level: 'High', current_score: 72, best_case_level: 'Low', best_case_score: 20, reason: 'recommendations',
    items: [
      { source: 'finding', title: 'Bank-certified January 2026 statement', affects_score: true, level_if_supports: 'Medium', score_if_supports: 40,
        addresses: [`${jan.id}:totals-1`, `${jan.id}:font-1`, `${jan.id}:meta-1`], recommendation: null,
        why: 'Confirms the closing balance printed on Anchorpoint_BankStatement_Jan2026.pdf, straight from the bank.' },
      { source: 'finding', title: 'Proof of registered address', affects_score: true, level_if_supports: 'Medium', score_if_supports: 46,
        addresses: ['xd-address-1'], recommendation: null,
        why: 'Settles which address is the company\'s: the business profile and the bank statement disagree.' },
      { source: 'checklist', title: 'Purchase Order', affects_score: false, level_if_supports: null, score_if_supports: null, addresses: [],
        why: 'Anchorpoint_BankStatement_Dec2025_scan.pdf shows 1 large supplier payment (of at least 20,000.00).',
        recommendation: { document_type: 'purchase_order', title: 'Purchase Order', request: 'Request a purchase order for this transaction:',
          reason: 'Anchorpoint_BankStatement_Dec2025_scan.pdf shows 1 large supplier payment (of at least 20,000.00).',
          source_document_id: jan.id, source_filename: 'Anchorpoint_BankStatement_Dec2025_scan.pdf',
          items: [{ amount: -42150.0, breakdown: [], counterparty: 'HARBOURSIDE OFFICE SUPPLIES PTE. LTD.', date: '23-Dec-25',
            description: 'SUPPLIER PAYMENT HARBOURSIDE OFFICE SUPPLIES SGD 42,150', prior_amount: null,
            reason: 'Payment of 42,150.00 on 23-Dec-25 to HARBOURSIDE OFFICE SUPPLIES PTE. LTD.' }] } },
    ],
  };
  // reviews_required: the case was created with officer reviews (this fixture predates the setting).
  Object.assign(fx.case, CASE_VIEWS['scored'], { risk, additional_documents, reviews_required: true });
  backend.servePreviews = true;
  backend.findingReviews[fx.case.id] = { verdicts: {}, signOffs: {} };
  return { fx, jan, biz };
}

test('risk summary and review', async ({ page, backend }) => {
  test.setTimeout(120_000);
  const { fx, jan } = setup(backend);
  await page.goto(`${fx.caseUrl}?section=risk`);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/risk.png` });
  await page.screenshot({ path: `${OUT}/risk-full.png`, fullPage: true });
  const risk = { documents: await boxes(page, '.risk-document'), card: await boxes(page, '.summary-card'), gauge: await boxes(page, '.severity-gauge-value') };

  await page.locator('.risk-document', { hasText: jan.filename }).getByRole('button').first().click();
  const dialog = page.getByRole('dialog', { name: `Review findings on ${jan.filename}` });
  await dialog.waitFor();
  // For the film: leave out the "couldn't pinpoint where" lines (the film shows the place on the page itself).
  await page.evaluate(() => {
    const hide = () => document.querySelectorAll('.risk-finding-unplaced').forEach((el) => {
      if ((el.textContent ?? '').includes("couldn't pinpoint")) (el as HTMLElement).style.display = 'none';
    });
    hide();
    new MutationObserver(hide).observe(document.body, { subtree: true, childList: true, characterData: true });
  });
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/review.png` });
  const review1 = {
    dialog: await boxes(page, '[role="dialog"]'),
    rows: await boxes(page, '[role="dialog"] .risk-findings li'),
    wrong: await boxes(page, '[role="dialog"] input[type="checkbox"]'),
    save: await boxes(page, '[role="dialog"] button:has-text("Save review")'),
    parts: await page.locator('[role="dialog"] .risk-findings li').first().evaluate((li) =>
      Array.from(li.querySelectorAll('*')).filter((el) => el.childElementCount === 0 && (el.textContent ?? '').trim()).map((el) => {
        const r = el.getBoundingClientRect();
        // the text's own extent, not its block's
        const range = document.createRange(); range.selectNodeContents(el); const t = range.getBoundingClientRect();
        return { cls: (el as HTMLElement).className, text: (el.textContent ?? '').trim().slice(0, 60), x: t.x, y: t.y, w: t.width, h: t.height, bx: r.x, bw: r.width };
      })),
  };
  // The officer dismisses the PDF-editor finding as a false alarm, with a note.
  await dialog.getByRole('checkbox', { name: /^Agents got this wrong: The file was last saved/ }).check();
  await dialog.getByRole('group', { name: 'Why is this a false alarm?' }).getByRole('button', { name: 'Other' }).click();
  await dialog.getByRole('textbox', { name: 'What was the reason?' }).fill('Bank exports via its own PDF tool.');
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/review-dismissed.png` });
  const review2 = {
    rows: await boxes(page, '[role="dialog"] .risk-findings li'),
    reasons: await boxes(page, '[role="dialog"] [role="group"]'),
    note: await dialog.getByRole('textbox', { name: 'What was the reason?' }).evaluate((el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return [{ x: r.x, y: r.y, w: r.width, h: r.height, pl: s.paddingLeft, font: s.font }]; }),
    save: await boxes(page, '[role="dialog"] button:has-text("Save review")'),
  };
  writeFileSync(`${OUT}/story2-risk.json`, JSON.stringify({ risk, review1, review2 }, null, 2));
});

test('trace tool call and audit trail', async ({ page, backend }) => {
  test.setTimeout(120_000);
  const { fx, jan } = setup(backend);
  backend.traces[jan.id] = { document_id: jan.id, started_at: 0, steps: DETECTION_STEPS };
  await page.goto(fx.documentUrl(jan));
  await page.waitForLoadState('networkidle');
  await page.getByRole('button', { name: 'Document actions' }).click();
  await page.getByRole('menuitem', { name: 'Agent trace' }).click();
  for (const label of ['Lead agent', 'Specialists', 'Layout specialist']) {
    await page.getByRole('button', { name: `Expand ${label}` }).first().click({ timeout: 2000 }).catch(() => {});
  }
  await page.locator('.trace-row-main', { hasText: 'Font consistency check' }).first().click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/trace-tool.png` });
  const trace = { rows: await boxes(page, '.trace-row-main'), details: await boxes(page, '.trace-details') };

  const ev = (at: string, type: string, o: Record<string, unknown> = {}) => ({
    event_id: `${type}-${at}`, type, occurred_at: at, actor_type: 'user', actor: 'officer', case_id: fx.case.id, document_id: null,
    outcome: 'success', started_at: null, duration_ms: null, detail: {}, ...o,
  });
  const analysed = structuredClone(AUDIT.find((e) => e.type === 'document.analyzed'))!;
  backend.auditEvents = [
    ev('2026-10-27T02:00:00Z', 'document.uploaded', { document_id: jan.id, detail: { content_type: 'application/pdf', filename: jan.filename, size_bytes: 3 } }),
    { ...analysed, case_id: fx.case.id, document_id: jan.id, occurred_at: '2026-10-27T02:00:40Z', event_id: 'story-analysed', detail: { ...analysed.detail, document_type: 'bank_statement', classification_confidence: 0.85 } },
    ev('2026-10-27T02:03:00Z', 'document.approved', { document_id: jan.id }),
    ev('2026-10-27T02:03:30Z', 'detection.check_run', { actor_type: 'agent', actor: 'detection-worker', document_id: jan.id, detail: { check: 'font-consistency', finding_count: 1, unable_to_assess: false, requested_by: 'officer' } }),
    ev('2026-10-27T02:03:40Z', 'detection.check_run', { actor_type: 'agent', actor: 'detection-worker', document_id: jan.id, detail: { check: 'metadata', finding_count: 1, unable_to_assess: false, requested_by: 'officer' } }),
    ev('2026-10-27T02:09:00Z', 'finding_review.saved', { document_id: jan.id, detail: { scope: 'document', real: 2, false_alarms: 1, cleared: 0 } }),
    ev('2026-10-27T02:10:00Z', 'finding_review.saved', { detail: { scope: 'case', real: 1, false_alarms: 0, cleared: 0 } }),
    ev('2026-10-27T02:11:00Z', 'note.added', { detail: { scope: 'case' } }),
  ];
  await page.goto(`${fx.caseUrl}?section=audit`);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/audit.png` });
  const audit = { entries: await boxes(page, '[data-testid="audit-entry"]') };
  writeFileSync(`${OUT}/story2-trace.json`, JSON.stringify({ trace, audit }, null, 2));
});

test('additional documents', async ({ page, backend }) => {
  test.setTimeout(120_000);
  const { fx } = setup(backend);
  await page.goto(`${fx.caseUrl}?section=additional`);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/additional.png` });
  await page.screenshot({ path: `${OUT}/additional-full.png`, fullPage: true });
  const items = await page.locator('main li, [class*="additional"] li, [class*="recommend"]').evaluateAll((els) => els.map((el) => {
    const r = el.getBoundingClientRect();
    return { cls: (el as HTMLElement).className, text: (el.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 90), x: r.x, y: r.y, w: r.width, h: r.height };
  }));
  writeFileSync(`${OUT}/story2-additional.json`, JSON.stringify({ items }, null, 2));
});

test('dashboard', async ({ page, backend }) => {
  test.setTimeout(120_000);
  await page.clock.setFixedTime(new Date('2026-11-05T04:00:00Z'));
  const { fx } = setup(backend);
  fx.case.status = 'pending_review';
  loadFixture(backend, 'harbor-light');
  backend.cases.push(
    makeCase({ id: 'c3', greenlink_case_id: 'GLDB-21', company_name: 'Meridian Freight Pte Ltd', status: 'pending_review', ...CASE_VIEWS['recommendations'] } as never),
    makeCase({ id: 'c4', greenlink_case_id: 'GLDB-22', company_name: 'Cedar Holdings', status: 'in_progress', ...CASE_VIEWS['nothing needed'] } as never),
    makeCase({ id: 'c5', greenlink_case_id: 'GLDB-23', company_name: 'Bolt Logistics', status: 'pending_review' }),
    makeCase({ id: 'c6', greenlink_case_id: 'GLDB-24', company_name: 'Orchard Lane Trading', status: 'in_progress', ...CASE_VIEWS['no reducible findings'] } as never),
  );
  await page.goto('/dashboard');
  await page.getByRole('button', { name: 'Date range' }).click();
  await page.getByRole('button', { name: 'All time' }).click();
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${OUT}/dashboard-full.png`, fullPage: true });
  const cards = await page.locator('h2, h3, [class*="card"] > header, a, li').evaluateAll((els) => els.map((el) => {
    const r = el.getBoundingClientRect();
    return { tag: el.tagName, cls: (el as HTMLElement).className, text: (el.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 70), x: r.x, y: r.y + window.scrollY, w: r.width, h: r.height };
  }).filter((b) => b.w > 0 && b.text));
  writeFileSync(`${OUT}/story2-dashboard.json`, JSON.stringify({ cards }, null, 2));
});
