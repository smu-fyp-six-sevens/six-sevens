import { useState, type ReactNode } from 'react';
import { KIND_LABELS, type LlmCall, type TraceNode, type TraceStep, type ValidationFailure } from './buildTrace';
import { KindIcon } from './KindIcon';
import { formatCost, formatSeconds, formatTokens, prettyJson } from './format';

// Fields shown in their own place, so not again under Attributes.
const SHOWN_FIELDS = new Set([
  'step', 'at', 'name', 'id', 'parent', 'in_span', 'duration_s', 'kind', 'run', 'error', 'llm_calls',
  'validation_failures', 'output_repairs', 'input', 'output', 'brief', 'summary', 'tool_calls', 'tool',
]);

type Tab = 'overview' | 'reasoning' | 'calls' | 'validation' | 'attributes';

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

function Overview({ node }: { node: TraceNode }) {
  const step = node.step;
  const toolCalls = Array.isArray(step.tool_calls) ? (step.tool_calls as Array<{ name?: string; args?: unknown }>) : null;
  const hasArgs = (args: unknown) => typeof args === 'object' && args !== null && Object.keys(args).length > 0;
  const empty =
    !node.error && !node.subtitle && typeof step.brief !== 'string' && typeof step.summary !== 'string'
    && typeof step.input !== 'string' && typeof step.output !== 'string' && !toolCalls && step.step !== 'scoring';
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
                  <code>{call.name}</code>
                  {hasArgs(call.args) ? <Code value={call.args} /> : null}
                </li>
              ))}
            </ol>
          )}
        </Block>
      )}
      <ScoredFindings step={step} />
      {empty && <p className="trace-muted">Nothing more was recorded for this step. See Attributes.</p>}
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

export function TraceDetails({ node, root }: { node: TraceNode; root: TraceNode }) {
  const [chosen, setChosen] = useState<Tab>('overview');
  const thinking = node.calls.flatMap((call) => call.thinking ?? []);
  const attributes = Object.fromEntries(Object.entries(node.step).filter(([key]) => !SHOWN_FIELDS.has(key)));
  const tabs: Array<{ id: Tab; label: string; count?: number; show: boolean }> = [
    { id: 'overview', label: 'Overview', show: true },
    { id: 'reasoning', label: 'Reasoning', count: thinking.length, show: thinking.length > 0 },
    { id: 'calls', label: 'Claude calls', count: node.calls.length, show: node.calls.length > 0 },
    { id: 'validation', label: 'Failed validation', count: node.failures.length, show: node.failures.length > 0 },
    { id: 'attributes', label: 'Attributes', show: Object.keys(attributes).length > 0 },
  ];
  const shown = tabs.filter((tab) => tab.show);
  const tab = shown.some((t) => t.id === chosen) ? chosen : 'overview';
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
          {tokens > 0 && <div><dt>Tokens</dt><dd>{formatTokens(node.totals.inputTokens)} in · {formatTokens(node.totals.outputTokens)} out</dd></div>}
          {node.totals.cost > 0 && <div><dt>Cost</dt><dd>{formatCost(node.totals.cost)}</dd></div>}
        </dl>
      </header>
      <div className="trace-tabs" role="tablist" aria-label="Span details">
        {shown.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={`trace-tab${tab === t.id ? ' trace-tab--active' : ''}`}
            onClick={() => setChosen(t.id)}
          >
            {t.label}
            {t.count ? <span className="trace-tab-count">{t.count}</span> : null}
          </button>
        ))}
      </div>
      <div className="trace-tab-panel" role="tabpanel">
        {tab === 'overview' && <Overview node={node} />}
        {tab === 'reasoning' && thinking.map((text, index) => <p key={index} className="trace-reasoning">{text}</p>)}
        {tab === 'calls' && <Calls calls={node.calls} />}
        {tab === 'validation' && <Failures failures={node.failures} />}
        {tab === 'attributes' && <Code value={attributes} />}
      </div>
    </div>
  );
}
