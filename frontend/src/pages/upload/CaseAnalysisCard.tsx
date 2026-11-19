import { useCallback, useEffect, useState } from 'react';
import type { AdditionalDocument, CaseAnalysis, CaseAnalysisOverview, CaseRisk } from '../../services/uploadService';
import type { CaseAnalysisRunItem } from '../../services/uploadService';
import { REQUIRED_DOCUMENT_TYPES, capitaliseDocumentTypes, getCaseAnalysis, getCaseAnalysisRun, listCaseAnalysisRuns, runCaseAnalysis } from '../../services/uploadService';
import { AnalysisSummaryBlock } from './AnalysisSummaryBlock';
import { analysisFindingAnchor } from './analysisSummary';
import { PreviousRuns } from './PreviousRuns';
import { AgentTraceView } from '../../components/agentTrace/AgentTraceView';
import { ErrorMessage } from '../../components/ErrorMessage';
import { outOfDateMessage } from './caseSections';
import { Icon } from '../../components/Icon';
import type { ReactNode } from 'react';

const CATEGORY_LABELS: Record<string, string> = {
  identity_entity_conflict: 'Identity Conflict',
  cross_document_variance: 'Documents Differ',
  behavioural_anomaly: 'Unusual Behaviour',
  unsupported_claim: 'Unsupported Claim',
};

const SEVERITY_RANK: Record<string, number> = { high: 3, medium: 2, low: 1 };

// The Overview tab's one card, Case Risk: the score, then why it is that score (the case analysis's summary), then
// every earlier run to open read-only (with its cross-document findings), and the run's trace. The analysis starts
// on its own once every approved document is scanned; Run again at the top is the fallback. The latest run's findings are on the Findings tab. The button is the fallback
// for a failed run, or one that didn't start automatically.
export function CaseAnalysisCard({
  caseId,
  analysisReady,
  overview,
  missingRequirements = 0,
  traceOpen,
  onTraceClose,
  score,
  findingsLink,
  suggestedDocuments = [],
}: {
  caseId: string;
  analysisReady: boolean;
  overview: CaseAnalysisOverview | null;
  // How many required checklist documents are not yet on file: the analysis waits for every one.
  missingRequirements?: number;
  traceOpen: boolean;
  onTraceClose: () => void;
  // The risk score (CaseRiskCard's score part), at the top of the card beside why it is that score: the case's
  // latest score, or, for an earlier run opened read-only, that run's own (its risk as it was then).
  score?: (runRisk?: CaseRisk | null) => ReactNode;
  // Under the summary: on to the findings it rests on. Not shown beside an earlier run.
  findingsLink?: ReactNode;
  // The documents the Additional Documents section suggests now, listed under the summary's "would help". Not shown
  // beside an earlier run.
  suggestedDocuments?: AdditionalDocument[];
}) {
  const [analysis, setAnalysis] = useState<CaseAnalysis | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [runs, setRuns] = useState<CaseAnalysisRunItem[]>([]);
  // An earlier run opened read-only; the latest is shown when null.
  const [viewing, setViewing] = useState<CaseAnalysis | null>(null);
  const [openingRunId, setOpeningRunId] = useState<string | null>(null);

  // The detail fetch failed, or found no analysis: the card shows what the overview has rather than loading for ever.
  const [detailFailed, setDetailFailed] = useState(false);

  const load = useCallback(async () => {
    setIsRefreshing(true);
    setDetailFailed(false);
    try {
      const loaded = await getCaseAnalysis(caseId);
      setAnalysis(loaded);
      // None to show (a 404): stop waiting for it, as for a failure.
      if (!loaded) setDetailFailed(true);
    } catch {
      // The card still shows the overview; a failed detail fetch isn't worth an error banner.
      setDetailFailed(true);
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

  useEffect(() => {
    if (traceOpen) void load();
  }, [traceOpen, load]);

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
  // The same layout for an earlier run: its own score on the left. One without a score has the summary alone.
  const waitingOnRequirements = !overview && !analysisReady && missingRequirements > 0;
  // A finished run whose summary hasn't arrived yet: a skeleton of the score-and-summary layout holds its place, so
  // the score doesn't show alone and then jump aside when the summary lands.
  const detailLoading =
    !viewing && overview?.status === 'complete' && analysis?.run_id !== overview.run_id && !detailFailed;
  const scoreShown = waitingOnRequirements
    ? null
    : score
      ? viewing
        ? viewing.risk
          ? score(viewing.risk as CaseRisk)
          : null
        : score()
      : null;

  return (
    <>
    <section className="summary-card">
      <div className="summary-card-header">
        <div className="summary-card-title">
          <span className="summary-icon summary-icon--risk"><Icon name="risk" size={20} /></span>
          <h2>Case Risk Summary</h2>
        </div>
        {analysisReady && (
          <button
            type="button"
            className="analysis-action"
            disabled={running}
            onClick={() => void start()}
          >
            {running ? 'Analysing…' : overview ? 'Run again' : 'Run case analysis'}
          </button>
        )}
      </div>

      {/* Never analysed: one message saying what the score waits for. Required documents still to approve come
          first; otherwise the score's own message (below) says how far scanning has got. */}
      {waitingOnRequirements && (
        <div className="summary-message summary-message--info">
          <span className="summary-message-icon"><Icon name="info" size={18} /></span>
          <div>
            <strong>Not scored yet.</strong>
            <p>
              {missingRequirements} of the {REQUIRED_DOCUMENT_TYPES.length} required documents still{' '}
              {missingRequirements === 1 ? 'needs' : 'need'} approving. The case is analysed and scored on its own
              once they are all approved and scanned.
            </p>
          </div>
        </div>
      )}

      {error && <ErrorMessage className="card-error" message={error} />}

      {viewing && (
        <div className="summary-message summary-message--info">
          <span className="summary-message-icon"><Icon name="info" size={18} /></span>
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
          <span className="summary-message-icon"><Icon name="alert" size={18} /></span>
          <div>
            <strong>Out of date.</strong>
            <p title={outOfDateMessage(overview).waitingList || undefined}>{outOfDateMessage(overview).detail}</p>
            <p>The risk score and the summary here are from before the documents changed.</p>
          </div>
        </div>
      )}

      {!viewing && analysis?.status === 'failed' && (
        <div className="summary-message summary-message--warning">
          <span className="summary-message-icon"><Icon name="alert" size={18} /></span>
          <div>
            <strong>The analysis did not finish.</strong>
            <p>Run it again.</p>
          </div>
        </div>
      )}

      {/* The score on the left, why it is that score on the right, what else was checked under both. */}
      {detailLoading && (
        <div className="risk-overview risk-overview--loading" role="status">
          <span className="sr-only">Loading the case risk summary…</span>
          <div className="risk-overview-score" aria-hidden="true">
            <span className="skeleton-gauge" />
            <span className="skeleton-bar skeleton-bar--level" />
            <span className="skeleton-bar skeleton-bar--facts" />
          </div>
          <div className="analysis-brief-why" aria-hidden="true">
            <span className="skeleton-bar skeleton-bar--headline" />
            <span className="skeleton-bar skeleton-bar--reason" />
            <span className="skeleton-bar skeleton-bar--reason skeleton-bar--short" />
          </div>
        </div>
      )}

      {!detailLoading && (scoreShown || (showResult && shown)) && (
        // Two columns only when there is both a score and a summary to sit beside it.
        <div className={`risk-overview${scoreShown && showResult && shown ? '' : ' risk-overview--single'}`}>
          {scoreShown && <div className="risk-overview-score">{scoreShown}</div>}
          {showResult && shown && (
            <AnalysisSummaryBlock
              analysis={shown}
              linkToRiskTab={!viewing}
              footer={!viewing && findingsLink}
              suggestedDocuments={viewing ? [] : suggestedDocuments}
            />
          )}
        </div>
      )}

      {showResult && shown && (
        <>

          {/* The latest run's cross-document findings are on the Findings tab, with the rest, to review; an earlier
              run's are listed here, as they were then. */}
          {viewing && (findings.length === 0 ? (
            <div className="summary-message summary-message--success">
              <span className="summary-message-icon"><Icon name="check" size={18} /></span>
              <div>
                <strong>No cross-document findings.</strong>
                <p>Nothing was proven across this case's documents.</p>
              </div>
            </div>
          ) : (
            <>
            <h3 className="analysis-heading analysis-heading--findings">Cross-document findings</h3>
            <ul className="risk-findings">
              {findings.map((finding) => (
                <li key={finding.id} id={analysisFindingAnchor(finding.id)}>
                  <span className={`risk-severity risk-severity--${finding.severity}`}>{finding.severity}</span>
                  <span className="risk-finding-text">
                    <span className="risk-finding-summary">
                      <span className="analysis-category">
                        {CATEGORY_LABELS[finding.category] ?? finding.category}
                      </span>
                      {capitaliseDocumentTypes(finding.summary)}
                    </span>
                    <span className="risk-finding-rationale">{finding.rationale}</span>
                  </span>
                </li>
              ))}
            </ul>
            </>
          ))}
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
    {traceOpen && (
      <AgentTraceView
        title="Case analysis trace"
        steps={shown?.trace ?? null}
        emptyText={shown ? 'No steps recorded yet.' : 'No analysis has been run for this case yet.'}
        isRefreshing={isRefreshing}
        onRefresh={load}
        onClose={onTraceClose}
      />
    )}
    </>
  );
}
