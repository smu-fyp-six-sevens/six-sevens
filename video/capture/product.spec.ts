// Screens of the real portal for the product sections of the film, on the frontend's mock backend.
// Run like capture.spec.ts (see README.md), with CAPTURE_OUT set.
import { test } from '../../frontend/e2e/support/test';
import { loadFixture, readRegressionFixture } from '../../frontend/e2e/support/fixtures';
import { makeCase } from '../../frontend/e2e/support/mockBackend';
import { DETECTION_STEPS } from './traceSteps';
import { mkdirSync } from 'node:fs';

const OUT = process.env.CAPTURE_OUT ?? '../public/ui/product';
mkdirSync(OUT, { recursive: true });
const CASE_VIEWS = readRegressionFixture<Record<string, Record<string, unknown>>>('case-views.json');
const C028 = readRegressionFixture<Record<string, unknown>>('detection-report-c028.json');

test.use({ timezoneId: 'Asia/Singapore' });

const shot = async (page: import('@playwright/test').Page, name: string) => {
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${OUT}/${name}.png` });
  await page.screenshot({ path: `${OUT}/${name}-full.png`, fullPage: true });
};

test('agents', async ({ page }) => {
  await page.goto('/agents');
  await shot(page, 'agents');
});

test('dashboard', async ({ page, backend }) => {
  await page.clock.setFixedTime(new Date('2026-11-05T04:00:00Z'));
  const fx = loadFixture(backend, 'anchorpoint');
  Object.assign(fx.case, CASE_VIEWS['scored']);
  loadFixture(backend, 'harbor-light');
  backend.cases.push(
    makeCase({ id: 'c3', greenlink_case_id: 'GLDB-21', company_name: 'Meridian Freight Pte Ltd', status: 'pending_review', ...CASE_VIEWS['recommendations'] }),
    makeCase({ id: 'c4', greenlink_case_id: 'GLDB-22', company_name: 'Cedar Holdings', status: 'in_progress', ...CASE_VIEWS['nothing needed'] }),
    makeCase({ id: 'c5', greenlink_case_id: 'GLDB-23', company_name: 'Bolt Logistics', status: 'pending_review' }),
  );
  await page.goto('/dashboard');
  await page.getByRole('button', { name: 'Date range' }).click();
  await page.getByRole('button', { name: 'All time' }).click();
  await shot(page, 'dashboard');
  await page.goto('/cases');
  await shot(page, 'case-queue');
});

test('case risk and summary', async ({ page, backend }) => {
  const fx = loadFixture(backend, 'anchorpoint');
  Object.assign(fx.case, CASE_VIEWS['scored']);
  for (const section of ['summary', 'risk', 'analysis']) {
    await page.goto(`${fx.caseUrl}?section=${section}`);
    await shot(page, `case-${section}`);
  }
});

test('detection report and agent trace', async ({ page, backend }) => {
  const fx = loadFixture(backend, 'anchorpoint');
  const doc = fx.document('Credit_Bureau_Report');
  doc.status = 'detected';
  backend.detectionReports[doc.id] = { ...C028, document_id: doc.id };
  backend.traces[doc.id] = { document_id: doc.id, started_at: 0, steps: DETECTION_STEPS };
  await page.goto(fx.documentUrl(doc));
  await shot(page, 'document-detection');
  await page.getByRole('button', { name: 'Document actions' }).click();
  await page.getByRole('menuitem', { name: 'Agent trace' }).click();
  for (const label of ['Lead agent', 'Specialists']) {
    await page.getByRole('button', { name: `Expand ${label}` }).first().click().catch(() => {});
  }
  await shot(page, 'agent-trace');
});
