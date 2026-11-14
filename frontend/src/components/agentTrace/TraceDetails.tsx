import { useState, type ReactNode } from 'react';
import {
  KIND_LABELS, humanize, secondLookOf, toolLabel, type EvalResult, type LlmCall, type TraceNode, type TraceStep, type ValidationFailure,
} from './buildTrace';
import { Chevron } from './Chevron';
import { KindIcon } from './KindIcon';
import { formatCost, formatSeconds, formatTokens, prettyJson } from './format';

// Fields shown in their own place, so not again under Attributes.
const SHOWN_FIELDS = new Set([
  'step', 'at', 'name', 'id', 'parent', 'in_span', 'duration_s', 'kind', 'run', 'error', 'llm_calls',
  'validation_failures', 'output_repairs', 'input', 'output', 'brief', 'summary', 'tool_calls', 'tool',
]);

type SectionId = 'overview' | 'checks' | 'reasoning' | 'calls' | 'validation' | 'attributes';

const RESULT_TONES: Record<EvalResult['result'], string> = { pass: 'ok', warn: 'warn', fail: 'error', 'n/a': '' };
const RESULT_LABELS: Record<EvalResult['result'], string> = { pass: 'passed', warn: 'warning', fail: 'failed', 'n/a': 'n/a' };
const RESULT_ORDER: Record<EvalResult['result'], number> = { fail: 0, warn: 1, pass: 2, 'n/a': 3 };

// The sensibility checks on this agent's run: problems first, each with why.
function Checks({ results }: { results: EvalResult[] }) {
  const sorted = [...results].sort((a, b) => RESULT_ORDER[a.result] - RESULT_ORDER[b.result]);
  return (
    <>
      <p className="trace-muted">
        Code checks of whether the agent took the expected path and gave a sensible answer. They don't measure accuracy.
      </p>
      <table className="trace-table">
        <thead><tr><th>Result</th><th>Check</th><th>Kind</th><th>Why</th></tr></thead>
        <tbody>
          {sorted.map((result, index) => (
            <tr key={`${result.agent}-${result.check}-${index}`}>
              <td><span className={`trace-chip trace-chip--${RESULT_TONES[result.result]}`}>{RESULT_LABELS[result.result]}</span></td>
              <td>{humanize(result.check)}</td>
              <td>{result.kind === 'trajectory' ? 'Path' : 'Answer'}</td>
              <td>{result.reason ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

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

const cellText = (value: unknown) => (typeof value === 'string' ? value : JSON.stringify(value));

// What the extraction second look reviewed and why, the tools it ran, and where it disagreed with the text.
function SecondLookDetails({ step }: { step: TraceStep }) {
  const review = secondLookOf(step);
  if (!review) return null;
  return (
    <>
      <Block title="Second look: fields reviewed">
        <table className="trace-table">
          <thead><tr><th>Field</th><th>Why</th></tr></thead>
          <tbody>
            {review.fields.map((field) => (
              <tr key={field.name}><td>{humanize(field.name)}</td><td>{field.why.join(', ')}</td></tr>
            ))}
          </tbody>
        </table>
      </Block>
      <Block title="Second look: tools run">
        {review.tools.length ? <p>{review.tools.map(toolLabel).join(', ')}</p> : <p className="trace-muted">No tools were run.</p>}
      </Block>
      {review.error && <Block title="Second look: failed"><p>{review.error}. The text pass was kept.</p></Block>}
      {review.disagreements.length > 0 && (
        <Block title="Second look: disagreements (the officer decides)">
          <table className="trace-table">
            <thead><tr><th>Field</th><th>Text reads</th><th>Second look reads</th><th>Read from</th><th>Pages</th></tr></thead>
            <tbody>
              {review.disagreements.map((item) => (
                <tr key={item.field}>
                  <td>{humanize(item.field)}</td>
                  <td>{cellText(item.textValue)}</td>
                  <td>{cellText(item.secondValue)}</td>
                  <td>{item.readBy}</td>
                  <td>{item.pages.join(', ') || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Block>
      )}
      {review.rejected.length > 0 && (
        <Block title="Second look: values not used">
          <ul>{review.rejected.map((item) => <li key={item.field}>{humanize(item.field)}: {item.reason}</li>)}</ul>
        </Block>
      )}
    </>
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
      <SecondLookDetails step={step} />
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
        <span className={`document-row-action-icon collapse-toggle${open ? '' : ' is-collapsed'}`}><Chevron /></span>
        {title}
        {count ? <span className="trace-count">{count}</span> : null}
      </button>
      {open && <div className="trace-section-body">{children}</div>}
    </section>
  );
}

export function TraceDetails({ node, root }: { node: TraceNode; root: TraceNode }) {
  const [open, setOpen] = useState<Set<SectionId>>(new Set(['overview', 'checks']));
  const thinking = node.calls.flatMap((call) => call.thinking ?? []);
  const checks = node.evals ?? [];
  const attributes = Object.fromEntries(Object.entries(node.step).filter(([key]) => !SHOWN_FIELDS.has(key)));
  const sections: Array<{ id: SectionId; title: string; count?: number; show: boolean; body: () => ReactNode }> = [
    { id: 'overview', title: 'Overview', show: hasOverview(node), body: () => <Overview node={node} /> },
    {
      id: 'checks', title: 'Sensibility checks', count: checks.length, show: checks.length > 0,
      body: () => <Checks results={checks} />,
    },
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
  const failed = Boolean(node.error) || (node.group && node.totals.errors > 0);

  return (
    <div className="trace-details">
      <header className="trace-details-head">
        <div className="trace-details-title">
          <KindIcon kind={node.kind} />
          {KIND_LABELS[node.kind] && <span className="trace-kind-name">{KIND_LABELS[node.kind]}</span>}
          <h3>{node.label}</h3>
        </div>
        {node.group ? <div className="trace-details-name" /> : <code className="trace-details-name">{node.name}</code>}
        <dl className="trace-stats trace-stats--small">
          <div><dt>Status</dt><dd className={failed ? 'trace-error-text' : 'trace-ok-text'}>{failed ? '✕ Error' : '✓ OK'}</dd></div>
          {node !== root && <div><dt>Starts</dt><dd>+{formatSeconds(Math.max(node.start - root.start, 0))}</dd></div>}
          {node.kind !== 'event' && node.seconds !== 0 && <div><dt>Took</dt><dd>{formatSeconds(node.seconds ?? node.end - node.start)}</dd></div>}
          {node.elapsed !== undefined && <div><dt>Since the step before</dt><dd>{formatSeconds(node.elapsed)}</dd></div>}
          {tokens > 0 && <div><dt>Tokens</dt><dd>{formatTokens(node.totals.inputTokens)} in · {formatTokens(node.totals.outputTokens)} out</dd></div>}
          {node.totals.cost > 0 && <div><dt>Cost</dt><dd>{formatCost(node.totals.cost)}</dd></div>}
          {node.confidence !== undefined && <div><dt>Confidence</dt><dd>{node.confidence.toFixed(2)}</dd></div>}
        </dl>
      </header>
      {shown.length === 0 ? (
        <p className="trace-muted trace-empty">{node.group ? 'Open a step below this heading for its details.' : 'Nothing more was recorded for this step.'}</p>
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
