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
  // Recorded after the other specialist's span, whose end is earlier: only its own span's steps count for its time.
  { step: 'detection_specialist', at: 16.1, in_span: 'layout', finding_count: 1, tools_called: ['font_consistency_tool'] },
  { step: 'run_audit', at: 17, in_span: 'run', model_calls: 2, call_ceiling: 12, output_repairs: [FAILURE] },
  { step: 'span', at: 17.1, name: 'detection.run', id: 'run', parent: null, duration_s: 7, kind: 'run', run: 'Fraud detection' },
];

const panel = (page: Page) => page.getByRole('dialog', { name: 'Agent trace' });
// The row whose name is exactly `label` (a tool's row also shows its outcome).
const row = (page: Page, label: string) =>
  panel(page).locator('.trace-row-main', { has: page.getByText(label, { exact: true }) });
const details = (page: Page) => panel(page).locator('.trace-details');
const expand = (page: Page, label: string) => panel(page).getByRole('button', { name: `Expand ${label}` }).click();

test.describe('Trace agent runs @SCRUM-59', () => {
  async function openTrace(page: Page, backend: Parameters<typeof loadFixture>[0]) {
    const fixture = loadFixture(backend, 'anchorpoint');
    const document = fixture.document('Credit_Bureau_Report');
    backend.traces[document.id] = { document_id: document.id, started_at: 0, steps: DETECTION_STEPS };
    await page.goto(fixture.documentUrl(document));
    await page.getByRole('button', { name: 'Document actions' }).click();
    await page.getByRole('menuitem', { name: 'Agent trace' }).click();
    await expand(page, 'Lead agent');
    await expand(page, 'Specialists');
  }

  test('the card lists each run, and opening one shows its tree of agents and tool calls with totals', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const document = fixture.document('Credit_Bureau_Report');
    backend.traces[document.id] = { document_id: document.id, started_at: 0, steps: DETECTION_STEPS };
    await page.goto(fixture.documentUrl(document));
    await page.getByRole('button', { name: 'Document actions' }).click();
    await page.getByRole('menuitem', { name: 'Agent trace' }).click();
    await expand(page, 'Lead agent');
    await expand(page, 'Specialists');

    await expect(row(page, 'Plan')).toBeVisible();
    await expect(row(page, 'Layout specialist')).toBeVisible();
    await expect(panel(page).locator('.trace-row-main', { hasText: 'Font consistency check' }).first()).toContainText('1 finding(s)');
    const totals = panel(page).locator('.trace-drawer-head');
    await expect(totals).toContainText('Claude calls2');
    await expect(totals).toContainText('Failed validations1');
    // Run, Specialists, the layout specialist's review, then the tool it ran.
    const tool = panel(page).getByRole('treeitem').filter({ hasText: 'Font consistency check' }).first();
    await expect(tool).toHaveAttribute('aria-level', '4');

    await panel(page).getByRole('button', { name: 'Close trace' }).click();
    await expect(panel(page)).toHaveCount(0);
  });

  test("an agent's tool calls are listed in order, and an agent that called none says so", async ({ page, backend }) => {
    await openTrace(page, backend);

    await row(page, 'Layout specialist').click();
    await expect(details(page).locator('.trace-tool-calls li')).toHaveText(['Font consistency check']);

    await row(page, 'Authenticity specialist').click();
    await expect(details(page)).toContainText('No tools were called.');
  });

  test("a Claude call shows the model's reasoning, tokens and cost", async ({ page, backend }) => {
    await openTrace(page, backend);

    await row(page, 'Plan').click();

    await details(page).getByRole('button', { name: /Reasoning/ }).click();
    await expect(details(page).locator('.trace-reasoning')).toHaveText(
      'The payslip carries the income, so the content specialist should check the net pay first.',
    );
    await details(page).getByRole('button', { name: /Claude calls/ }).click();
    await expect(details(page).locator('.trace-table')).toContainText('claude-sonnet-5-5');
    await expect(details(page)).toContainText('$0.0061');
  });

  test('a reply that failed validation keeps its raw payload, agent and time', async ({ page, backend }) => {
    await openTrace(page, backend);

    await expect(row(page, 'Layout')).toContainText('⚠ 1');
    await row(page, 'Layout').click();
    await details(page).getByRole('button', { name: /Failed validation/ }).click();

    const failure = details(page).locator('.trace-failure');
    await expect(failure).toContainText('specialist assessment');
    await expect(failure).toContainText('detection.specialist.layout');
    await expect(failure.locator('time')).toHaveAttribute('datetime', FAILURE.at);
    await expect(failure).toContainText('"confidence": "high"');
  });

  test('every row with Claude calls shows its tokens and cost, and steps show under their span', async ({ page, backend }) => {
    await openTrace(page, backend);

    await expect(row(page, 'Plan')).toContainText('4.0k tokens · $0.0061');
    // The run adds up what happened in it.
    await expect(row(page, 'Anomaly detection')).toContainText('4.7k tokens · $0.0073');
    await expect(panel(page).locator('.trace-row-main', { hasText: 'Investigation plan' })).toContainText('briefs for 2 specialists');

    // A step shows its time since the step before; the run's duration is the run's, not the trace's.
    await expect(panel(page).locator('.trace-row-main', { hasText: 'Investigation plan' })).toContainText('100 ms');
    await expect(panel(page).locator('.trace-drawer-head')).toContainText('Duration7.00s');
    await expect(panel(page).locator('.trace-notice')).toHaveCount(0);

    await expect(panel(page).getByLabel('Show steps')).toHaveCount(0);
    await expect(panel(page).getByRole('tab')).toHaveCount(0);
  });

  test('a run reads as its stages, each folded until opened', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const document = fixture.document('Credit_Bureau_Report');
    backend.traces[document.id] = { document_id: document.id, started_at: 0, steps: DETECTION_STEPS };
    await page.goto(fixture.documentUrl(document));
    await page.getByRole('button', { name: 'Document actions' }).click();
    await page.getByRole('menuitem', { name: 'Agent trace' }).click();

    await expect(row(page, 'Lead agent')).toBeVisible();
    await expect(row(page, 'Specialists')).toBeVisible();
    await expect(row(page, 'Plan')).toHaveCount(0);

    await row(page, 'Lead agent').click();
    await expect(row(page, 'Plan')).toBeVisible();
    // A heading has no kind label, and the run it's in has no start offset.
    await expect(details(page).getByRole('heading', { name: 'Lead agent' })).toBeVisible();
    await expect(details(page).locator('.trace-kind-name')).toHaveCount(0);
    await row(page, 'Anomaly detection').click();
    await expect(details(page)).not.toContainText('Starts');
    await row(page, 'Plan').click();
    await expect(details(page)).toContainText('Starts');
  });

  test("a specialist's step is timed against its own span, not the one running beside it", async ({ page, backend }) => {
    await openTrace(page, backend);

    await expect(panel(page).locator('.trace-row-main', { hasText: 'Specialist finished' })).toContainText('100 ms');
  });

  test('the details are rows that open and close, and stay open from one step to the next', async ({ page, backend }) => {
    await openTrace(page, backend);

    await panel(page).locator('.trace-row-main', { hasText: 'Investigation plan' }).click();
    const overview = details(page).getByRole('button', { name: 'Overview' });
    const attributes = details(page).getByRole('button', { name: 'Attributes' });
    await expect(overview).toHaveAttribute('aria-expanded', 'true');
    await expect(details(page)).toContainText('briefs for 2 specialists');
    await expect(attributes).toHaveAttribute('aria-expanded', 'false');
    await expect(details(page)).not.toContainText('detection_briefs');

    await attributes.click();
    await expect(details(page)).toContainText('detection_briefs');
    await overview.click();
    await expect(details(page)).not.toContainText('briefs for 2 specialists');

    await panel(page).locator('.trace-row-main', { hasText: 'Detection started' }).click();
    await expect(details(page).getByRole('heading', { name: 'Detection started' })).toBeVisible();
    await panel(page).locator('.trace-row-main', { hasText: 'Run audit' }).click();
    await expect(details(page).getByRole('button', { name: 'Attributes' })).toHaveAttribute('aria-expanded', 'true');
  });

  test("a trace with no spans says its Claude usage wasn't recorded, rather than zero", async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const document = fixture.document('Credit_Bureau_Report');
    backend.traces[document.id] = { document_id: document.id, started_at: 0, steps: [
      { step: 'ingest', at: 1 },
      { step: 'agent_call', at: 3, tool_calls: [] },
      // An hour later, once an officer approved it: the wait isn't time worked.
      { step: 'detection_start', at: 3603 },
      { step: 'detection_done', at: 3607, detection_verdict: 'CLEAN' },
    ] };
    await page.goto(fixture.documentUrl(document));
    await page.getByRole('button', { name: 'Document actions' }).click();
    await page.getByRole('menuitem', { name: 'Agent trace' }).click();

    await expand(page, 'Classifier');
    await expect(panel(page).locator('.trace-row-main', { hasText: 'Type decision' })).toContainText('decided without using a tool');
    const totals = panel(page).locator('.trace-drawer-head');
    await expect(totals).toContainText('TokensNot recorded');
    await expect(totals).toContainText('CostNot recorded');
    await expect(totals).not.toContainText('$0');
    await expect(totals).toContainText('Duration6.00s');
    await expect(panel(page).locator('.trace-row-main', { hasText: 'Type decision' })).toContainText('2.00s');
    await expect(panel(page).locator('.trace-row-main', { hasText: 'Detection started' }).locator('.trace-duration')).toHaveCount(0);
    await expect(panel(page).locator('.trace-notice')).toContainText('Claude Cost tab');
  });

  test('Agent Trace in the menu shows the document\'s trace on a document, and the analysis\'s on a case', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    const document = fixture.document('Credit_Bureau_Report');
    backend.traces[document.id] = { document_id: document.id, started_at: 0, steps: DETECTION_STEPS };
    await page.goto(fixture.documentUrl(document));
    await expect(page.locator('.document-details-body')).toBeVisible();
    await page.getByRole('button', { name: 'Document actions' }).click();
    await page.getByRole('menuitem', { name: 'Agent trace' }).click();
    await expect(panel(page)).toBeVisible();
    await expect(panel(page).locator('.trace-row-main', { hasText: 'Anomaly detection' })).toBeVisible();
  });

  test('a document with no trace says so', async ({ page, backend }) => {
    const fixture = loadFixture(backend, 'anchorpoint');
    await page.goto(fixture.documentUrl(fixture.document('Credit_Bureau_Report')));
    await page.getByRole('button', { name: 'Document actions' }).click();
    await page.getByRole('menuitem', { name: 'Agent trace' }).click();
    await expect(panel(page)).toContainText("No trace yet: this document hasn't been processed.");
  });
});
