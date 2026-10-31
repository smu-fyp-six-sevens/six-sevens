import { useCallback, useEffect, useState } from 'react';
import type { CaseAnalysis, CaseAnalysisOverview } from '../../services/uploadService';
import type { CaseAnalysisRunItem } from '../../services/uploadService';
import { getCaseAnalysis, getCaseAnalysisRun, listCaseAnalysisRuns, runCaseAnalysis } from '../../services/uploadService';
import { AnalysisSummaryBlock } from './AnalysisSummaryBlock';
import { analysisFindingAnchor } from './analysisSummary';
import { PreviousRuns } from './PreviousRuns';
import { CaseAnalysisTracePanel } from './CaseAnalysisTracePanel';
import { ErrorMessage } from '../../components/ErrorMessage';
import { missingRequirementsText, outOfDateMessage } from './caseSections';

const CATEGORY_LABELS: Record<string, string> = {
  identity_entity_conflict: 'Identity conflict',
  cross_document_variance: 'Documents differ',
  behavioural_anomaly: 'Unusual behaviour',
  unsupported_claim: 'Unsupported claim',
};

const SEVERITY_RANK: Record<string, number> = { high: 3, medium: 2, low: 1 };

// "Run case analysis": a structured summary, cross-document findings, and every earlier run to open read-only.
export function CaseAnalysisCard({
  caseId,
  analysisReady,
  overview,
  missingRequirements = 0,
}: {
  caseId: string;
  analysisReady: boolean;
  overview: CaseAnalysisOverview | null;
  // How many required checklist documents are not yet on file: the analysis waits for every one.
  missingRequirements?: number;
}) {
  const [analysis, setAnalysis] = useState<CaseAnalysis | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [runs, setRuns] = useState<CaseAnalysisRunItem[]>([]);
  // An earlier run opened read-only; the latest is shown when null.
  const [viewing, setViewing] = useState<CaseAnalysis | null>(null);
  const [openingRunId, setOpeningRunId] = useState<string | null>(null);

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

  const loadRuns = useCallback(async () => {
    try {
      setRuns(await listCaseAnalysisRuns(caseId));
    } catch {
      // Only the list of earlier runs is missing; the latest result still shows.
    }
  }, [caseId]);

  async function openRun(runId: string | null) {
    setError(null);
    if (runId === null) {
      setViewing(null);
      return;
    }
    setOpeningRunId(runId);
    try {
      setViewing(await getCaseAnalysisRun(caseId, runId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to load that analysis');
    } finally {
      setOpeningRunId(null);
    }
  }

  // The case snapshot carries the overview over the socket, so a change in it means the result moved. Each
  // snapshot is a new object, so the effect watches these fields, not the object.
  const hasOverview = overview !== null;
  const runId = overview?.run_id;
  const runStatus = overview?.status;
  const stale = overview?.stale;
  useEffect(() => {
    if (hasOverview) {
      void load();
      void loadRuns();
    } else {
      setAnalysis(null);
      setRuns([]);
    }
  }, [hasOverview, runId, runStatus, stale, load, loadRuns]);

  // A new run is the card's result again.
  useEffect(() => {
    setViewing(null);
  }, [runId]);

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

  // What the card shows: an earlier run opened read-only, or the latest.
  const shown = viewing ?? analysis;
  const findings = [...(shown?.findings ?? [])].sort(
    (a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity],
  );
  const showResult = shown?.status === 'complete';

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
            <p>
              {missingRequirements
                ? `Every required document must be approved first: ${missingRequirementsText(missingRequirements)}.`
                : 'The analysis can be run once every approved document has been scanned.'}
            </p>
          </div>
        </div>
      )}

      {error && <ErrorMessage className="card-error" message={error} />}

      {viewing && (
        <div className="summary-message summary-message--info">
          <span className="summary-message-icon">ⓘ</span>
          <div>
            <strong>
              Earlier run from{' '}
              {new Date(viewing.started_at).toLocaleString('en-SG', {
                day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit',
              })}
              , read-only.
            </strong>
            <p>
              It reflects the documents as they were then.{' '}
              <button type="button" className="text-button" onClick={() => void openRun(null)}>
                Back to the latest
              </button>
            </p>
          </div>
        </div>
      )}

      {running && (
        <div className="summary-message summary-message--info" role="status">
          <span className="analysis-spinner" aria-hidden="true" />
          <div>
            <strong>Analysing this case…</strong>
            <p>The documents are being compared and the case scored. This can take a while, and it carries on if you leave this page.</p>
          </div>
        </div>
      )}

      {!viewing && !running && overview?.stale && (
        <div className="summary-message summary-message--warning">
          <span className="summary-message-icon">⚠</span>
          <div>
            <strong>Out of date.</strong>
            <p title={outOfDateMessage(overview).waitingList || undefined}>{outOfDateMessage(overview).detail}</p>
            <p>The risk score and recommendations below are from before the documents changed.</p>
          </div>
        </div>
      )}

      {!viewing && analysis?.status === 'failed' && (
        <div className="summary-message summary-message--warning">
          <span className="summary-message-icon">⚠</span>
          <div>
            <strong>The analysis did not finish.</strong>
            <p>Run it again.</p>
          </div>
        </div>
      )}

      {showResult && shown && (
        <>
          <AnalysisSummaryBlock analysis={shown} linkToRiskTab={!viewing} />

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
                <li key={finding.id} id={analysisFindingAnchor(finding.id)}>
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
        </>
      )}

      {runs.length > 1 && (
        <PreviousRuns
          runs={runs}
          viewingRunId={viewing?.run_id ?? openingRunId}
          onOpen={(id) => void openRun(id)}
        />
      )}
    </section>
    <CaseAnalysisTracePanel analysis={analysis} isRefreshing={isRefreshing} onRefresh={load} />
    </>
  );
}
