import { useMemo, useState } from 'react';
import { RefreshButton } from '../RefreshButton';
import { buildTrace, type TraceStep } from './buildTrace';
import { KindIcon } from './KindIcon';
import { TraceDrawer } from './TraceDrawer';
import { durationTone, formatCost, formatSeconds, formatTokens } from './format';

// A card listing the runs in a trace (a document's classification, extraction and detection, or a case analysis
// run); opening one shows the whole trace beside the selected span's details.
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
  const [openKey, setOpenKey] = useState<string | null>(null);
  // A trace with no spans (from before they were recorded) is one list of steps.
  const runs = trace.hasSpans ? trace.roots.filter((node) => node.kind !== 'event') : [];

  return (
    <section className="details-card trace-card" aria-label={title}>
      <RefreshButton isRefreshing={isRefreshing} onClick={() => void onRefresh()} label={title.toLowerCase()} />
      <div className="trace-card-head">
        <h2>{title}</h2>
        {trace.roots.length > 0 && (
          <button type="button" className="trace-open" onClick={() => setOpenKey((runs[runs.length - 1] ?? trace.roots[0]).key)}>
            Open trace
          </button>
        )}
      </div>
      {trace.roots.length === 0 ? (
        <p className="trace-muted">{emptyText}</p>
      ) : runs.length === 0 ? (
        <p className="trace-muted">{trace.roots.length} step(s) recorded. Open the trace to see them.</p>
      ) : (
        <ul className="trace-runs">
          {runs.map((run) => {
            const seconds = run.end - run.start;
            const tokens = run.totals.inputTokens + run.totals.outputTokens;
            return (
              <li key={run.key}>
                <button type="button" className="trace-run" onClick={() => setOpenKey(run.key)}>
                  <KindIcon kind={run.kind} />
                  <span className="trace-run-label">{run.label}</span>
                  <span className={run.totals.errors ? 'trace-chip trace-chip--error' : 'trace-chip trace-chip--ok'}>
                    {run.totals.errors ? '✕ Error' : '✓ OK'}
                  </span>
                  {run.totals.failures > 0 && (
                    <span className="trace-chip trace-chip--warn" title="Failed validations">⚠ {run.totals.failures}</span>
                  )}
                  <span className="trace-run-figures">
                    {run.totals.calls} call{run.totals.calls === 1 ? '' : 's'} · {formatTokens(tokens)} tokens · {formatCost(run.totals.cost)}
                  </span>
                  <span className={`trace-duration trace-duration--${durationTone(seconds)}`}>{formatSeconds(seconds)}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {openKey && <TraceDrawer title={title} trace={trace} initialKey={openKey} onClose={() => setOpenKey(null)} />}
    </section>
  );
}
