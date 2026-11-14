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
  llm: 'Claude call',
  tool: 'Tool',
  chain: '',
  event: '',
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

// One sensibility check on an agent's run (backend evals/): did it take the expected path, is its answer sensible.
export interface EvalResult {
  agent: string;
  check: string;
  kind: 'output' | 'trajectory';
  result: 'pass' | 'warn' | 'fail' | 'n/a';
  reason?: string;
  span?: string;
}

export interface EvalTotals {
  passed: number;
  warnings: number;
  failures: number;
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
  // A step's time since the step before it in the same span; unset when it starts a run, or follows a wait.
  elapsed?: number;
  // A heading over a run's steps (Classifier, Extractor, ...), not something recorded.
  group?: boolean;
  // A group of steps' time worked, where first to last could include a wait.
  seconds?: number;
  // How sure the agent was: the classifier's confidence in the type, or the extractor's in the fields.
  confidence?: number;
  error?: string;
  step: TraceStep;
  calls: LlmCall[];
  failures: ValidationFailure[];
  children: TraceNode[];
  totals: TraceTotals;
  // The sensibility checks on this agent's work (or, on a run, on work no row of its own stands for).
  evals?: EvalResult[];
}

export interface BuiltTrace {
  roots: TraceNode[];
  totals: TraceTotals;
  evals: EvalTotals;
  // Time spent working: a document's runs can be hours apart, waiting for an officer.
  seconds: number;
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
  'classify.agent_call': 'Deciding the type',
  'extract.run_extraction': 'Reading the fields',
  'extraction.second_look_agent': 'Second look: choosing what to recheck',
  'extraction.vision_read': 'Second look: reading the page images',
  'detection.lead_plan': 'Plan',
  'detection.lead_review': 'Review',
  'detection.numerical_binder': 'Matching figures to checks',
  'case_pipeline.hypothesise': 'Proposing hypotheses',
  'case_pipeline.adjudicate': 'Judging hypotheses',
  'case_pipeline.evidence_requests': 'Drafting evidence requests',
  'case_pipeline.summary': 'Writing the summary',
};

const SPECIALIST_LABELS: Record<string, string> = {
  layout_rendering: 'Layout & Rendering',
  content_validation: 'Content Validation',
  digital_authenticity: 'Digital Authenticity',
};

const STEP_LABELS: Record<string, string> = {
  run_start: 'Run started',
  ingest: 'Document read',
  content_guard: 'Content safety check',
  rule_classify: 'Rule-based type check',
  agent_call: 'Type decision',
  llm_classify_tool: 'Claude read the text',
  vision_llm: 'Claude read the page images',
  validate: 'Classification checked',
  classify_tool_refused: 'Tool call refused',
  reclassify: 'Type checked again',
  record_escalation: 'Sent for officer review',
  classification_finalised: 'Document type decided',
  extraction: 'Fields extracted',
  extraction_layout_lookup: 'Page layout read',
  extraction_vision_read: 'Page images read',
  extraction_second_look: 'Second look finished',
  extraction_tool_refused: 'Second look: tool call refused',
  detection_start: 'Detection started',
  detection_plan: 'Investigation plan',
  detection_review: 'Reports read',
  detection_second_look: 'Second look requested',
  detection_specialist: 'Specialist finished',
  detection_done: 'Detection finished',
  detection_error: 'Detection failed',
  findings_error: 'Findings not saved',
  dossier: 'Case file prepared',
  deterministic: 'Deterministic comparisons',
  hypotheses: 'Hypotheses proposed',
  tool_call: 'Evidence gathered',
  judge_reply: 'Judge decision',
  verify: 'Verdict checked',
  scoring: 'Risk score',
  evidence_requests: 'Evidence requests',
  outlook: 'Risk outlook',
  additional_documents: 'Additional documents',
  summary: 'Case summary',
  run_audit: 'Run audit',
  done: 'Finished',
};

const TOOL_LABELS: Record<string, string> = {
  llm_classify_tool: 'Text classification',
  vision_llm: 'Page image reading',
  layout_lookup: 'Layout lookup',
  vision_read: 'Visual read',
  numerical_checks_tool: 'Numerical checks',
  external_validation_tool: 'Public record checks',
  metadata_tool: 'File metadata check',
};

export function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? `${humanize(name.replace(/_tool$/, ''))} check`;
}

const specialist = (name: string) => SPECIALIST_LABELS[name] ?? humanize(name);

function spanLabel(step: TraceStep, name: string): string {
  if (SPAN_LABELS[name]) return SPAN_LABELS[name];
  if (name.startsWith('tool.')) return toolLabel(name.slice(5));
  if (name.startsWith('detection.review.')) return `${specialist(name.slice(17))} specialist`;
  if (name.startsWith('detection.specialist.')) return specialist(name.slice(21));
  return str(step.run) ?? humanize(name.replace(/^case_pipeline\.|^cross_document\./, ''));
}

function spanKind(step: TraceStep, name: string): TraceNodeKind {
  const declared = str(step.kind);
  if (declared === 'run' || declared === 'agent' || declared === 'tool') return declared;
  if (name.startsWith('tool.')) return 'tool';
  return list(step.llm_calls).length > 0 ? 'llm' : 'chain';
}

export interface SecondLookField {
  name: string;
  why: string[];
}

export interface SecondLookDisagreement {
  field: string;
  textValue: unknown;
  secondValue: unknown;
  readBy: string;
  pages: number[];
}

export interface SecondLook {
  fields: SecondLookField[];
  tools: string[];
  disagreements: SecondLookDisagreement[];
  rejected: Array<{ field: string; reason: string }>;
  error?: string;
}

const TRIGGER_LABELS: Record<string, string> = {
  low_confidence: 'low confidence',
  confidence_missing: 'no confidence given',
  missing_required: 'required but missing',
  guard_dropped: 'number not printed in the text',
};

// What an extraction step's second look reviewed and found; undefined when it had none.
export function secondLookOf(step: TraceStep): SecondLook | undefined {
  if (step.step !== 'extraction' || typeof step.second_look !== 'object' || step.second_look === null) return undefined;
  const review = step.second_look as { fields?: unknown; tools_used?: unknown };
  return {
    fields: (list(review.fields) as Array<{ name?: unknown; triggers?: unknown }>).map((item) => ({
      name: String(item.name),
      why: list(item.triggers).map((trigger) => TRIGGER_LABELS[String(trigger)] ?? humanize(String(trigger))),
    })),
    tools: list(review.tools_used).map(String),
    disagreements: (list(step.extraction_disagreements) as Array<Record<string, unknown>>).map((item) => ({
      field: String(item.field),
      textValue: item.text_value,
      secondValue: item.second_look_value,
      readBy: item.source === 'layout_lookup' ? 'page layout' : 'page image',
      pages: list(item.pages).filter((page): page is number => typeof page === 'number'),
    })),
    rejected: (list(step.second_look_rejected_fields) as Array<{ field?: unknown; reason?: unknown }>).map((item) => ({
      field: String(item.field),
      reason: String(item.reason ?? ''),
    })),
    error: str(step.second_look_error),
  };
}

function describeExtraction(step: TraceStep): string | undefined {
  const review = secondLookOf(step);
  if (!review) return str(step.document_type);
  const found = review.error ? 'failed, text pass kept' : `${review.disagreements.length} disagreement(s)`;
  return `${String(step.document_type)} · second look on ${review.fields.length} field(s) · ${found}`;
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
      const calls = list(step.tool_calls).map((call) => toolLabel(String(call)));
      return calls.length ? `uses ${calls.join(', ')}` : 'decided without using a tool';
    }
    case 'classification_finalised':
      return `${String(step.document_type ?? 'no type')} · confidence ${String(step.confidence ?? '—')}`;
    case 'extraction':
      return describeExtraction(step);
    case 'extraction_layout_lookup':
    case 'extraction_vision_read':
      return `${list(step.fields).length} field(s) · page(s) ${step.step === 'extraction_vision_read' ? list(step.pages).join(', ') || '—' : String(step.pages ?? '—')}`;
    case 'extraction_second_look':
      return `${list(step.fields).length} field(s) reviewed in ${num(step.turn_count)} turn(s)`;
    case 'classify_tool_refused':
    case 'extraction_tool_refused':
      return list(step.tools).map((tool) => toolLabel(String(tool))).join(', ');
    case 'detection_review':
      if (!step.second_look_requested) return 'no second look asked for';
      return step.second_look_accepted ? 'second look asked for' : 'second look asked for, not run';
    case 'detection_plan':
      return `briefs for ${Object.keys((step.detection_briefs as object | undefined) ?? {}).length} specialists`;
    case 'detection_specialist': {
      const tools = list(step.tools_called).map((tool) => toolLabel(String(tool)));
      return `${num(step.finding_count)} finding(s) · tools: ${tools.length ? tools.join(', ') : 'none'}`;
    }
    case 'detection_done':
      return `verdict ${String(step.detection_verdict)}`;
    case 'hypotheses':
      return `${list(step.proposed).length} hypothesis(es) proposed`;
    case 'tool_call':
      return `${toolLabel(String(step.tool))} for ${String(step.hypothesis)}`;
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

const START_STEPS = new Set(['run_start', 'ingest', 'detection_start']);
// Longer than any one step takes, so a gap this long is a wait between runs (an on-demand check, say).
const MAX_STEP_SECONDS = 600;

function elapsedSince(previous: number | undefined, node: TraceNode, inSpan: boolean): number | undefined {
  // Inside a span, a run's first step is timed from the span's start; outside one, it follows a wait.
  if (previous === undefined || (!inSpan && START_STEPS.has(node.name))) return undefined;
  const seconds = node.start - previous;
  return seconds >= 0 && seconds <= MAX_STEP_SECONDS ? seconds : undefined;
}

// Each step against what came before it among its siblings, so a specialist running alongside doesn't count.
function assignElapsed(siblings: TraceNode[], from?: number): void {
  let previous = from;
  for (const node of siblings) {
    if (node.kind === 'event') node.elapsed = elapsedSince(previous, node, from !== undefined);
    previous = Math.max(previous ?? node.end, node.end);
  }
}

const GROUP_MEMBERS: Record<string, string[]> = {
  'Document intake': ['ingest', 'content_guard'],
  Classifier: [
    'rule_classify', 'classify.agent_call', 'agent_call', 'llm_classify_tool', 'vision_llm', 'tool.llm_classify_tool',
    'tool.vision_llm', 'validate', 'classify_tool_refused', 'reclassify', 'record_escalation', 'classification_finalised',
  ],
  Extractor: ['extract.run_extraction', 'extraction', 'extraction_tool_refused'],
  'Lead agent': ['detection.lead_plan', 'detection_plan', 'detection_review', 'detection_second_look', 'detection.lead_review'],
  Specialists: ['detection_specialist'],
  'Case file': ['dossier', 'deterministic'],
  'Hypothesis planner': ['case_pipeline.hypothesise', 'hypotheses'],
  Evidence: ['tool_call'],
  'Hypothesis judge': ['case_pipeline.adjudicate', 'judge_reply', 'verify'],
  'Risk and outlook': ['scoring', 'case_pipeline.evidence_requests', 'evidence_requests', 'outlook', 'additional_documents'],
  'Summary writer': ['case_pipeline.summary', 'summary'],
};
const GROUP_OF = new Map(Object.entries(GROUP_MEMBERS).flatMap(([group, names]) => names.map((name) => [name, group] as const)));

const groupOf = (name: string) => (name.startsWith('detection.review.') ? 'Specialists' : GROUP_OF.get(name));

// The confidence a step reports, for the two steps that decide something (the type, the fields).
function confidenceOf(step: TraceStep): number | undefined {
  const reported = step.step === 'classification_finalised' || step.step === 'extraction' ? step.confidence : undefined;
  return typeof reported === 'number' && Number.isFinite(reported) ? reported : undefined;
}

function newestConfidence(nodes: TraceNode[]): number | undefined {
  for (const node of [...nodes].reverse()) {
    const found = node.confidence ?? newestConfidence(node.children);
    if (found !== undefined) return found;
  }
  return undefined;
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

// A run's children under their headings; what belongs to none (its start, its audit, an error) stays beside them.
function withGroups(parentKey: string, nodes: TraceNode[]): TraceNode[] {
  const groups = new Map<string, TraceNode>();
  const rest: TraceNode[] = [];
  for (const node of nodes) {
    const label = groupOf(node.name);
    if (!label) {
      rest.push(node);
      continue;
    }
    let group = groups.get(label);
    if (!group) {
      group = {
        key: `${parentKey}/${label}`, kind: 'chain', group: true, label, name: label, start: node.start, end: node.end,
        step: { step: 'group', at: node.start }, calls: [], failures: [], children: [], totals: emptyTotals(),
      };
      groups.set(label, group);
    }
    group.children.push(node);
    group.start = Math.min(group.start, node.start);
    group.end = Math.max(group.end, node.end);
    addTotals(group.totals, node.totals);
  }
  for (const group of groups.values()) {
    // The newest one: a reclassification decides again.
    group.confidence = newestConfidence(group.children);
    if (group.children.every((child) => child.kind === 'event')) {
      group.seconds = group.children.reduce((sum, child) => sum + (child.elapsed ?? 0), 0);
    }
  }
  return [...rest, ...groups.values()].sort((a, c) => a.start - c.start || a.end - c.end);
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

  const evalSteps: TraceStep[] = [];
  steps.forEach((step, index) => {
    if (step.step === 'span') return;
    if (step.step === 'evals') {
      evalSteps.push(step);
      return;
    }
    const at = num(step.at);
    const error = step.step.endsWith('error') ? str(step.error) ?? str(step.findings_error) : undefined;
    const node: TraceNode = {
      key: `step-${index}`,
      kind: 'event',
      label: STEP_LABELS[step.step] ?? humanize(step.step),
      name: step.step,
      subtitle: describeStep(step),
      start: at,
      end: at,
      confidence: confidenceOf(step),
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
    assignElapsed(node.children, node.start);
    if (node.kind === 'run') node.children = withGroups(node.key, node.children);
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
  assignElapsed(top);
  const seconds = top.reduce((sum, node) => sum + (node.kind === 'event' ? node.elapsed ?? 0 : node.end - node.start), 0);
  const roots = withGroups('top', top);

  const totals = emptyTotals();
  roots.forEach((node) => addTotals(totals, node.totals));
  return {
    roots,
    totals,
    evals: attachEvals(roots, evalSteps),
    seconds,
    hasSpans: spans.size > 0,
  };
}

// The trace heading each agent's checks belong under; an agent with a row of its own (a specialist) names it by span.
const EVAL_GROUPS: Record<string, string> = {
  classifier: 'Classifier',
  extractor: 'Extractor',
  lead: 'Lead agent',
  hypothesis_planner: 'Hypothesis planner',
  hypothesis_judge: 'Hypothesis judge',
  summary_writer: 'Summary writer',
  evidence_requests: 'Risk and outlook',
};

function findNode(nodes: TraceNode[], match: (node: TraceNode) => boolean): TraceNode | undefined {
  for (const node of nodes) {
    if (match(node)) return node;
    const found = findNode(node.children, match);
    if (found) return found;
  }
  return undefined;
}

// Puts each run's check results on the row of the agent they mark, and counts them for the header.
function attachEvals(roots: TraceNode[], evalSteps: TraceStep[]): EvalTotals {
  const counts: EvalTotals = { passed: 0, warnings: 0, failures: 0 };
  for (const step of evalSteps) {
    counts.passed += num(step.passed);
    counts.warnings += num(step.warnings);
    counts.failures += num(step.failures);
    const run = findNode(roots, (node) => node.key === str(step.in_span)) ?? roots.find((node) => node.kind === 'run');
    for (const result of list(step.results) as EvalResult[]) {
      if (result.result === 'n/a') continue;
      const group = EVAL_GROUPS[result.agent] ?? (result.agent.startsWith('specialist:') ? 'Specialists' : undefined);
      const target = (result.span && run && findNode([run], (node) => node.key === result.span))
        ?? (group && run && findNode([run], (node) => node.group === true && node.label === group))
        ?? run;
      if (target) target.evals = [...(target.evals ?? []), result];
    }
  }
  return counts;
}

export function evalSummary(results: EvalResult[]): { tone: 'ok' | 'warn' | 'error'; text: string } {
  const failures = results.filter((r) => r.result === 'fail').length;
  const warnings = results.filter((r) => r.result === 'warn').length;
  if (failures) return { tone: 'error', text: `✕ ${failures} check${failures === 1 ? '' : 's'}` };
  if (warnings) return { tone: 'warn', text: `⚠ ${warnings} check${warnings === 1 ? '' : 's'}` };
  return { tone: 'ok', text: `✓ ${results.length} check${results.length === 1 ? '' : 's'}` };
}

export interface TraceRow {
  node: TraceNode;
  depth: number;
  // Whether each ancestor level still has siblings below, for the tree's guide lines.
  guides: boolean[];
  last: boolean;
}

// The rows the tree shows, depth first. A search keeps matching rows and their ancestors.
export function visibleRows(
  roots: TraceNode[],
  { collapsed, search }: { collapsed: Set<string>; search: string },
): TraceRow[] {
  const query = search.trim().toLowerCase();
  const matches = (node: TraceNode): boolean =>
    !query || `${node.label} ${node.name}`.toLowerCase().includes(query) || node.children.some(matches);
  const rows: TraceRow[] = [];
  const walk = (nodes: TraceNode[], depth: number, guides: boolean[]) => {
    const shown = nodes.filter(matches);
    shown.forEach((node, index) => {
      const last = index === shown.length - 1;
      rows.push({ node, depth, guides, last });
      if (!collapsed.has(node.key) || query) walk(node.children, depth + 1, [...guides, !last]);
    });
  };
  walk(roots, 0, []);
  return rows;
}

export function groupKeys(nodes: TraceNode[]): string[] {
  return nodes.flatMap((node) => [...(node.group ? [node.key] : []), ...groupKeys(node.children)]);
}

export function rootOf(node: TraceNode, roots: TraceNode[]): TraceNode {
  const contains = (candidate: TraceNode): boolean => candidate === node || candidate.children.some(contains);
  return roots.find(contains) ?? node;
}
