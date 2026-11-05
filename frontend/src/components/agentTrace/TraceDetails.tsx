import { useState, type ReactNode } from 'react';
import { KIND_LABELS, toolLabel, type LlmCall, type TraceNode, type TraceStep, type ValidationFailure } from './buildTrace';
import { KindIcon } from './KindIcon';
import { formatCost, formatSeconds, formatTokens, prettyJson } from './format';

// Fields shown in their own place, so not again under Attributes.
const SHOWN_FIELDS = new Set([
  'step', 'at', 'name', 'id', 'parent', 'in_span', 'duration_s', 'kind', 'run', 'error', 'llm_calls',
  'validation_failures', 'output_repairs', 'input', 'output', 'brief', 'summary', 'tool_calls', 'tool',
]);

type SectionId = 'overview' | 'reasoning' | 'calls' | 'validation' | 'attributes';

function Code({ value }: { value: unknown }) {
  return <pre className="trace-code">{prettyJson(value)}</pre>;
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="trace-block">
      <h4>{title}</h4>
      {children}
    </section>
  );
}

function ScoredFindings({ step }: { step: TraceStep }) {
  const findings = Array.isArray(step.findings) ? (step.findings as Array<Record<string, unknown>>) : [];
  if (step.step !== 'scoring' || !findings.length) return null;
  return (
    <Block title="Scored findings">
      <table className="trace-table">
        <thead>
          <tr><th>Points</th><th>Severity</th><th>Category</th><th>Tool</th><th>Document</th></tr>
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
    </Block>
  );
}

function hasOverview(node: TraceNode): boolean {
  const { step } = node;
  return Boolean(node.error || (node.kind === 'event' && node.subtitle))
    || ['brief', 'input', 'output', 'summary'].some((field) => typeof step[field] === 'string')
    || (node.kind === 'agent' && Array.isArray(step.tool_calls))
    || (step.step === 'scoring' && Array.isArray(step.findings) && step.findings.length > 0);
}

function Overview({ node }: { node: TraceNode }) {
  const step = node.step;
  const toolCalls = Array.isArray(step.tool_calls) ? (step.tool_calls as Array<{ name?: string; args?: unknown }>) : null;
  const hasArgs = (args: unknown) => typeof args === 'object' && args !== null && Object.keys(args).length > 0;
  return (
    <>
      {node.error && <p className="trace-error">{node.error}</p>}
      {node.kind === 'event' && node.subtitle && <p className="trace-lead">{node.subtitle}</p>}
      {typeof step.brief === 'string' && <Block title="Brief"><p>{step.brief}</p></Block>}
      {typeof step.input === 'string' && <Block title="Input"><Code value={step.input} /></Block>}
      {typeof step.output === 'string' && <Block title="Output"><Code value={step.output} /></Block>}
      {typeof step.summary === 'string' && <Block title="Summary"><p>{step.summary}</p></Block>}
      {toolCalls && node.kind === 'agent' && (
        <Block title="Tool calls, in order">
          {toolCalls.length === 0 ? (
            <p className="trace-muted">No tools were called.</p>
          ) : (
            <ol className="trace-tool-calls">
              {toolCalls.map((call, index) => (
                <li key={index}>
                  <span>{toolLabel(String(call.name))}</span>
                  {hasArgs(call.args) ? <Code value={call.args} /> : null}
                </li>
              ))}
            </ol>
          )}
        </Block>
      )}
      <ScoredFindings step={step} />
    </>
  );
}

function Calls({ calls }: { calls: LlmCall[] }) {
  return (
    <table className="trace-table">
      <thead>
        <tr><th>Model</th><th>In</th><th>Cached</th><th>Out</th><th>Cost</th><th>Time</th><th>Stop</th></tr>
      </thead>
      <tbody>
        {calls.map((call, index) => (
          <tr key={index}>
            <td>{call.error ? <span className="trace-error-text">{call.error}</span> : call.model ?? '—'}</td>
            <td>{call.input_tokens ?? '—'}</td>
            <td>{(call.cache_read_tokens ?? 0) + (call.cache_write_tokens ?? 0)}</td>
            <td>{call.output_tokens ?? '—'}</td>
            <td>{call.cost_usd != null ? formatCost(call.cost_usd) : '—'}</td>
            <td>{call.seconds != null ? formatSeconds(call.seconds) : '—'}</td>
            <td>{call.stop_reason ?? '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Failures({ failures }: { failures: ValidationFailure[] }) {
  return (
    <>
      {failures.map((failure, index) => (
        <div key={index} className="trace-failure">
          <div className="trace-failure-head">
            <strong>{failure.site ?? 'a model reply'}</strong>
            <span className={`trace-chip trace-chip--${failure.status === 'failed' ? 'error' : 'warn'}`}>{failure.status}</span>
          </div>
          <dl className="trace-failure-meta">
            {failure.agent && <div><dt>Agent</dt><dd>{failure.agent}</dd></div>}
            {failure.at && (
              <div><dt>At</dt><dd><time dateTime={failure.at}>{new Date(failure.at).toLocaleString('en-SG')}</time></dd></div>
            )}
            {failure.issues?.length ? <div><dt>Issues</dt><dd>{failure.issues.join('; ')}</dd></div> : null}
          </dl>
          <Block title="Raw payload"><Code value={failure.raw ?? ''} /></Block>
          {failure.used && <Block title="What was used"><Code value={failure.used} /></Block>}
        </div>
      ))}
    </>
  );
}

function Section({
  title, count, open, onToggle, children,
}: {
  title: string;
  count?: number;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <section className="trace-section">
      <button type="button" className="trace-section-head" aria-expanded={open} onClick={onToggle}>
        <span className="trace-section-caret" aria-hidden="true">{open ? '▾' : '▸'}</span>
        {title}
        {count ? <span className="trace-count">{count}</span> : null}
      </button>
      {open && <div className="trace-section-body">{children}</div>}
    </section>
  );
}

export function TraceDetails({ node, root }: { node: TraceNode; root: TraceNode }) {
  const [open, setOpen] = useState<Set<SectionId>>(new Set(['overview']));
  const thinking = node.calls.flatMap((call) => call.thinking ?? []);
  const attributes = Object.fromEntries(Object.entries(node.step).filter(([key]) => !SHOWN_FIELDS.has(key)));
  const sections: Array<{ id: SectionId; title: string; count?: number; show: boolean; body: () => ReactNode }> = [
    { id: 'overview', title: 'Overview', show: hasOverview(node), body: () => <Overview node={node} /> },
    {
      id: 'reasoning', title: 'Reasoning', count: thinking.length, show: thinking.length > 0,
      body: () => thinking.map((text, index) => <p key={index} className="trace-reasoning">{text}</p>),
    },
    { id: 'calls', title: 'Claude calls', count: node.calls.length, show: node.calls.length > 0, body: () => <Calls calls={node.calls} /> },
    {
      id: 'validation', title: 'Failed validation', count: node.failures.length, show: node.failures.length > 0,
      body: () => <Failures failures={node.failures} />,
    },
    { id: 'attributes', title: 'Attributes', show: Object.keys(attributes).length > 0, body: () => <Code value={attributes} /> },
  ];
  const shown = sections.filter((section) => section.show);
  const toggle = (id: SectionId) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const tokens = node.totals.inputTokens + node.totals.outputTokens;

  return (
    <div className="trace-details">
      <header className="trace-details-head">
        <div className="trace-details-title">
          <KindIcon kind={node.kind} />
          <span className="trace-kind-name">{KIND_LABELS[node.kind]}</span>
          <h3>{node.label}</h3>
        </div>
        <code className="trace-details-name">{node.name}</code>
        <dl className="trace-stats trace-stats--small">
          <div><dt>Status</dt><dd className={node.error ? 'trace-error-text' : 'trace-ok-text'}>{node.error ? '✕ Error' : '✓ OK'}</dd></div>
          <div><dt>Starts</dt><dd>+{formatSeconds(Math.max(node.start - root.start, 0))}</dd></div>
          {node.kind !== 'event' && <div><dt>Took</dt><dd>{formatSeconds(node.end - node.start)}</dd></div>}
          {node.elapsed !== undefined && <div><dt>Since the step before</dt><dd>{formatSeconds(node.elapsed)}</dd></div>}
          {tokens > 0 && <div><dt>Tokens</dt><dd>{formatTokens(node.totals.inputTokens)} in · {formatTokens(node.totals.outputTokens)} out</dd></div>}
          {node.totals.cost > 0 && <div><dt>Cost</dt><dd>{formatCost(node.totals.cost)}</dd></div>}
        </dl>
      </header>
      {shown.length === 0 ? (
        <p className="trace-muted trace-empty">Nothing more was recorded for this step.</p>
      ) : (
        <div className="trace-sections">
          {shown.map((section) => (
            <Section
              key={section.id}
              title={section.title}
              count={section.count}
              open={open.has(section.id)}
              onToggle={() => toggle(section.id)}
            >
              {section.body()}
            </Section>
          ))}
        </div>
      )}
    </div>
  );
}
