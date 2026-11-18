import { useEffect, useId, useState } from 'react';
import { Icon } from '../../components/Icon';
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

// "+12" / "−24" / "No change", against the run before it that had a score; a dash when there was none to compare.
// Signed figures, not arrows: the sign carries the direction, and the words are there for a screen reader.
function ScoreChange({ change }: { change: number | null | undefined }) {
  if (change == null) {
    return (
      <span className="analysis-run-change analysis-run-change--none">
        —<span className="sr-only">No earlier score to compare</span>
      </span>
    );
  }
  if (change === 0) {
    return (
      <span className="analysis-run-change analysis-run-change--none">
        —<span className="sr-only">No change</span>
      </span>
    );
  }
  const up = change > 0;
  return (
    <span className={`analysis-run-change analysis-run-change--${up ? 'up' : 'down'}`}>
      {up ? '+' : '−'}
      {Math.abs(change)}
      <span className="sr-only"> {up ? 'up' : 'down'} from the run before</span>
    </span>
  );
}

// A cell's column name for a screen reader: the heading row is drawn for sighted readers only.
function ColumnName({ children }: { children: string }) {
  return <span className="sr-only">{children}: </span>;
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
  // Folded by default, like the rows above it; open while an earlier run is shown, so "Back to latest" is in reach.
  const [open, setOpen] = useState(viewingRunId !== null);
  useEffect(() => {
    if (viewingRunId !== null) setOpen(true);
  }, [viewingRunId]);
  const listId = useId();

  return (
    <section className="analysis-runs" aria-label="Previous runs">
      {/* A row like Checked and fine and Media check: its label on the left, here a toggle that folds out the runs;
          once open, the column names sit on the same line, over their columns. */}
      <div className="analysis-run-head">
      <h3 className="analysis-runs-title">
        <button
          type="button"
          className="analysis-runs-toggle"
          aria-expanded={open}
          aria-controls={listId}
          onClick={() => setOpen((isOpen) => !isOpen)}
        >
          Previous Runs
          <span className="analysis-runs-count">{runs.length}</span>
          <Icon name="chevronRight" size={14} className="analysis-runs-chevron" />
        </button>
      </h3>
      {open && (
        <>
          <span aria-hidden="true">Risk</span>
          <span className="analysis-run-cell--number" aria-hidden="true">Score</span>
          <span aria-hidden="true">Change</span>
          <span aria-hidden="true" />
        </>
      )}
      </div>
      {open && (
      <div id={listId}>
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
              {/* One cell per column of the heading row, empty when the run has nothing for it, so every row lines up. */}
              {openable ? (
                <>
                  <span className="analysis-run-cell">
                    <ColumnName>Risk</ColumnName>
                    {run.risk_level && (
                      <span className={`risk-badge risk-badge--${run.risk_level.toLowerCase()}`}>{run.risk_level}</span>
                    )}
                  </span>
                  <span className="analysis-run-cell analysis-run-cell--number">
                    <ColumnName>Score</ColumnName>
                    {run.risk_score != null && <strong className="analysis-run-score">{run.risk_score}</strong>}
                  </span>
                  <span className="analysis-run-cell">
                    <ColumnName>Change</ColumnName>
                    <ScoreChange change={run.score_change} />
                  </span>
                </>
              ) : (
                <span className="analysis-run-cell analysis-run-status">{STATUS_LABELS[run.status] ?? run.status}</span>
              )}
              <span className="analysis-run-cell analysis-run-action">
                {openable && !isViewing && (
                  <button type="button" className="text-button analysis-run-open" onClick={() => onOpen(isLatest ? null : run.run_id)}>
                    {isLatest ? 'Back to latest' : 'Open'}
                  </button>
                )}
                {isViewing && <span className="analysis-run-viewing">Showing</span>}
              </span>
            </li>
          );
        })}
      </ol>
      </div>
      )}
    </section>
  );
}
