import { useState } from 'react';
import type { CaseRequirementCoverage, CaseStageChange } from '../../services/uploadService';
import { finaliseCase, reopenCase } from '../../services/uploadService';
import { BRANCH_STAGES, CASE_STAGES, caseStageLabel, caseStageTone, isMovedBack } from '../../utils/caseStages';

function formatChangedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('en-SG', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

// Where the case stands: its stage, the stages around it, and who moved it when.
export function CaseStageCard({
  caseId,
  stage,
  history,
  problem,
  onChanged,
}: {
  caseId: string;
  stage: string;
  history: CaseStageChange[];
  // One line on something that failed and holds the case up (a detection run, the analysis), or null.
  problem: string | null;
  onChanged: (summary: CaseRequirementCoverage) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const steps = CASE_STAGES.filter((step) => !BRANCH_STAGES.has(step) || step === stage);
  const currentIndex = Math.max(steps.indexOf(stage as (typeof CASE_STAGES)[number]), 0);
  const action =
    stage === 'case_analysed' || stage === 'additional_documents_required'
      ? { label: 'Finalise case', run: finaliseCase, failure: "Couldn't finalise the case." }
      : stage === 'finalised'
        ? { label: 'Reopen case', run: reopenCase, failure: "Couldn't reopen the case." }
        : null;

  async function changeStage() {
    if (!action) return;
    setBusy(true);
    setError(null);
    try {
      onChanged(await action.run(caseId));
    } catch {
      setError(`${action.failure} Refresh and try again.`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="summary-card case-stage-card" aria-labelledby="case-stage-title">
      <div className="summary-card-header">
        <div className="summary-card-title">
          <span className="summary-icon summary-icon--audit">◎</span>
          <h2 id="case-stage-title">Case Progress</h2>
          <span className={`case-stage-badge tone-${caseStageTone(stage)}`}>{caseStageLabel(stage)}</span>
        </div>
        {action && (
          <button type="button" className="analysis-action" disabled={busy} onClick={() => void changeStage()}>
            {busy ? 'Saving…' : action.label}
          </button>
        )}
      </div>

      {problem && (
        <p className="case-stage-problem" role="status">
          ⚠ {problem}
        </p>
      )}

      {error && (
        <p className="case-stage-error" role="alert">
          {error}
        </p>
      )}

      <ol className="case-stage-steps" aria-label="Case stages">
        {steps.map((step, index) => {
          const state = index < currentIndex ? 'done' : index === currentIndex ? 'current' : 'upcoming';
          return (
            <li
              key={step}
              className={`case-stage-step case-stage-step--${state}`}
              aria-current={state === 'current' ? 'step' : undefined}
            >
              {caseStageLabel(step)}
            </li>
          );
        })}
      </ol>

      <h3 className="case-stage-history-title">Stage history</h3>
      {history.length === 0 ? (
        <p className="case-stage-empty">No stage changes recorded yet.</p>
      ) : (
        <table className="case-stage-history">
          <thead>
            <tr>
              <th scope="col">Stage</th>
              <th scope="col">Changed</th>
              <th scope="col">By</th>
            </tr>
          </thead>
          <tbody>
            {[...history].reverse().map((change) => (
              <tr key={change.id}>
                <td>
                  {change.from_status ? `${caseStageLabel(change.from_status)} → ` : ''}
                  <b>{caseStageLabel(change.to_status)}</b>
                  {isMovedBack(change.from_status, change.to_status) && (
                    <span className="case-stage-moved-back">Moved back</span>
                  )}
                </td>
                <td>{formatChangedAt(change.changed_at)}</td>
                <td>{change.changed_by}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
