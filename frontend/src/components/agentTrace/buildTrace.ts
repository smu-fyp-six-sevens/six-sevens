// Turns a stored trace (a document's langgraph trace, or a case analysis run's) into a tree of runs, agents, model
// turns, tools and steps. The backend records each span as a "span" step when it ends (telemetry.steps_into), and
// every other step names the span it happened in (`in_span`). Traces from before spans were recorded have neither,
// and show as a flat list of steps.

export interface TraceStep {
  step: string;
  at: number;
  [key: string]: unknown;
}

export type TraceNodeKind = 'run' | 'agent' | 'llm' | 'tool' | 'chain' | 'event';

export const KIND_LABELS: Record<TraceNodeKind, string> = {
  run: 'Run',
  agent: 'Agent',
  llm: 'LLM',
  tool: 'Tool',
  chain: 'Step',
  event: 'Step',
};

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
  errors: number;
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
  calls: LlmCall[];
  failures: ValidationFailure[];
  children: TraceNode[];
  totals: TraceTotals;
}

export interface BuiltTrace {
  roots: TraceNode[];
  totals: TraceTotals;
  start: number;
  end: number;
  hasSpans: boolean;
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
  'classify.agent_call': 'Classifier',
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
  if (name.startsWith('detection.specialist.')) return `${humanize(name.slice(21))} specialist · turn`;
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
  return { calls: 0, inputTokens: 0, outputTokens: 0, cost: 0, failures: 0, errors: 0 };
}

function addTotals(into: TraceTotals, from: TraceTotals): void {
  into.calls += from.calls;
  into.inputTokens += from.inputTokens;
  into.outputTokens += from.outputTokens;
  into.cost += from.cost;
  into.failures += from.failures;
  into.errors += from.errors;
}

function toolOutcome(step: TraceStep): string | undefined {
  if (step.error) return 'failed';
  if (step.unable_to_assess) return str(step.reason) ?? 'unable to assess';
  if (step.nothing_to_check) return 'nothing to check';
  if (typeof step.findings === 'number') return `${step.findings} finding(s)`;
  return undefined;
}

export function buildTrace(steps: TraceStep[]): BuiltTrace {
  const spans = new Map<string, TraceNode>();
  steps.forEach((step, index) => {
    if (step.step !== 'span') return;
    const name = str(step.name) ?? 'span';
    const end = num(step.at);
    const kind = spanKind(step, name);
    spans.set(str(step.id) ?? `span-${index}`, {
      key: str(step.id) ?? `span-${index}`,
      kind,
      label: spanLabel(step, name),
      name,
      subtitle: kind === 'tool' ? toolOutcome(step) : undefined,
      start: Math.max(0, end - num(step.duration_s)),
      end,
      error: str(step.error),
      step,
      calls: list(step.llm_calls) as LlmCall[],
      failures: list(step.validation_failures) as ValidationFailure[],
      children: [],
      totals: emptyTotals(),
    });
  });

  const top: TraceNode[] = [];
  for (const node of spans.values()) {
    const owner = spans.get(str(node.step.parent) ?? '');
    (owner ? owner.children : top).push(node);
  }

  steps.forEach((step, index) => {
    if (step.step === 'span') return;
    const at = num(step.at);
    const error = step.step.endsWith('error') ? str(step.error) ?? str(step.findings_error) : undefined;
    const node: TraceNode = {
      key: `step-${index}`,
      kind: 'event',
      label: humanize(step.step),
      name: step.step,
      subtitle: describeStep(step),
      start: at,
      end: at,
      error,
      step,
      calls: [],
      failures: list(step.output_repairs) as ValidationFailure[],
      children: [],
      // Failed validations count once, from the run's audit step; each also sits on the span it happened in.
      totals: { ...emptyTotals(), failures: list(step.output_repairs).length, errors: error ? 1 : 0 },
    };
    const owner = spans.get(str(step.in_span) ?? '')
      // A step recorded just outside a run (its start, or an error after it) is shown with that run.
      ?? top.find((root) => root.kind === 'run' && at >= root.start - 1 && at <= root.end + 1);
    (owner ? owner.children : top).push(node);
  });

  const finish = (node: TraceNode): void => {
    node.children.forEach(finish);
    node.children.sort((a, b) => a.start - b.start || a.end - b.end);
    for (const call of node.calls) {
      node.totals.calls += 1;
      node.totals.inputTokens += num(call.input_tokens) + num(call.cache_read_tokens) + num(call.cache_write_tokens);
      node.totals.outputTokens += num(call.output_tokens);
      node.totals.cost += num(call.cost_usd);
    }
    if (node.kind !== 'event' && node.error) node.totals.errors += 1;
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
    hasSpans: spans.size > 0,
  };
}

export interface TraceRow {
  node: TraceNode;
  depth: number;
  // Whether each ancestor level still has siblings below, for the tree's guide lines.
  guides: boolean[];
  last: boolean;
}

// The rows the tree shows, depth first. Steps are left out unless asked for; a search keeps matching rows and
// their ancestors.
export function visibleRows(
  roots: TraceNode[],
  { collapsed, showSteps, search }: { collapsed: Set<string>; showSteps: boolean; search: string },
): TraceRow[] {
  const query = search.trim().toLowerCase();
  const matches = (node: TraceNode): boolean =>
    !query || `${node.label} ${node.name}`.toLowerCase().includes(query) || node.children.some(matches);
  const rows: TraceRow[] = [];
  const walk = (nodes: TraceNode[], depth: number, guides: boolean[]) => {
    const shown = nodes.filter((node) => (showSteps || node.kind !== 'event') && matches(node));
    shown.forEach((node, index) => {
      const last = index === shown.length - 1;
      rows.push({ node, depth, guides, last });
      if (!collapsed.has(node.key) || query) walk(node.children, depth + 1, [...guides, !last]);
    });
  };
  walk(roots, 0, []);
  return rows;
}

export function hasVisibleChildren(node: TraceNode, showSteps: boolean): boolean {
  return node.children.some((child) => showSteps || child.kind !== 'event');
}

export function rootOf(node: TraceNode, roots: TraceNode[]): TraceNode {
  const contains = (candidate: TraceNode): boolean => candidate === node || candidate.children.some(contains);
  return roots.find(contains) ?? node;
}
