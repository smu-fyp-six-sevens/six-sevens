// Copied from frontend/e2e/specs/agent-trace.spec.ts (the trace steps that spec renders), for the film's captures.
const FAILURE = {
  site: 'specialist assessment',
  agent: 'detection.specialist.layout',
  at: '2026-11-03T08:00:00+00:00',
  status: 'degraded',
  issues: ['ignored confidence'],
  raw: '{"summary": "One font differs.", "confidence": "high"}',
  used: '{"summary": "One font differs."}',
};

export const DETECTION_STEPS = [
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
    tool: 'font_consistency_tool', input: '{"document": "Anchorpoint_BankStatement_Jan2026.pdf", "pages": [1], "fields": ["closing_balance"]}', findings: 1, unable_to_assess: false, nothing_to_check: false,
    output: '{"tool": "font_consistency_tool", "body_font": "Helvetica-Bold 9pt", "findings": [{"id": "font_consistency_tool-1", "severity": "medium", "text": "812,400.00", "field": "closing_balance", "expected": "Helvetica-Bold 9pt", "detected": "Helvetica 9pt", "page": 1, "summary": "The closing balance is set in a different weight from the rest of the statement."}]}',
  },
  {
    step: 'span', at: 16, name: 'detection.specialist.layout', id: 'layout-turn', parent: 'layout', duration_s: 1.5,
    llm_calls: [{ model: 'claude-haiku-5-5', input_tokens: 400, output_tokens: 300, cost_usd: 0.0012, seconds: 1.4, stop_reason: 'end_turn', thinking: [] }],
    validation_failures: [FAILURE],
  },
  {
    step: 'span', at: 16.2, name: 'detection.review.layout', id: 'layout', parent: 'run', duration_s: 3.5, kind: 'agent',
    brief: 'Check the fonts and layout of the closing balance.', tool_calls: [{ name: 'font_consistency_tool', args: { document: 'Anchorpoint_BankStatement_Jan2026.pdf' } }], summary: 'The closing balance is set in a different weight.',
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
