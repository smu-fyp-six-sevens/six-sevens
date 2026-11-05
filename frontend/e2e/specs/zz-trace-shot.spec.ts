import { test } from '../support/test';
import { loadFixture } from '../support/fixtures';

const call = (input: number, output: number, cost: number) => ({
  model: 'claude-haiku-5-5', input_tokens: input, output_tokens: output, cost_usd: cost, seconds: 1.2, stop_reason: 'end_turn', thinking: ['Checks the fonts first.'],
});
const STEPS = [
  { step: 'ingest', at: 0.2, in_span: 'doc', filename: 'payslip.pdf' },
  { step: 'rule_classify', at: 0.3, in_span: 'doc', rule_top_type: 'payslip', rule_top_strength: 'strong' },
  { step: 'span', at: 2.3, name: 'classify.agent_call', id: 'cls', parent: 'doc', duration_s: 2, llm_calls: [call(800, 90, 0.004)] },
  { step: 'agent_call', at: 2.31, in_span: 'cls', tool_calls: [] },
  { step: 'classification_finalised', at: 2.4, in_span: 'doc', document_type: 'payslip', confidence: 0.97 },
  { step: 'span', at: 6.4, name: 'extract.run_extraction', id: 'ext', parent: 'doc', duration_s: 4, llm_calls: [call(1500, 400, 0.011)] },
  { step: 'extraction', at: 6.5, in_span: 'doc', document_type: 'payslip' },
  { step: 'run_audit', at: 6.6, in_span: 'doc', model_calls: 2, call_ceiling: 12, output_repairs: [] },
  { step: 'span', at: 6.7, name: 'langgraph.process_document', id: 'doc', parent: null, duration_s: 6.7, kind: 'run', run: 'Classification and extraction' },
  { step: 'detection_start', at: 3600 },
  { step: 'span', at: 3602.5, name: 'detection.lead_plan', id: 'plan', parent: 'run', duration_s: 2.4, llm_calls: [call(900, 120, 0.0061)] },
  { step: 'detection_plan', at: 3602.6, in_span: 'run', detection_briefs: { layout: 'Fonts.' }, confidence: 0.8 },
  { step: 'span', at: 3604, name: 'tool.font_consistency_tool', id: 'font', parent: 'layout', duration_s: 0.8, kind: 'tool', tool: 'font_consistency_tool', input: '{}', findings: 1, output: '{}' },
  { step: 'span', at: 3606, name: 'detection.specialist.layout_rendering', id: 'turn', parent: 'layout', duration_s: 1.5, llm_calls: [call(400, 300, 0.0012)] },
  { step: 'detection_specialist', at: 3606.1, in_span: 'layout', tools_called: ['font_consistency_tool'], finding_count: 1 },
  { step: 'span', at: 3606.2, name: 'detection.review.layout_rendering', id: 'layout', parent: 'run', duration_s: 3.5, kind: 'agent', brief: 'Check the fonts.', tool_calls: [{ name: 'font_consistency_tool', args: {} }], summary: 'One font differs.' },
  { step: 'run_audit', at: 3607, in_span: 'run', model_calls: 2, call_ceiling: 12, output_repairs: [] },
  { step: 'span', at: 3607.1, name: 'detection.run', id: 'run', parent: null, duration_s: 7, kind: 'run', run: 'Fraud detection' },
];

test('screenshot', async ({ page, backend }) => {
  const fixture = loadFixture(backend, 'anchorpoint');
  const document = fixture.document('Credit_Bureau_Report');
  backend.traces[document.id] = { document_id: document.id, started_at: 0, steps: STEPS };
  await page.setViewportSize({ width: 1440, height: 860 });
  await page.goto(fixture.documentUrl(document));
  await page.getByRole('region', { name: 'Agent trace' }).locator('.trace-run').last().click();
  const panel = page.getByRole('dialog', { name: 'Agent trace' });
  await panel.getByRole('button', { name: 'Expand Classifier' }).click();
  await panel.getByRole('button', { name: 'Expand Specialists' }).click();
  await panel.locator('.trace-row-main', { hasText: 'Type decision' }).click();
  await page.screenshot({ path: 'C:/Users/LENOVO/AppData/Local/Temp/claude/0d901e780b1fdade/3299347f-14c9-4f1e-820d-d13ff1ceb19e/scratchpad/trace3.png' });
});
