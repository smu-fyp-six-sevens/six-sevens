import type { CaseAnalysis } from '../../services/uploadService';
import { RefreshButton } from '../../components/RefreshButton';
import { ThinkingLog } from '../../components/ThinkingLog';

// Debug aid, the case-level counterpart of the document page's LangGraph trace
// (see backend/app/case_pipeline/trace.py). Unlike findings, it also lists hypotheses that were dropped.
export function CaseAnalysisTracePanel({
  analysis,
  isRefreshing,
  onRefresh,
}: {
  analysis: CaseAnalysis | null;
  isRefreshing: boolean;
  onRefresh: () => void | Promise<void>;
}) {
  const steps = analysis?.trace ?? [];
  const proposed = steps.find((step) => step.step === 'hypotheses');
  const verifications = steps.filter((step) => step.step === 'verify');
  const kept = verifications.filter((step) => step.kept === true).length;
  const scoring = steps.find((step) => step.step === 'scoring');
  const scoredFindings = Array.isArray(scoring?.findings)
    ? (scoring.findings as Array<Record<string, unknown>>)
    : [];
  const outlookStep = steps.find((step) => step.step === 'outlook');
  const outlookRequests = Array.isArray(outlookStep?.requests)
    ? (outlookStep.requests as Array<Record<string, unknown>>)
    : [];

  return (
    <section className="details-card debug-card">
      <RefreshButton isRefreshing={isRefreshing} onClick={() => void onRefresh()} label="case analysis trace" />
      <h2>Case analysis trace (debug)</h2>
      {!analysis ? (
        <p>No analysis has been run for this case yet.</p>
      ) : (
        <>
          <div>run_id: {analysis.run_id}</div>
          <div>status: {analysis.status}{analysis.stale ? ' (stale)' : ''}</div>
          <div>started_at: {analysis.started_at}</div>
          <div>finished_at: {analysis.finished_at ?? '—'}</div>
          <div>
            hypotheses proposed / kept after verification:{' '}
            {Array.isArray(proposed?.proposed) ? proposed.proposed.length : '—'} / {kept}
          </div>
          {scoring && (
            <div className="debug-section">
              <strong>Scoring</strong>
              <div>
                {String(scoring.level ?? '—')} · score {String(scoring.score ?? '—')}/100 · {String(scoring.points ?? '—')} points
                {' '}from {scoredFindings.length} finding(s) on {String(scoring.documents_scanned ?? '—')}/
                {String(scoring.documents_total ?? '—')} documents
              </div>
              <div>
                without cross-document findings: {String(scoring.level_without_cross_document ?? '—')} ·{' '}
                {String(scoring.score_without_cross_document ?? '—')} ({String(scoring.cross_document_findings ?? 0)} added)
              </div>
              {Array.isArray(scoring.reasons) && scoring.reasons.map((reason) => <div key={String(reason)}>{String(reason)}</div>)}
              {scoredFindings.length > 0 && (
                <table className="debug-table">
                  <thead>
                    <tr>
                      <th align="right">points</th>
                      <th align="left">severity</th>
                      <th align="left">category</th>
                      <th align="left">tool</th>
                      <th align="left">document</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scoredFindings.map((finding) => (
                      <tr key={String(finding.finding_id)}>
                        <td align="right">{String(finding.points)}</td>
                        <td>{String(finding.severity)}</td>
                        <td>{String(finding.category ?? '—')}</td>
                        <td>{String(finding.tool ?? '—')}</td>
                        <td>{String(finding.document)}{finding.source === 'cross_document' ? ' (case)' : ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <details>
                <summary>Rules applied</summary>
                <pre className="debug-json">
                  {JSON.stringify(
                    {
                      severity_points: scoring.severity_points,
                      level_thresholds: scoring.level_thresholds,
                      critical_needs_high_documents: scoring.critical_needs_high_documents,
                      noise_cap_per_document: scoring.noise_cap_per_document,
                      repeat_weight: scoring.repeat_weight,
                      unassessed_checks: scoring.unassessed_checks,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
            </div>
          )}
          {outlookRequests.length > 0 && (
            <div className="debug-section">
              <strong>Outlook</strong> (current {String(outlookStep?.current_score ?? '—')}, best case{' '}
              {String(outlookStep?.best_case_score ?? '—')})
              {outlookRequests.map((request, index) => (
                <details key={`${String(request.title)}-${index}`}>
                  <summary>
                    {String(request.title)}
                    {request.scored ? ` → ${String(request.score_if_supports)} (${String(request.level_if_supports)})` : ' (not scored)'}
                  </summary>
                  <pre className="debug-json">{JSON.stringify(request, null, 2)}</pre>
                </details>
              ))}
            </div>
          )}
          {analysis.error && <div className="debug-error">error: {analysis.error}</div>}
          <ThinkingLog steps={steps} />
          <details className="debug-section">
            <summary>Full step log ({steps.length} steps)</summary>
            <pre className="debug-json">{JSON.stringify(steps, null, 2)}</pre>
          </details>
          {analysis.audit && (
            <details className="debug-section">
              <summary>Audit ({analysis.audit.tool_calls?.length ?? 0} tool calls)</summary>
              <pre className="debug-json">
                {JSON.stringify(analysis.audit, null, 2)}
              </pre>
            </details>
          )}
        </>
      )}
    </section>
  );
}
