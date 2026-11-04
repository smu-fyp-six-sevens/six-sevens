import type { CacheUse, CaseCostRun, DocumentRecord } from '../../services/uploadService';
import { formatUsd as usd } from './processingTimes';
import { useCaseCost } from './useCaseCost';

// Placeholder view of a case's Claude spend (GET /audit/cases/{id}/cost). Deliberately plain; to be reworked.
const RUN_LABELS: Record<string, string> = {
  document: 'Classify + extract',
  extraction_rerun: 'Re-extract',
  detection: 'Detection',
  case_analysis: 'Case analysis',
};

const tokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n));
const percent = (rate: number | null | undefined) => (rate == null ? '—' : `${Math.round(rate * 100)}%`);

// How much of the case's Claude input came from the prompt cache, and what that saved.
function PromptCaching({ cache, byRun }: { cache: CacheUse; byRun: Record<string, CacheUse> }) {
  const input = cache.input_tokens ?? 0;
  const read = cache.cache_read_tokens ?? 0;
  const written = cache.cache_write_tokens ?? 0;
  const uncached = Math.max(0, input - read - written);
  const share = (n: number) => `${input ? (n / input) * 100 : 0}%`;
  return (
    <section className="cache-use" aria-label="Prompt caching">
      <h3>Prompt caching</h3>
      {!cache.runs ? (
        <p className="cache-use-note">No runs with token counts yet.</p>
      ) : (
        <>
          <div className="cache-use-headline">
            <span className="cache-use-rate">{percent(cache.hit_rate)}</span>
            <span>of input tokens were read from the cache, saving <strong>{usd(cache.saved_usd ?? 0)}</strong></span>
          </div>
          <div className="cache-use-meter" role="img" aria-label={`${percent(cache.hit_rate)} read from the cache`}>
            <span className="cache-use-meter--read" style={{ width: share(read) }} />
            <span className="cache-use-meter--write" style={{ width: share(written) }} />
          </div>
          <ul className="cache-use-legend">
            <li><span className="cache-use-swatch cache-use-meter--read" /> Read from cache {tokens(read)}</li>
            <li><span className="cache-use-swatch cache-use-meter--write" /> Written to cache {tokens(written)}</li>
            <li><span className="cache-use-swatch cache-use-meter--plain" /> Not cached {tokens(uncached)}</li>
          </ul>
          <table className="supporting-document-breakdown">
            <thead>
              <tr>
                <th>Run</th>
                <th>Runs</th>
                <th>Input tokens</th>
                <th>From cache</th>
                <th>Saved</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(byRun).map(([kind, use]) => (
                <tr key={kind}>
                  <td>{RUN_LABELS[kind] ?? kind}</td>
                  <td>{use.runs}</td>
                  <td>{tokens(use.input_tokens ?? 0)}</td>
                  <td>{percent(use.hit_rate)}</td>
                  <td>{usd(use.saved_usd ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="cache-use-note">
            Saved is what the cache reads would have cost as plain input, less what the cache writes cost above it.
            {(cache.runs_without_saving ?? 0) > 0 &&
              ` ${cache.runs_without_saving} earlier run(s) predate the saving being recorded, so their saving isn't counted.`}
          </p>
        </>
      )}
    </section>
  );
}

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

          {cost.cache && <PromptCaching cache={cost.cache} byRun={cost.cache_by_run ?? {}} />}

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
