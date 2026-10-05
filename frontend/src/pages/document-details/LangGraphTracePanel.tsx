import type { LangGraphTrace } from '../../services/uploadService';
import { RefreshButton } from '../../components/RefreshButton';

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
        <section className="details-card" style={{ fontFamily: 'monospace', fontSize: '0.85em', position: 'relative' }}>
          <RefreshButton isRefreshing={isRefreshingTrace} onClick={() => void refreshLangGraphTrace()} label="langgraph trace" />
          <h2 style={{ marginTop: 0 }}>LangGraph trace (debug)</h2>
          {!langGraphTrace ? (
            <p>No langgraph trace yet for this document (not processed via langgraph in this backend process, or not started yet).</p>
          ) : (
            <>
              <div>rule_top_type / strength: {String(langGraphTrace.rule_top_type ?? '—')} / {String(langGraphTrace.rule_top_strength ?? '—')}</div>
              <div>certain_type: {String(langGraphTrace.certain_type ?? '—')}</div>
              <div>llm_top_type / strength (only if the agent called it): {String(langGraphTrace.llm_top_type ?? '—')} / {String(langGraphTrace.llm_top_strength ?? '—')}</div>
              <div>vision_top_type / strength (only if the agent called it): {String(langGraphTrace.vision_top_type ?? '—')} / {String(langGraphTrace.vision_top_strength ?? '—')}</div>
              <div>final_status: {String(langGraphTrace.final_status ?? '—')}</div>
              <div>
                classification_reasoning (debug only — not saved to the database yet):{' '}
                {String(langGraphTrace.classification_reasoning ?? '—')}
              </div>
              <details style={{ marginTop: '0.5em' }}>
                <summary>Full step log ({langGraphTrace.steps.length} steps)</summary>
                <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {JSON.stringify(langGraphTrace.steps, null, 2)}
                </pre>
              </details>
            </>
          )}
        </section>
  );
}
