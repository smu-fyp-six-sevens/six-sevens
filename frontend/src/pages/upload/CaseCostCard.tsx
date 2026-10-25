import { useCallback, useEffect, useState } from 'react';
import { getCaseCost, type CaseCost, type DocumentRecord } from '../../services/uploadService';

// Placeholder view of a case's Claude spend (GET /audit/cases/{id}/cost). Deliberately plain; to be reworked.
const RUN_LABELS: Record<string, string> = {
  document: 'Classify + extract',
  extraction_rerun: 'Re-extract',
  detection: 'Detection',
  case_analysis: 'Case analysis',
};

const usd = (value: number) => `$${value.toFixed(4)}`;

export function CaseCostCard({ caseId, documents }: { caseId: string; documents: DocumentRecord[] }) {
  const [cost, setCost] = useState<CaseCost | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setCost(await getCaseCost(caseId));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load the cost');
    } finally {
      setLoading(false);
    }
  }, [caseId]);

  useEffect(() => {
    void load();
  }, [load]);

  const filename = (id: string) => documents.find((document) => document.id === id)?.filename ?? `${id.slice(0, 8)}… (removed)`;

  return (
    <section className="summary-card">
      <div className="summary-card-header">
        <div className="summary-card-title">
          <span className="summary-icon summary-icon--audit">$</span>
          <h2>Claude Cost</h2>
        </div>
        <button type="button" className="case-overview-text-button" onClick={() => void load()} disabled={loading}>
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
          <p>
            <small>
              At Anthropic list prices, from each run's record in the audit trail. Runs from before cost was
              recorded aren't included.
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
                      <td>{RUN_LABELS[run.run ?? ''] ?? run.type}</td>
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
