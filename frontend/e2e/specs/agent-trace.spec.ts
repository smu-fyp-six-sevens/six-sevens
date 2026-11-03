import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { loadFixture } from '../support/fixtures';

/**
 * Trace Agent Runs (SCRUM-59): the document page shows each run as a tree of agents, Claude calls and tool calls,
 * with each one's timing, tokens, reasoning and any reply that failed validation.
 *
 * The steps are the shape the backend records (telemetry.steps_into): a span is written when it ends, so after
 * its children, and every other step names the span it happened in.
 */

const FAILURE = {
  site: 'specialist assessment',
  agent: 'detection.specialist.layout',
  at: '2026-11-03T08:00:00+00:00',
  status: 'degraded',
  issues: ['ignored confidence'],
  raw: '{"summary": "One font differs.", "confidence": "high"}',
  used: '{"summary": "One font differs."}',
};

const DETECTION_STEPS = [
  { step: 'detection_start', at: 10 },
  {
    step: 'span', at: 12.5, name: 'detection.lead_plan', id: 'plan', parent: 'run', duration_s: 2.4,
    llm_calls: [{
      model: 'claude-sonnet-5-5', input_tokens: 900, output_tokens: 120, cache_read_tokens: 3000, cache_write_tokens: 0,
      cost_usd: 0.0061, seconds: 2.3, stop_reason: 'end_turn',
      thinking: ['The payslip carries the income, so the content specialist should check the net pay first.'],
    }],
  },
  { step: 'detection_plan', at: 12.6, in_span: 'run', detection_briefs: { layout: 'Fonts.', authenticity: 'Metadata.' }, confidence: 0.8 },
  {
    step: 'span', at: 14, name: 'tool.font_consistency_tool', id: 'font', parent: 'layout', duration_s: 0.8, kind: 'tool',
    tool: 'font_consistency_tool', input: '{}', findings: 1, unable_to_assess: false, nothing_to_check: false,
    output: '{"tool": "font_consistency_tool", "findings": [{"id": "font_consistency_tool-1"}]}',
  },
  {
    step: 'span', at: 16, name: 'detection.specialist.layout', id: 'layout-turn', parent: 'layout', duration_s: 1.5,
    llm_calls: [{ model: 'claude-haiku-5-5', input_tokens: 400, output_tokens: 300, cost_usd: 0.0012, seconds: 1.4, stop_reason: 'end_turn', thinking: [] }],
    validation_failures: [FAILURE],
  },
  {
    step: 'span', at: 16.2, name: 'detection.review.layout', id: 'layout', parent: 'run', duration_s: 3.5, kind: 'agent',
    brief: 'Check the fonts.', tool_calls: [{ name: 'font_consistency_tool', args: {} }], summary: 'One font differs.',
  },
  {
    step: 'span', at: 15, name: 'detection.review.authenticity', id: 'auth', parent: 'run', duration_s: 2, kind: 'agent',
    brief: 'Check the metadata.', tool_calls: [], summary: 'Nothing worth flagging.',
  },
  { step: 'run_audit', at: 17, in_span: 'run', model_calls: 2, call_ceiling: 12, output_repairs: [FAILURE] },
  { step: 'span', at: 17.1, name: 'detection.run', id: 'run', parent: null, duration_s: 7, kind: 'run', run: 'Fraud detection' },
];

const card = (page: Page) => page.getByRole('region', { name: 'Agent trace' });
const panel = (page: Page) => page.getByRole('dialog', { name: 'Agent trace' });
// The row whose name is exactly `label` (a tool's row also shows its outcome).
const row = (page: Page, label: string) =>
  panel(page).locator('.trace-row-main', { has: page.getByText(label, { exact: true }) });
const details = (page: Page) => panel(page).locator('.trace-details');

test.describe('Trace agent runs @SCRUM-59', () => {
  async function openTrace(page: Page, backend: Parameters<typeof loadFixture>[0]) {
    const fixture = loadFixture(backend, 'anchorpoint');
    const document = fixture.document('Credit_Bureau_Report');
    backend.traces[document.id] = { document_id: document.id, started_at: 0, steps: DETECTION_STEPS };
    await page.goto(fixture.documentUrl(document));
    await card(page).locator('.trace-run', { hasText: 'Fraud detection' }).click();
  }

  test('the card lists each run, and opening one shows its tree of agents and tool calls with totals', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const document = fixture.document('Credit_Bureau_Report');
    backend.traces[document.id] = { document_id: document.id, started_at: 0, steps: DETECTION_STEPS };
    await page.goto(fixture.documentUrl(document));

    const run = card(page).locator('.trace-run');
    await expect(run).toHaveCount(1);
    await expect(run).toContainText('Fraud detection');
    await expect(run).toContainText('2 calls');
    await expect(run).toContainText('⚠ 1');
    await run.click();

    await expect(row(page, 'Lead investigator · plan')).toBeVisible();
    await expect(row(page, 'Layout specialist')).toBeVisible();
    await expect(panel(page).locator('.trace-row-main', { hasText: 'font_consistency_tool' })).toContainText('1 finding(s)');
    const totals = panel(page).locator('.trace-drawer-head');
    await expect(totals).toContainText('Claude calls2');
    await expect(totals).toContainText('Failed validations1');
    // The tool ran inside the layout specialist's review.
    const tool = panel(page).getByRole('treeitem').filter({ hasText: 'font_consistency_tool' });
    await expect(tool).toHaveAttribute('aria-level', '3');

    await panel(page).getByRole('button', { name: 'Close trace' }).click();
    await expect(panel(page)).toHaveCount(0);
  });

  test("an agent's tool calls are listed in order, and an agent that called none says so", async ({ page, backend }) => {
    await openTrace(page, backend);

    await row(page, 'Layout specialist').click();
    await expect(details(page).locator('.trace-tool-calls li')).toHaveText(['font_consistency_tool']);

    await row(page, 'Authenticity specialist').click();
    await expect(details(page)).toContainText('No tools were called.');
  });

  test("a Claude call shows the model's reasoning, tokens and cost", async ({ page, backend }) => {
    await openTrace(page, backend);

    await row(page, 'Lead investigator · plan').click();

    await details(page).getByRole('tab', { name: /Reasoning/ }).click();
    await expect(details(page).locator('.trace-reasoning')).toHaveText(
      'The payslip carries the income, so the content specialist should check the net pay first.',
    );
    await details(page).getByRole('tab', { name: /Claude calls/ }).click();
    await expect(details(page).locator('.trace-table')).toContainText('claude-sonnet-5-5');
    await expect(details(page)).toContainText('$0.0061');
  });

  test('a reply that failed validation keeps its raw payload, agent and time', async ({ page, backend }) => {
    await openTrace(page, backend);

    await expect(row(page, 'Layout specialist · turn')).toContainText('⚠ 1');
    await row(page, 'Layout specialist · turn').click();
    await details(page).getByRole('tab', { name: /Failed validation/ }).click();

    const failure = details(page).locator('.trace-failure');
    await expect(failure).toContainText('specialist assessment');
    await expect(failure).toContainText('detection.specialist.layout');
    await expect(failure.locator('time')).toHaveAttribute('datetime', FAILURE.at);
    await expect(failure).toContainText('"confidence": "high"');
  });

  test('a document with no trace says so', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    await page.goto(fixture.documentUrl(fixture.document('Credit_Bureau_Report')));

    await expect(card(page)).toContainText("No trace yet: this document hasn't been processed.");
    await expect(card(page).getByRole('button', { name: 'Open trace' })).toHaveCount(0);
  });
});
