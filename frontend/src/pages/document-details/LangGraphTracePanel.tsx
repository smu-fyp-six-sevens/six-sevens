import type { LangGraphTrace, LangGraphTraceStep } from '../../services/uploadService';
import { RefreshButton } from '../../components/RefreshButton';

// One run's use of its limits, from its run_audit step (backend run_context.py): tokens against the token budget,
// model calls against the call ceiling. Traces from before the budget have no token figures.
function budgetLine(step: LangGraphTraceStep): string {
  const count = (value: unknown) => (typeof value === 'number' ? value.toLocaleString() : '—');
  const kind = typeof step.run_kind === 'string' ? step.run_kind : 'run';
  const tokens =
    typeof step.token_budget === 'number'
      ? `${count(step.tokens)} / ${count(step.token_budget)} tokens`
      : `${count(step.tokens)} tokens (no budget)`;
  const stopped =
    typeof step.tokens === 'number' && typeof step.token_budget === 'number' && step.tokens >= step.token_budget
      ? ' · stopped: token budget reached'
      : '';
  return `${kind}: ${tokens} · ${count(step.model_calls)} / ${count(step.call_ceiling)} model calls${stopped}`;
}

export function LangGraphTracePanel({
  langGraphTrace,
  isRefreshingTrace,
  refreshLangGraphTrace,
}: {
  langGraphTrace: LangGraphTrace | null;
  isRefreshingTrace: boolean;
  refreshLangGraphTrace: () => void | Promise<void>;
}) {
  return (
        <section className="details-card debug-card">
          <RefreshButton isRefreshing={isRefreshingTrace} onClick={() => void refreshLangGraphTrace()} label="langgraph trace" />
          <h2>LangGraph trace (debug)</h2>
          {!langGraphTrace ? (
            <p>No langgraph trace yet for this document (not processed via langgraph in this backend process, or not started yet).</p>
          ) : (
            <>
              {langGraphTrace.steps.some((step) => step.step === 'run_audit') && (
                <div className="debug-section">
                  <strong>Budget</strong>
                  {langGraphTrace.steps
                    .filter((step) => step.step === 'run_audit')
                    .map((step) => (
                      <div key={step.at}>{budgetLine(step)}</div>
                    ))}
                </div>
              )}
              <div>rule_top_type / strength: {String(langGraphTrace.rule_top_type ?? '—')} / {String(langGraphTrace.rule_top_strength ?? '—')}</div>
              <div>certain_type: {String(langGraphTrace.certain_type ?? '—')}</div>
              <div>llm_top_type / strength (only if the agent called it): {String(langGraphTrace.llm_top_type ?? '—')} / {String(langGraphTrace.llm_top_strength ?? '—')}</div>
              <div>vision_top_type / strength (only if the agent called it): {String(langGraphTrace.vision_top_type ?? '—')} / {String(langGraphTrace.vision_top_strength ?? '—')}</div>
              <div>final_status: {String(langGraphTrace.final_status ?? '—')}</div>
              <div>
                classification_reasoning (debug only — not saved to the database yet):{' '}
                {String(langGraphTrace.classification_reasoning ?? '—')}
              </div>
              <details className="debug-section">
                <summary>Full step log ({langGraphTrace.steps.length} steps)</summary>
                <pre className="debug-json">
                  {JSON.stringify(langGraphTrace.steps, null, 2)}
                </pre>
              </details>
            </>
          )}
        </section>
  );
}
