// Turns a stored trace (a document's langgraph trace, or a case analysis run's) into a tree of runs, agents, model
// calls, tools and events. The backend records each span as a "span" step when it ends (telemetry.steps_into), and
// every other step names the span it happened in (`in_span`). Traces from before spans were recorded have neither,
// and show as a flat list of events.

export interface TraceStep {
  step: string;
  at: number;
  [key: string]: unknown;
}

export type TraceNodeKind = 'run' | 'agent' | 'llm' | 'tool' | 'chain' | 'call' | 'event';

export interface LlmCall {
  model?: string;
  input_tokens?: number;
  output_tokens?: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  cost_usd?: number;
  seconds?: number | null;
  stop_reason?: string | null;
  thinking?: string[];
  error?: string;
}

export interface ValidationFailure {
  site?: string;
  agent?: string;
  at?: string;
  status?: string;
  issues?: string[];
  raw?: string;
  used?: string | null;
}

export interface TraceTotals {
  calls: number;
  inputTokens: number;
  outputTokens: number;
  cost: number;
  failures: number;
}

export interface TraceNode {
  key: string;
  kind: TraceNodeKind;
  label: string;
  // The span or step name as recorded.
  name: string;
  subtitle?: string;
  // Seconds since the trace started.
  start: number;
  end: number;
  error?: string;
  step: TraceStep;
  call?: LlmCall;
  children: TraceNode[];
  totals: TraceTotals;
}

export interface BuiltTrace {
  roots: TraceNode[];
  totals: TraceTotals;
  start: number;
  end: number;
}

const num = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) ? value : 0);
const str = (value: unknown): string | undefined => (typeof value === 'string' && value !== '' ? value : undefined);
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

export function humanize(text: string): string {
  const spaced = text.replace(/[._]+/g, ' ').trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

const SPAN_LABELS: Record<string, string> = {
  'langgraph.process_document': 'Classification and extraction',
  'langgraph.rerun_extraction': 'Extraction rerun',
  'detection.run': 'Fraud detection',
  'case_pipeline.run': 'Case analysis',
  'classify.agent_call': 'Classifier · model turn',
  'extract.run_extraction': 'Extraction',
  'detection.lead_plan': 'Lead investigator · plan',
  'detection.lead_review': 'Lead investigator · review',
  'detection.numerical_binder': 'Numerical binder',
  'case_pipeline.evidence_requests': 'Evidence requests',
  'case_pipeline.summary': 'Summary writer',
};

function spanLabel(step: TraceStep, name: string): string {
  if (SPAN_LABELS[name]) return SPAN_LABELS[name];
  if (name.startsWith('tool.')) return name.slice(5);
  if (name.startsWith('detection.review.')) return `${humanize(name.slice(17))} specialist`;
  if (name.startsWith('detection.specialist.')) return `${humanize(name.slice(21))} · model turn`;
  return str(step.run) ?? humanize(name.replace(/^case_pipeline\.|^cross_document\./, ''));
}

function spanKind(step: TraceStep, name: string): TraceNodeKind {
  const declared = str(step.kind);
  if (declared === 'run' || declared === 'agent' || declared === 'tool') return declared;
  if (name.startsWith('tool.')) return 'tool';
  return list(step.llm_calls).length > 0 ? 'llm' : 'chain';
}

// One line saying what a step found, for the steps worth a summary.
export function describeStep(step: TraceStep): string | undefined {
  switch (step.step) {
    case 'run_start':
      return `${list(step.documents).length} document(s)`;
    case 'ingest':
      return str(step.error) ?? str(step.blocked) ?? str(step.filename);
    case 'rule_classify':
      return step.rule_top_type ? `${String(step.rule_top_type)} (${String(step.rule_top_strength ?? '—')})` : 'no rule match';
    case 'agent_call': {
      const calls = list(step.tool_calls).map(String);
      return calls.length ? `calls ${calls.join(', ')}` : 'answers without a tool';
    }
    case 'classification_finalised':
      return `${String(step.document_type ?? 'no type')} · confidence ${String(step.confidence ?? '—')}`;
    case 'extraction':
      return str(step.document_type);
    case 'detection_plan':
      return `briefs for ${Object.keys((step.detection_briefs as object | undefined) ?? {}).length} specialists`;
    case 'detection_specialist': {
      const tools = list(step.tools_called).map(String);
      return `${num(step.finding_count)} finding(s) · tools: ${tools.length ? tools.join(', ') : 'none'}`;
    }
    case 'detection_done':
      return `verdict ${String(step.detection_verdict)}`;
    case 'hypotheses':
      return `${list(step.proposed).length} hypothesis(es) proposed`;
    case 'tool_call':
      return `${String(step.tool)} for ${String(step.hypothesis)}`;
    case 'verify':
      return step.kept ? `${String(step.hypothesis)} kept` : `${String(step.hypothesis)} dropped${step.reason ? `: ${String(step.reason)}` : ''}`;
    case 'scoring':
      return `${String(step.level ?? '—')} · score ${String(step.score ?? '—')}/100 · ${String(step.points ?? '—')} points from ${list(step.findings).length} finding(s) on ${String(step.documents_scanned ?? '—')}/${String(step.documents_total ?? '—')} documents`;
    case 'run_audit':
      return `${num(step.model_calls)}/${String(step.call_ceiling ?? '—')} model calls · ${list(step.output_repairs).length} repaired output(s)`;
    case 'done':
      return str(step.final_status) ?? str(step.status);
    case 'error':
    case 'detection_error':
      return str(step.error);
    default:
      return undefined;
  }
}

function emptyTotals(): TraceTotals {
  return { calls: 0, inputTokens: 0, outputTokens: 0, cost: 0, failures: 0 };
}

function addTotals(into: TraceTotals, from: TraceTotals): void {
  into.calls += from.calls;
  into.inputTokens += from.inputTokens;
  into.outputTokens += from.outputTokens;
  into.cost += from.cost;
  into.failures += from.failures;
}

// Model calls are recorded on their span with a duration but no start, so they are laid end to end from its start.
function callNodes(span: TraceNode): TraceNode[] {
  let cursor = span.start;
  return list(span.step.llm_calls).map((raw, index) => {
    const call = (raw ?? {}) as LlmCall;
    const start = cursor;
    const end = Math.min(span.end, start + num(call.seconds));
    cursor = end;
    const totals = {
      calls: 1,
      inputTokens: num(call.input_tokens) + num(call.cache_read_tokens) + num(call.cache_write_tokens),
      outputTokens: num(call.output_tokens),
      cost: num(call.cost_usd),
      failures: 0,
    };
    return {
      key: `${span.key}:call:${index}`,
      kind: 'call',
      label: call.model ?? 'Claude call',
      name: 'llm.call',
      subtitle: call.error ? 'failed' : call.stop_reason ?? undefined,
      start,
      end,
      error: call.error,
      step: span.step,
      call,
      children: [],
      totals,
    };
  });
}

export function buildTrace(steps: TraceStep[]): BuiltTrace {
  const spans = new Map<string, TraceNode>();
  steps.forEach((step, index) => {
    if (step.step !== 'span') return;
    const name = str(step.name) ?? 'span';
    const end = num(step.at);
    const node: TraceNode = {
      key: str(step.id) ?? `span-${index}`,
      kind: spanKind(step, name),
      label: spanLabel(step, name),
      name,
      start: Math.max(0, end - num(step.duration_s)),
      end,
      error: str(step.error),
      step,
      children: [],
      totals: emptyTotals(),
    };
    node.subtitle = node.kind === 'tool' ? toolOutcome(step) : str(step.summary);
    spans.set(node.key, node);
  });

  const top: TraceNode[] = [];
  for (const node of spans.values()) {
    const parent = str(node.step.parent);
    const owner = parent ? spans.get(parent) : undefined;
    (owner ? owner.children : top).push(node);
  }

  steps.forEach((step, index) => {
    if (step.step === 'span') return;
    const at = num(step.at);
    const node: TraceNode = {
      key: `event-${index}`,
      kind: 'event',
      label: humanize(step.step),
      name: step.step,
      subtitle: describeStep(step),
      start: at,
      end: at,
      error: step.step.endsWith('error') ? str(step.error) ?? str(step.findings_error) : undefined,
      step,
      children: [],
      totals: { ...emptyTotals(), failures: list(step.output_repairs).length },
    };
    const owner = spans.get(str(step.in_span) ?? '')
      // An event recorded just outside a run (its start, or an error after it) is shown with that run.
      ?? top.find((root) => root.kind === 'run' && at >= root.start - 1 && at <= root.end + 1);
    (owner ? owner.children : top).push(node);
  });

  const finish = (node: TraceNode): void => {
    node.children.forEach(finish);
    const calls = node.kind === 'event' || node.kind === 'call' ? [] : callNodes(node);
    node.children = [...calls, ...node.children].sort((a, b) => a.start - b.start || a.end - b.end);
    // Failed validations count once, from the run's audit step; each also sits on the span it happened in.
    for (const child of node.children) addTotals(node.totals, child.totals);
  };
  top.forEach(finish);
  top.sort((a, b) => a.start - b.start);

  const totals = emptyTotals();
  top.forEach((node) => addTotals(totals, node.totals));
  return {
    roots: top,
    totals,
    start: top.length ? Math.min(...top.map((n) => n.start)) : 0,
    end: top.length ? Math.max(...top.map((n) => n.end)) : 0,
  };
}

function toolOutcome(step: TraceStep): string | undefined {
  if (step.error) return 'failed';
  if (step.unable_to_assess) return str(step.reason) ?? 'unable to assess';
  if (step.nothing_to_check) return 'nothing to check';
  if (typeof step.findings === 'number') return `${step.findings} finding(s)`;
  return undefined;
}

// Every node under `node`, depth first, with its depth: the rows the tree shows.
export function flatten(nodes: TraceNode[], collapsed: Set<string>, depth = 0): Array<{ node: TraceNode; depth: number }> {
  return nodes.flatMap((node) => [
    { node, depth },
    ...(collapsed.has(node.key) ? [] : flatten(node.children, collapsed, depth + 1)),
  ]);
}
