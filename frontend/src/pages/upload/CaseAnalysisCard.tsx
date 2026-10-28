import { useCallback, useEffect, useState } from 'react';
import type { CaseAnalysis, CaseAnalysisOverview } from '../../services/uploadService';
import { getCaseAnalysis, runCaseAnalysis } from '../../services/uploadService';
import { CaseAnalysisTracePanel } from './CaseAnalysisTracePanel';
import { ErrorMessage } from '../../components/ErrorMessage';

const CATEGORY_LABELS: Record<string, string> = {
  identity_entity_conflict: 'Identity conflict',
  cross_document_variance: 'Documents differ',
  behavioural_anomaly: 'Unusual behaviour',
  unsupported_claim: 'Unsupported claim',
};

const SEVERITY_RANK: Record<string, number> = { high: 3, medium: 2, low: 1 };

// "Run case analysis": cross-document findings, and a plain-language summary.
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

  // The case snapshot carries the overview over the socket, so a change in it means the result moved. Each
  // snapshot is a new object, so the effect watches these fields, not the object.
  const hasOverview = overview !== null;
  const runId = overview?.run_id;
  const runStatus = overview?.status;
  const stale = overview?.stale;
  useEffect(() => {
    if (hasOverview) {
      void load();
    } else {
      setAnalysis(null);
    }
  }, [hasOverview, runId, runStatus, stale, load]);

  // A failed run's stored error is an exception's text: the console has it, the card says to run it again.
  const failedError = analysis?.status === 'failed' ? analysis.error : null;
  useEffect(() => {
    if (failedError) console.error(`Case analysis for case ${caseId} failed:`, failedError);
  }, [caseId, failedError]);

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

  const findings = [...(analysis?.findings ?? [])].sort(
    (a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity],
  );
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
          <button type="button" className="analysis-action" disabled={running} onClick={() => void start()}>
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

      {error && <ErrorMessage className="card-error" message={error} />}

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
            <p>Run it again.</p>
          </div>
        </div>
      )}

      {showResult && analysis && (
        <>
          {analysis.summary && <p className="analysis-summary">{analysis.summary}</p>}

          {findings.length === 0 ? (
            <div className="summary-message summary-message--success">
              <span className="summary-message-icon">✓</span>
              <div>
                <strong>No cross-document findings.</strong>
                <p>Nothing was proven across this case's documents.</p>
              </div>
            </div>
          ) : (
            <ul className="risk-findings">
              {findings.map((finding) => (
                <li key={finding.id}>
                  <span className={`risk-severity risk-severity--${finding.severity}`}>{finding.severity}</span>
                  <span className="risk-finding-text">
                    <span className="risk-finding-summary">
                      <span className="analysis-category">
                        {CATEGORY_LABELS[finding.category] ?? finding.category}
                      </span>
                      {finding.summary}
                    </span>
                    <span className="risk-finding-rationale">{finding.rationale}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}

          {(analysis.consistent_checks ?? []).length > 0 && (
            <>
              <h3 className="analysis-heading">Checked and consistent</h3>
              <ul className="risk-findings">
                {(analysis.consistent_checks ?? []).map((line) => (
                  <li key={line}>
                    <span className="risk-finding-text">
                      <span className="risk-finding-rationale">{line}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </section>
    <CaseAnalysisTracePanel analysis={analysis} isRefreshing={isRefreshing} onRefresh={load} />
    </>
  );
}
