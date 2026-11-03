import { useMemo, useState, type ReactNode } from 'react';
import { RefreshButton } from '../RefreshButton';
import {
  buildTrace,
  flatten,
  type LlmCall,
  type TraceNode,
  type TraceNodeKind,
  type TraceStep,
  type TraceTotals,
  type ValidationFailure,
} from './buildTrace';

// A run's trace as a tree of spans beside the selected span's details: each agent, model call and tool call with
// its timing, tokens, cost, reasoning and any reply that failed validation.

const KIND_LABELS: Record<TraceNodeKind, string> = {
  run: 'Run',
  agent: 'Agent',
  llm: 'LLM',
  tool: 'Tool',
  chain: 'Step',
  call: 'Call',
  event: 'Event',
};

// Fields shown in their own sections, so not again under Attributes.
const SHOWN_FIELDS = new Set([
  'step', 'at', 'name', 'id', 'parent', 'in_span', 'duration_s', 'kind', 'run', 'error', 'llm_calls',
  'validation_failures', 'output_repairs', 'input', 'output', 'brief', 'summary', 'tool_calls', 'tool', 'findings',
  'unable_to_assess', 'nothing_to_check', 'reason',
]);

function formatSeconds(seconds: number): string {
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 2 : 1)} s`;
  return `${Math.floor(seconds / 60)} m ${Math.round(seconds % 60)} s`;
}

const formatCost = (usd: number) => `$${usd.toFixed(usd < 0.01 ? 4 : 3)}`;
const formatTokens = (tokens: number) => (tokens >= 1000 ? `${(tokens / 1000).toFixed(1)}k` : String(tokens));

function Totals({ totals, duration }: { totals: TraceTotals; duration: number }) {
  return (
    <dl className="agent-trace-totals">
      <div><dt>Duration</dt><dd>{formatSeconds(duration)}</dd></div>
      <div><dt>Claude calls</dt><dd>{totals.calls}</dd></div>
      <div><dt>Tokens in / out</dt><dd>{formatTokens(totals.inputTokens)} / {formatTokens(totals.outputTokens)}</dd></div>
      <div><dt>Cost</dt><dd>{formatCost(totals.cost)}</dd></div>
      <div>
        <dt>Failed validations</dt>
        <dd className={totals.failures ? 'agent-trace-warn' : undefined}>{totals.failures}</dd>
      </div>
    </dl>
  );
}

function rootOf(node: TraceNode, roots: TraceNode[]): TraceNode {
  const contains = (candidate: TraceNode): boolean =>
    candidate === node || candidate.children.some(contains);
  return roots.find(contains) ?? node;
}

function TimelineBar({ node, root }: { node: TraceNode; root: TraceNode }) {
  const span = Math.max(root.end - root.start, 0.001);
  const left = ((node.start - root.start) / span) * 100;
  const width = ((node.end - node.start) / span) * 100;
  if (node.kind === 'event') {
    return (
      <span className="agent-trace-timeline" aria-hidden="true">
        <span className="agent-trace-timeline-dot" style={{ left: `${Math.min(Math.max(left, 0), 100)}%` }} />
      </span>
    );
  }
  return (
    <span className="agent-trace-timeline" aria-hidden="true">
      <span
        className={`agent-trace-timeline-bar agent-trace-kind--${node.kind}`}
        style={{ left: `${Math.max(left, 0)}%`, width: `${Math.max(width, 0.6)}%` }}
      />
    </span>
  );
}

function Json({ value }: { value: unknown }) {
  const text = typeof value === 'string' ? tryPretty(value) : JSON.stringify(value, null, 2);
  return <pre className="agent-trace-pre">{text}</pre>;
}

function tryPretty(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="agent-trace-section">
      <h4>{title}</h4>
      {children}
    </section>
  );
}

function Reasoning({ thinking }: { thinking: string[] }) {
  if (!thinking.length) return null;
  return (
    <Section title="Reasoning">
      {thinking.map((text, index) => (
        <p key={index} className="agent-trace-reasoning">{text}</p>
      ))}
    </Section>
  );
}

function CallTable({ calls }: { calls: LlmCall[] }) {
  if (!calls.length) return null;
  return (
    <Section title={`Claude calls (${calls.length})`}>
      <table className="agent-trace-table">
        <thead>
          <tr>
            <th>Model</th>
            <th>In</th>
            <th>Cache read / write</th>
            <th>Out</th>
            <th>Cost</th>
            <th>Time</th>
            <th>Stop</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((call, index) => (
            <tr key={index}>
              <td>{call.error ? <span className="agent-trace-error-text">{call.error}</span> : call.model ?? '—'}</td>
              <td>{call.input_tokens ?? '—'}</td>
              <td>{call.cache_read_tokens ?? 0} / {call.cache_write_tokens ?? 0}</td>
              <td>{call.output_tokens ?? '—'}</td>
              <td>{call.cost_usd != null ? formatCost(call.cost_usd) : '—'}</td>
              <td>{call.seconds != null ? formatSeconds(call.seconds) : '—'}</td>
              <td>{call.stop_reason ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

function Failures({ failures }: { failures: ValidationFailure[] }) {
  if (!failures.length) return null;
  return (
    <Section title={`Failed validation (${failures.length})`}>
      {failures.map((failure, index) => (
        <div key={index} className="agent-trace-failure">
          <div className="agent-trace-failure-head">
            <strong>{failure.site ?? 'a model reply'}</strong>
            <span className={`agent-trace-pill agent-trace-pill--${failure.status === 'failed' ? 'error' : 'warn'}`}>{failure.status}</span>
            {failure.agent && <span>agent {failure.agent}</span>}
            {failure.at && <time dateTime={failure.at}>{new Date(failure.at).toLocaleString('en-SG')}</time>}
          </div>
          {failure.issues?.length ? <p>{failure.issues.join('; ')}</p> : null}
          <details>
            <summary>Raw payload</summary>
            <Json value={failure.raw ?? ''} />
          </details>
          {failure.used && (
            <details>
              <summary>What was used</summary>
              <Json value={failure.used} />
            </details>
          )}
        </div>
      ))}
    </Section>
  );
}

function ToolCalls({ calls }: { calls: Array<{ name?: string; args?: unknown }> }) {
  return (
    <Section title="Tool calls, in order">
      {calls.length === 0 ? (
        <p className="agent-trace-muted">No tools were called.</p>
      ) : (
        <ol className="agent-trace-tool-calls">
          {calls.map((call, index) => (
            <li key={index}>
              <code>{call.name}</code>
              {typeof call.args === 'object' && call.args !== null && Object.keys(call.args).length > 0 ? (
                <Json value={call.args} />
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </Section>
  );
}

function ScoringTable({ step }: { step: TraceStep }) {
  const findings = Array.isArray(step.findings) ? (step.findings as Array<Record<string, unknown>>) : [];
  if (!findings.length) return null;
  return (
    <Section title="Scored findings">
      <table className="agent-trace-table">
        <thead>
          <tr>
            <th>Points</th>
            <th>Severity</th>
            <th>Category</th>
            <th>Tool</th>
            <th>Document</th>
          </tr>
        </thead>
        <tbody>
          {findings.map((finding, index) => (
            <tr key={String(finding.finding_id ?? index)}>
              <td>{String(finding.points)}</td>
              <td>{String(finding.severity)}</td>
              <td>{String(finding.category ?? '—')}</td>
              <td>{String(finding.tool ?? '—')}</td>
              <td>{String(finding.document)}{finding.source === 'cross_document' ? ' (case)' : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

function Attributes({ step }: { step: TraceStep }) {
  const rest = Object.fromEntries(Object.entries(step).filter(([key]) => !SHOWN_FIELDS.has(key)));
  if (!Object.keys(rest).length) return null;
  return (
    <Section title="Attributes">
      <Json value={rest} />
    </Section>
  );
}

function Details({ node, root }: { node: TraceNode; root: TraceNode }) {
  const step = node.step;
  const calls = node.kind === 'call' ? [] : ((step.llm_calls as LlmCall[] | undefined) ?? []);
  const failures = [
    ...((step.validation_failures as ValidationFailure[] | undefined) ?? []),
    ...((step.output_repairs as ValidationFailure[] | undefined) ?? []),
  ];
  const thinking = node.call ? node.call.thinking ?? [] : calls.flatMap((call) => call.thinking ?? []);
  return (
    <div className="agent-trace-details" aria-live="polite">
      <header>
        <span className={`agent-trace-kind agent-trace-kind--${node.kind}`}>{KIND_LABELS[node.kind]}</span>
        <h3>{node.label}</h3>
        <code className="agent-trace-name">{node.name}</code>
        <dl className="agent-trace-meta">
          <div><dt>Starts</dt><dd>+{formatSeconds(Math.max(node.start - root.start, 0))}</dd></div>
          {node.kind !== 'event' && <div><dt>Took</dt><dd>{formatSeconds(node.end - node.start)}</dd></div>}
          {node.totals.calls > 0 && (
            <div><dt>Tokens in / out</dt><dd>{node.totals.inputTokens} / {node.totals.outputTokens}</dd></div>
          )}
          {node.totals.cost > 0 && <div><dt>Cost</dt><dd>{formatCost(node.totals.cost)}</dd></div>}
          <div><dt>Status</dt><dd className={node.error ? 'agent-trace-error-text' : undefined}>{node.error ? 'Error' : 'OK'}</dd></div>
        </dl>
      </header>

      {node.error && <p className="agent-trace-error">{node.error}</p>}
      {node.subtitle && node.kind === 'event' && <p className="agent-trace-summary">{node.subtitle}</p>}

      {node.call && <CallTable calls={[node.call]} />}
      {typeof step.brief === 'string' && node.kind === 'agent' && (
        <Section title="Brief"><p>{step.brief}</p></Section>
      )}
      {typeof step.input === 'string' && <Section title="Input"><Json value={step.input} /></Section>}
      {typeof step.output === 'string' && <Section title="Output"><Json value={step.output} /></Section>}
      {typeof step.summary === 'string' && node.kind !== 'event' && (
        <Section title="Summary"><p>{step.summary}</p></Section>
      )}
      {Array.isArray(step.tool_calls) && node.kind === 'agent' && (
        <ToolCalls calls={step.tool_calls as Array<{ name?: string; args?: unknown }>} />
      )}
      <Reasoning thinking={thinking} />
      <CallTable calls={calls} />
      <Failures failures={failures} />
      {step.step === 'scoring' && <ScoringTable step={step} />}
      {node.kind !== 'call' && <Attributes step={step} />}
    </div>
  );
}

export function AgentTraceView({
  title,
  steps,
  emptyText,
  isRefreshing,
  onRefresh,
}: {
  title: string;
  steps: TraceStep[] | null;
  emptyText: string;
  isRefreshing: boolean;
  onRefresh: () => void | Promise<void>;
}) {
  const trace = useMemo(() => buildTrace(steps ?? []), [steps]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const rows = flatten(trace.roots, collapsed);
  const selected = rows.find((row) => row.node.key === selectedKey)?.node ?? trace.roots[trace.roots.length - 1] ?? null;

  const toggle = (key: string) =>
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <section className="details-card agent-trace" aria-label={title}>
      <RefreshButton isRefreshing={isRefreshing} onClick={() => void onRefresh()} label={title.toLowerCase()} />
      <h2>{title}</h2>
      {trace.roots.length === 0 ? (
        <p className="agent-trace-muted">{emptyText}</p>
      ) : (
        <>
          <Totals totals={trace.totals} duration={trace.end - trace.start} />
          <div className="agent-trace-body">
            <div className="agent-trace-tree" role="tree" aria-label={`${title} spans`}>
              {rows.map(({ node, depth }) => {
                const isSelected = selected?.key === node.key;
                const hasChildren = node.children.length > 0;
                return (
                  <div
                    key={node.key}
                    role="treeitem"
                    aria-level={depth + 1}
                    aria-selected={isSelected}
                    aria-expanded={hasChildren ? !collapsed.has(node.key) : undefined}
                    className={`agent-trace-row${isSelected ? ' agent-trace-row--selected' : ''}${node.error ? ' agent-trace-row--error' : ''}`}
                    style={{ paddingLeft: `${8 + depth * 16}px` }}
                  >
                    {hasChildren ? (
                      <button
                        type="button"
                        className="agent-trace-caret"
                        aria-label={`${collapsed.has(node.key) ? 'Expand' : 'Collapse'} ${node.label}`}
                        onClick={() => toggle(node.key)}
                      >
                        {collapsed.has(node.key) ? '▸' : '▾'}
                      </button>
                    ) : (
                      <span className="agent-trace-caret" aria-hidden="true" />
                    )}
                    <button type="button" className="agent-trace-select" onClick={() => setSelectedKey(node.key)}>
                      <span className={`agent-trace-kind agent-trace-kind--${node.kind}`}>{KIND_LABELS[node.kind]}</span>
                      <span className="agent-trace-label">
                        {node.label}
                        {node.subtitle && <span className="agent-trace-subtitle">{node.subtitle}</span>}
                      </span>
                      <span className="agent-trace-figures">
                        {node.kind !== 'event' && formatSeconds(node.end - node.start)}
                        {node.totals.calls > 0 && ` · ${formatTokens(node.totals.inputTokens + node.totals.outputTokens)} tok`}
                      </span>
                      <TimelineBar node={node} root={rootOf(node, trace.roots)} />
                    </button>
                  </div>
                );
              })}
            </div>
            {selected && <Details node={selected} root={rootOf(selected, trace.roots)} />}
          </div>
        </>
      )}
    </section>
  );
}
