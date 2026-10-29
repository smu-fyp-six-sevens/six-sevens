import { officerName } from '../../lib/officers';
import type { CaseAnalysisRunItem } from '../../services/uploadService';

const STATUS_LABELS: Record<string, string> = {
  running: 'Running',
  failed: 'Did not finish',
  superseded: 'Replaced by a newer run',
};

function formatWhen(value: string): string {
  return new Date(value).toLocaleString('en-SG', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

// "▲ 12" / "▼ 24" / "No change", against the run before it that had a score.
function ScoreChange({ change }: { change: number | null | undefined }) {
  if (change == null) return null;
  if (change === 0) return <span className="analysis-run-change">No change</span>;
  const up = change > 0;
  return (
    <span
      className={`analysis-run-change analysis-run-change--${up ? 'up' : 'down'}`}
      title={`${up ? 'Up' : 'Down'} ${Math.abs(change)} from the run before`}
    >
      {up ? '▲' : '▼'} {Math.abs(change)}
    </span>
  );
}

// Every analysis of the case, newest first. A finished one opens read-only; the latest is the card's own result.
export function PreviousRuns({
  runs,
  viewingRunId,
  onOpen,
}: {
  runs: CaseAnalysisRunItem[];
  // The run the card is showing; the latest when null.
  viewingRunId: string | null;
  onOpen: (runId: string | null) => void;
}) {
  return (
    <section className="analysis-runs" aria-label="Previous runs">
      <h3 className="analysis-heading">Previous runs</h3>
      <ol className="analysis-run-list">
        {runs.map((run, index) => {
          const isLatest = index === 0;
          const isViewing = isLatest ? viewingRunId === null : viewingRunId === run.run_id;
          const openable = run.status === 'complete';
          return (
            <li key={run.run_id} className={`analysis-run${isViewing ? ' analysis-run--viewing' : ''}`} data-testid="analysis-run">
              <div className="analysis-run-main">
                <time className="analysis-run-when" dateTime={run.started_at}>{formatWhen(run.started_at)}</time>
                {run.started_by && <span className="analysis-run-by">by {officerName(run.started_by)}</span>}
                {isLatest && <span className="analysis-run-tag">Latest</span>}
                {run.stale && <span className="analysis-run-tag analysis-run-tag--stale">Out of date</span>}
              </div>
              <div className="analysis-run-result">
                {openable ? (
                  <>
                    {run.risk_level && (
                      <span className={`risk-badge risk-badge--${run.risk_level.toLowerCase()}`}>{run.risk_level}</span>
                    )}
                    {run.risk_score != null && <strong className="analysis-run-score">{run.risk_score}</strong>}
                    <ScoreChange change={run.score_change} />
                    <span className="analysis-run-findings">
                      {run.finding_count} cross-document finding{run.finding_count === 1 ? '' : 's'}
                    </span>
                  </>
                ) : (
                  <span className="analysis-run-status">{STATUS_LABELS[run.status] ?? run.status}</span>
                )}
                {openable && !isViewing && (
                  <button type="button" className="text-button analysis-run-open" onClick={() => onOpen(isLatest ? null : run.run_id)}>
                    {isLatest ? 'Back to latest' : 'Open'}
                  </button>
                )}
                {isViewing && <span className="analysis-run-viewing">Showing</span>}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
