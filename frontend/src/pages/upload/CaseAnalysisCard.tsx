import { useCallback, useEffect, useState } from 'react';
import type { CaseAnalysis, CaseAnalysisOverview } from '../../services/uploadService';
import { getCaseAnalysis, runCaseAnalysis } from '../../services/uploadService';
import { CaseAnalysisTracePanel } from './CaseAnalysisTracePanel';

const CATEGORY_LABELS: Record<string, string> = {
  identity_entity_conflict: 'Identity conflict',
  cross_document_variance: 'Documents differ',
  behavioural_anomaly: 'Unusual behaviour',
  unsupported_claim: 'Unsupported claim',
};

// "Run case analysis": cross-document findings, what-if outlook and a plain-language summary.
export function CaseAnalysisCard({
  caseId,
  analysisReady,
  overview,
}: {
  caseId: string;
  analysisReady: boolean;
  overview: CaseAnalysisOverview | null;
}) {
  const [analysis, setAnalysis] = useState<CaseAnalysis | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const load = useCallback(async () => {
    setIsRefreshing(true);
    try {
      setAnalysis(await getCaseAnalysis(caseId));
    } catch {
      // The card still shows the overview; a failed detail fetch isn't worth an error banner.
    } finally {
      setIsRefreshing(false);
    }
  }, [caseId]);

  // The case snapshot carries the overview over the socket, so a change in it means the result moved.
  useEffect(() => {
    if (overview) {
      void load();
    } else {
      setAnalysis(null);
    }
  }, [overview?.run_id, overview?.status, overview?.stale, load]);

  const running = starting || overview?.status === 'running';

  async function start() {
    setStarting(true);
    setError(null);
    try {
      await runCaseAnalysis(caseId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to start the case analysis');
    } finally {
      setStarting(false);
    }
  }

  const outlook = analysis?.outlook ?? null;
  const showResult = analysis?.status === 'complete';

  return (
    <>
    <section className="summary-card">
      <div className="summary-card-header">
        <div className="summary-card-title">
          <span className="summary-icon summary-icon--risk">◇</span>
          <h2>Case Analysis</h2>
        </div>
        {analysisReady && (
          <button type="button" disabled={running} onClick={() => void start()}>
            {running ? 'Analysing…' : overview ? 'Run again' : 'Run case analysis'}
          </button>
        )}
      </div>

      {!analysisReady && !overview && (
        <div className="summary-message summary-message--info">
          <span className="summary-message-icon">ⓘ</span>
          <div>
            <strong>Not ready yet.</strong>
            <p>The analysis can be run once every approved document has been scanned.</p>
          </div>
        </div>
      )}

      {error && <p style={{ color: 'crimson' }}>{error}</p>}

      {overview?.stale && (
        <div className="summary-message summary-message--warning">
          <span className="summary-message-icon">⚠</span>
          <div>
            <strong>Out of date.</strong>
            <p>Documents changed since this analysis ran. The risk score and recommendations are from before that; run it again to update them.</p>
          </div>
        </div>
      )}

      {analysis?.status === 'failed' && (
        <div className="summary-message summary-message--warning">
          <span className="summary-message-icon">⚠</span>
          <div>
            <strong>The analysis did not finish.</strong>
            <p>{analysis.error ?? 'Run it again.'}</p>
          </div>
        </div>
      )}

      {showResult && analysis && (
        <>
          {analysis.summary && <p>{analysis.summary}</p>}

          {analysis.findings.length === 0 ? (
            <div className="summary-message summary-message--success">
              <span className="summary-message-icon">✓</span>
              <div>
                <strong>No cross-document findings.</strong>
                <p>Nothing was proven across this case's documents.</p>
              </div>
            </div>
          ) : (
            <ul className="risk-findings">
              {analysis.findings.map((finding) => (
                <li key={finding.id}>
                  <span className={`risk-severity risk-severity--${finding.severity}`}>{finding.severity}</span>
                  <span className="risk-finding-text">
                    <span className="risk-finding-summary">
                      {CATEGORY_LABELS[finding.category] ?? finding.category}: {finding.summary}
                    </span>
                    <span className="risk-finding-rationale">{finding.rationale}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}

          {outlook && outlook.requests.length > 0 && (
            <>
              <h3>What would change the score</h3>
              {outlook.best_case_score != null && outlook.current_score != null && (
                <p>
                  Currently <strong>{outlook.current_score}</strong> ({outlook.current_level}). If everything below
                  supports the client, the best case is <strong>{outlook.best_case_score}</strong> (
                  {outlook.best_case_level}).
                </p>
              )}
              <ul className="risk-findings">
                {outlook.requests.map((request) => (
                  <li key={request.title}>
                    <span className="risk-finding-text">
                      <span className="risk-finding-summary">
                        {request.title}
                        {request.score_if_supports != null && ` — would become ${request.score_if_supports}`}
                        {request.level_if_supports ? ` (${request.level_if_supports})` : ''}
                      </span>
                      <span className="risk-finding-rationale">{request.what_it_shows}</span>
                    </span>
                  </li>
                ))}
              </ul>
              <p className="risk-finding-rationale">
                An estimate that assumes the evidence supports the client. If it does not, the score stays where it is.
              </p>
            </>
          )}
        </>
      )}
    </section>
    <CaseAnalysisTracePanel analysis={analysis} isRefreshing={isRefreshing} onRefresh={load} />
    </>
  );
}
