// Scratch: screenshots of the agent trace view for design review. Deleted before handing over.
import { test } from '../support/test';
import { loadFixture } from '../support/fixtures';

const OUT = 'C:/Users/LENOVO/AppData/Local/Temp/claude/c--Users-LENOVO-Documents-GitHub-six-sevens/dd11c794-1e7f-454d-82ef-af5c31a0a7d6/scratchpad/shots';

const call = (model: string, inTok: number, outTok: number, cost: number, seconds: number, thinking: string[] = []) => ({
  model, input_tokens: inTok, output_tokens: outTok, cache_read_tokens: 2800, cache_write_tokens: 0,
  cost_usd: cost, seconds, stop_reason: 'end_turn', thinking,
});

const STEPS = [
  { step: 'ingest', at: 0.4, in_span: 'p', filename: 'Payslip_2026-08.pdf', text_available: true },
  { step: 'content_guard', at: 0.9, in_span: 'p', guard_risk: 'none' },
  { step: 'rule_classify', at: 1.0, in_span: 'p', rule_top_type: 'payslip', rule_top_strength: 0.72 },
  { step: 'span', at: 4.1, name: 'classify.agent_call', id: 'c1', parent: 'p', duration_s: 3.0,
    llm_calls: [call('claude-sonnet-5-5', 1800, 160, 0.0081, 2.9, ['The rule match is fairly strong but not decisive; ask the text classifier to corroborate.'])] },
  { step: 'span', at: 5.6, name: 'tool.llm_classify_tool', id: 't1', parent: 'p', duration_s: 1.4, kind: 'tool', tool: 'llm_classify_tool', input: '{}',
    output: '{"candidates": [{"document_type": "payslip", "match_strength": 0.94}]}',
    llm_calls: [call('claude-haiku-5-5', 1200, 90, 0.0011, 1.3)] },
  { step: 'span', at: 7.0, name: 'classify.agent_call', id: 'c2', parent: 'p', duration_s: 1.3,
    llm_calls: [call('claude-sonnet-5-5', 2100, 140, 0.0052, 1.2)] },
  { step: 'classification_finalised', at: 7.1, in_span: 'p', document_type: 'payslip', confidence: 0.94 },
  { step: 'span', at: 13.2, name: 'extract.run_extraction', id: 'x', parent: 'p', duration_s: 6.0,
    llm_calls: [call('claude-sonnet-5-5', 3400, 900, 0.0221, 5.9)] },
  { step: 'extraction', at: 13.3, in_span: 'p', document_type: 'payslip' },
  { step: 'run_audit', at: 13.4, in_span: 'p', model_calls: 4, call_ceiling: 8, output_repairs: [] },
  { step: 'done', at: 13.5, in_span: 'p', final_status: 'analyzed' },
  { step: 'span', at: 13.6, name: 'langgraph.process_document', id: 'p', parent: null, duration_s: 13.5, kind: 'run', run: 'Classification and extraction' },

  { step: 'detection_start', at: 60.0 },
  { step: 'span', at: 62.5, name: 'detection.lead_plan', id: 'plan', parent: 'run', duration_s: 2.4,
    llm_calls: [call('claude-sonnet-5-5', 900, 220, 0.0061, 2.3, ['Payslips carry the income figure, so the content specialist should check net pay against gross and deductions first.'])] },
  { step: 'detection_plan', at: 62.6, in_span: 'run', detection_briefs: { layout: 'Fonts on the figures.', content: 'Net pay arithmetic.', authenticity: 'Metadata.' }, confidence: 0.8 },
  { step: 'span', at: 64.0, name: 'tool.font_consistency_tool', id: 'f', parent: 'layout', duration_s: 1.1, kind: 'tool', tool: 'font_consistency_tool', input: '{}', findings: 1, unable_to_assess: false, nothing_to_check: false, output: '{"tool": "font_consistency_tool", "findings": [{"id": "font_consistency_tool-1", "summary": "Net pay is in a different font."}]}' },
  { step: 'span', at: 64.9, name: 'tool.text_overlay_tool', id: 'o', parent: 'layout', duration_s: 0.6, kind: 'tool', tool: 'text_overlay_tool', input: '{}', findings: 0, unable_to_assess: false, nothing_to_check: false, output: '{"findings": []}' },
  { step: 'span', at: 66.9, name: 'detection.specialist.layout', id: 'lt', parent: 'layout', duration_s: 1.9,
    llm_calls: [call('claude-haiku-5-5', 2400, 380, 0.0031, 1.8, ['Only the net pay differs in font, which is how a pasted value looks.'])],
    validation_failures: [{ site: 'specialist assessment', agent: 'detection.specialist.layout', at: '2026-11-03T08:01:06+00:00', status: 'degraded', issues: ['ignored confidence'], raw: '{"summary": "Net pay differs.", "confidence": "high"}', used: '{"summary": "Net pay differs."}' }] },
  { step: 'span', at: 67.0, name: 'detection.review.layout', id: 'layout', parent: 'run', duration_s: 4.4, kind: 'agent', brief: 'Fonts on the figures.', tool_calls: [{ name: 'font_consistency_tool', args: {} }, { name: 'text_overlay_tool', args: {} }], summary: 'Net pay is set in a different font from the rest of the payslip.', finding_count: 1 },
  { step: 'span', at: 65.8, name: 'tool.numerical_checks_tool', id: 'n', parent: 'content', duration_s: 2.9, kind: 'tool', tool: 'numerical_checks_tool', input: '{"bindings": [{"role": "net_pay", "field": "net_pay"}]}', findings: 0, unable_to_assess: false, nothing_to_check: false, output: '{"findings": []}',
    llm_calls: [call('claude-haiku-5-5', 1500, 300, 0.0019, 1.5)] },
  { step: 'span', at: 68.2, name: 'detection.review.content', id: 'content', parent: 'run', duration_s: 5.6, kind: 'agent', brief: 'Net pay arithmetic.', tool_calls: [{ name: 'numerical_checks_tool', args: { bindings: [{ role: 'net_pay', field: 'net_pay' }] } }], summary: 'Gross less deductions equals net pay.', finding_count: 0 },
  { step: 'span', at: 64.6, name: 'detection.review.authenticity', id: 'auth', parent: 'run', duration_s: 2.0, kind: 'agent', brief: 'Metadata.', tool_calls: [], summary: 'Nothing worth flagging.', finding_count: 0 },
  { step: 'span', at: 71.0, name: 'detection.lead_review', id: 'review', parent: 'run', duration_s: 2.7,
    llm_calls: [call('claude-sonnet-5-5', 3100, 260, 0.0102, 2.6)] },
  { step: 'run_audit', at: 71.1, in_span: 'run', model_calls: 6, call_ceiling: 12, output_repairs: [{ site: 'specialist assessment', agent: 'detection.specialist.layout', at: '2026-11-03T08:01:06+00:00', status: 'degraded', issues: ['ignored confidence'], raw: '{}', used: '{}' }] },
  { step: 'detection_done', at: 71.2, in_span: 'run', detection_verdict: 'flagged' },
  { step: 'span', at: 71.3, name: 'detection.run', id: 'run', parent: null, duration_s: 11.2, kind: 'run', run: 'Fraud detection' },
];

test('preview the agent trace', async ({ page, backend }) => {
  const fixture = loadFixture(backend, 'anchorpoint');
  const document = fixture.document('Credit_Bureau_Report');
  backend.traces[document.id] = { document_id: document.id, started_at: 0, steps: STEPS };
  await page.setViewportSize({ width: 1500, height: 1000 });
  await page.goto(fixture.documentUrl(document));
  const card = page.getByRole('region', { name: 'Agent trace' });
  await card.scrollIntoViewIfNeeded();
  await card.screenshot({ path: `${OUT}/1-card.png` });
  await card.locator('.trace-run', { hasText: 'Fraud detection' }).click();
  const drawer = page.getByRole('dialog', { name: 'Agent trace' });
  await page.screenshot({ path: `${OUT}/2-drawer.png` });
  await drawer.locator('.trace-row-main', { hasText: 'Layout specialist' }).first().click();
  await page.screenshot({ path: `${OUT}/3-agent.png` });
  await drawer.locator('.trace-row-main', { hasText: 'Lead investigator · plan' }).click();
  await drawer.getByRole('tab', { name: /Reasoning/ }).click();
  await page.screenshot({ path: `${OUT}/4-reasoning.png` });
  await drawer.locator('.trace-row-main', { hasText: 'Layout specialist · turn' }).click();
  await drawer.getByRole('tab', { name: /Failed validation/ }).click();
  await page.screenshot({ path: `${OUT}/5-validation.png` });
  await drawer.getByRole('tab', { name: 'Timeline' }).click();
  await drawer.getByLabel('Show steps').check();
  await page.screenshot({ path: `${OUT}/6-timeline.png` });
  await page.setViewportSize({ width: 420, height: 900 });
  await drawer.getByRole('tab', { name: /Trace tree/ }).click();
  await page.screenshot({ path: `${OUT}/7-phone.png` });
});
