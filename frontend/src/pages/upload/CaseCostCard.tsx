import type { CaseCostRun, DocumentRecord } from '../../services/uploadService';
import { formatUsd as usd } from './processingTimes';
import { useCaseCost } from './useCaseCost';

// Placeholder view of a case's Claude spend (GET /audit/cases/{id}/cost). Deliberately plain; to be reworked.
const RUN_LABELS: Record<string, string> = {
  document: 'Classify + extract',
  extraction_rerun: 'Re-extract',
  detection: 'Detection',
  case_analysis: 'Case analysis',
};

export function CaseCostCard({ caseId, documents }: { caseId: string; documents: DocumentRecord[] }) {
  const { cost, error, loading, reload } = useCaseCost(caseId);

  // A document still in the case by its name now; one deleted or moved out by the name it was uploaded with.
  const filename = (id: string) => {
    const current = documents.find((document) => document.id === id)?.filename;
    if (current) return current;
    const known = cost?.documents?.[id];
    const name = known?.filename ?? `${id.slice(0, 8)}…`;
    return `${name} (${known?.deleted ? 'deleted' : known && !known.in_case ? 'moved to another case' : 'removed'})`;
  };

  const runLabel = (run: CaseCostRun) => {
    const label = RUN_LABELS[run.run ?? ''] ?? run.type;
    const repeat = (run.attempt ?? 1) > 1 ? ` · run ${run.attempt}` : '';
    const ending = run.outcome === 'failed' ? ' · failed' : run.outcome === 'cancelled' ? ' · cancelled' : '';
    return `${label}${repeat}${ending}`;
  };

  return (
    <section className="summary-card">
      <div className="summary-card-header">
        <div className="summary-card-title">
          <span className="summary-icon summary-icon--audit">$</span>
          <h2>Claude Cost</h2>
        </div>
        <button type="button" className="case-overview-text-button" onClick={() => void reload()} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {error && (
        <div className="summary-message summary-message--warning">
          <span className="summary-message-icon">⚠</span>
          <div>
            <strong>Couldn't load the cost.</strong>
            <p>{error}</p>
          </div>
        </div>
      )}

      {cost && (
        <>
          <p>
            <strong>Total: {usd(cost.total_usd)}</strong> · documents{' '}
            {usd(cost.total_usd - cost.case_analysis_usd)} · case analysis {usd(cost.case_analysis_usd)}
          </p>
          {((cost.repeated_runs_usd ?? 0) > 0 || (cost.failed_or_cancelled_usd ?? 0) > 0) && (
            <p>
              Included: retries and reruns {usd(cost.repeated_runs_usd ?? 0)} · failed or cancelled runs{' '}
              {usd(cost.failed_or_cancelled_usd ?? 0)}
            </p>
          )}
          <p>
            <small>
              At Anthropic list prices, from each run's record in the audit trail: every run counts, including
              earlier case analyses, retries, cancelled runs and documents since deleted.
            </small>
          </p>

          {Object.keys(cost.documents_usd).length > 0 && (
            <table className="supporting-document-breakdown">
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Cost</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(cost.documents_usd)
                  .sort(([, a], [, b]) => b - a)
                  .map(([id, value]) => (
                    <tr key={id}>
                      <td>{filename(id)}</td>
                      <td>{usd(value)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          )}

          {cost.runs.length > 0 ? (
            <details>
              <summary>{cost.runs.length} runs</summary>
              <table className="supporting-document-breakdown">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Run</th>
                    <th>Document</th>
                    <th>Claude calls</th>
                    <th>Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {cost.runs.map((run, index) => (
                    <tr key={`${run.occurred_at}-${index}`}>
                      <td>{new Date(run.occurred_at).toLocaleString()}</td>
                      <td>{runLabel(run)}</td>
                      <td>{run.document_id ? filename(run.document_id) : '—'}</td>
                      <td>{run.llm_calls ?? '—'}</td>
                      <td>{usd(run.llm_cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          ) : (
            <p>No costed runs recorded for this case yet.</p>
          )}
        </>
      )}
    </section>
  );
}
