import type { CaseAnalysisOverview, CaseRisk, DocumentCaseMatch, DocumentRecord } from '../../services/uploadService';
import { formatDocumentType } from '../../services/uploadService';
import { Rate } from '../../components/Rate';
import { RATE_MEANING, formatCheck } from '../../utils/checkLabels';
import { isEligibleForApproval } from './uploadHelpers';
import { useFindingReviews } from './useFindingReviews';
import type { CaseSection } from './caseSections';
import { formatUsd, processingTimes } from './processingTimes';
import { useCaseCost } from './useCaseCost';
import { CaseOverviewSkeleton } from '../../components/Skeletons';

const RISK_LEVELS = ['Low', 'Medium', 'High', 'Critical'] as const;

function riskTone(level: string | null | undefined): string {
  return level ? level.toLowerCase() : 'unknown';
}

function documentStatus(document: DocumentRecord): { label: string; tone: string } {
  if (document.status === 'detected') return { label: 'Detection complete', tone: 'done' };
  if (document.status === 'detecting' || document.status === 'approved') return { label: 'Detection in progress', tone: 'progress' };
  if (document.status === 'processing') return { label: 'Processing', tone: 'progress' };
  if (document.status === 'rejected') return { label: 'Rejected', tone: 'muted' };
  if (document.routing_status === 'manual_review') return { label: 'Needs review', tone: 'action' };
  if (document.status === 'analyzed') return { label: 'Ready for approval', tone: 'action' };
  return { label: document.status.replace(/_/g, ' '), tone: 'muted' };
}

function dateLabel(value: string | null | undefined): string {
  if (!value) return 'Date unavailable';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? 'Date unavailable'
    : date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

function Stat({ label, value, detail, tone = 'blue', onClick }: { label: string; value: string | number; detail: string; tone?: string; onClick?: () => void }) {
  const className = `case-overview-stat case-overview-stat--${tone}${onClick ? ' case-overview-stat--interactive' : ''}`;
  const content = <>
      <span className="case-overview-stat-label">{label}</span>
      <strong className="case-overview-stat-value">{value}</strong>
      <span className="case-overview-stat-detail">{detail}</span>
    </>;
  return onClick
    ? <button className={className} type="button" onClick={onClick} aria-label={`${label}: ${value}. ${detail}. Open documents.`}>{content}</button>
    : <article className={className}>{content}</article>;
}

export function CaseOverview({
  documents,
  flaggedDocuments,
  approvedDocuments,
  caseMatches,
  risk,
  analysisReady,
  analysisOverview,
  snapshotLoaded,
  caseId,
  onSelectSection,
}: {
  documents: DocumentRecord[];
  flaggedDocuments: DocumentRecord[];
  approvedDocuments: DocumentRecord[];
  caseMatches: Map<string, DocumentCaseMatch>;
  risk: CaseRisk | null;
  analysisReady: boolean;
  analysisOverview: CaseAnalysisOverview | null;
  snapshotLoaded: boolean;
  caseId: string;
  onSelectSection: (section: CaseSection) => void;
}) {
  const scored = risk?.state === 'scored' && risk.level;
  // The officer's reviews of this case's findings (Risk Summary), as precision and recall.
  const accuracy = useFindingReviews(caseId, true).data;
  const reviewed = accuracy ? accuracy.case.correct + accuracy.case.wrong + accuracy.case.missed : 0;
  const checksByReviews = [...(accuracy?.case.by_check ?? [])]
    .sort((a, b) => b.correct + b.wrong + b.missed - (a.correct + a.wrong + a.missed))
    .slice(0, 4);
  const reviewCount = flaggedDocuments.length;
  const awaitingApprovalCount = documents.filter((document) => isEligibleForApproval(document, caseMatches)).length;
  const findingCount = risk?.contributing_findings.length ?? 0;
  const findings = [...(risk?.contributing_findings ?? [])]
    .sort((a, b) => ({ high: 0, medium: 1, low: 2, info: 3 }[a.severity] - ({ high: 0, medium: 1, low: 2, info: 3 }[b.severity])))
    .slice(0, 4);
  const recentDocuments = [...documents].reverse().slice(0, 5);
  const detectedCount = documents.filter((document) => document.status === 'detected').length;
  const analysisPhase = !analysisOverview
    ? analysisReady
      ? { label: 'Ready to run', tone: 'action' }
      : { label: 'Waiting for document checks', tone: 'idle' }
    : analysisOverview.status === 'running'
      ? { label: 'Analysis running', tone: 'progress' }
      : analysisOverview.status === 'failed'
        ? { label: 'Analysis failed', tone: 'error' }
        : analysisOverview.stale || analysisOverview.status === 'superseded'
          ? { label: 'Out of date · rerun needed', tone: 'action' }
          : { label: `${analysisOverview.finding_count} findings · up to date`, tone: 'done' };
  const progressStages = [
    { label: 'Documents Uploaded', detail: `${documents.length} ${documents.length === 1 ? 'document' : 'documents'}`, tone: documents.length ? 'done' : 'idle' },
    { label: 'Officer Approval', detail: awaitingApprovalCount ? `${awaitingApprovalCount} eligible to approve` : `${approvedDocuments.length} of ${documents.length} approved`, tone: awaitingApprovalCount ? 'action' : documents.length > 0 && approvedDocuments.length === documents.length ? 'done' : 'idle' },
    { label: 'Fraud Detection', detail: `${detectedCount} of ${documents.length} complete`, tone: documents.length > 0 && detectedCount === documents.length ? 'done' : risk?.state === 'pending' ? 'progress' : 'idle' },
    { label: 'Case Analysis', detail: analysisPhase.label, tone: analysisPhase.tone },
  ];
  const { cost, error: timingError, loading: timingLoading } = useCaseCost(caseId);
  const timings = processingTimes(cost?.timings);
  return (
    <main className="case-overview" aria-label="Case summary dashboard">
      {!snapshotLoaded ? (
        <CaseOverviewSkeleton />
      ) : (
        <>
          <section className="case-overview-topline" aria-label="Case risk and progress">
            <article className={`case-overview-risk case-overview-risk--${riskTone(risk?.level)}`}>
              <div className="case-overview-risk-copy">
                <span className="case-overview-stat-label">Overall Case Severity</span>
                <strong>{scored ? `${risk.level} risk` : risk?.state === 'pending' ? 'Assessment in progress' : 'Not scored yet'}</strong>
                <span>{scored ? 'Based on completed document checks' : risk?.state === 'failed' ? 'A document check failed to complete' : 'Run case analysis after documents are approved and scanned'}</span>
              </div>
              {scored && risk.score != null && (
                <div className="case-overview-score" aria-label={`Risk score ${risk.score} out of 100`}>
                  <strong>{risk.score}</strong><span>/100</span>
                </div>
              )}
            </article>
            <div className="case-overview-stats">
              <Stat label="Documents" value={documents.length} detail={`${approvedDocuments.length} approved`} />
              <Stat label="Awaiting Approval" value={awaitingApprovalCount} detail="Eligible to approve" tone="blue" onClick={() => onSelectSection('documents')} />
              <Stat label="Needs Attention" value={reviewCount} detail={reviewCount ? 'Review before approval' : 'No documents waiting'} tone={reviewCount ? 'amber' : 'green'} />
              <Stat label="Findings" value={findingCount} detail={risk?.unassessed_checks.length ? `${risk.unassessed_checks.length} checks unassessed` : 'From completed checks'} tone={findingCount ? 'red' : 'green'} />
            </div>
          </section>

          <section className="case-overview-grid">
            <article className="case-overview-panel case-overview-progress">
              <div className="case-overview-panel-heading">
                <div><h2>Case Progress</h2><p>Document review and analysis status</p></div>
                <span className={`case-overview-pill case-overview-pill--${analysisReady ? 'green' : 'blue'}`}>
                  {analysisReady ? 'Analysis Ready' : 'In Progress'}
                </span>
              </div>
              <div className="case-overview-progress-track" aria-label={`${detectedCount} of ${documents.length} documents detected`}>
                <span style={{ width: `${documents.length ? (detectedCount / documents.length) * 100 : 0}%` }} />
              </div>
              <div className="case-overview-progress-labels"><span>{detectedCount} of {documents.length} documents detected</span><span>{approvedDocuments.length} approved</span></div>
              <ol className="case-overview-stages">
                {progressStages.map((stage) => (
                  <li className={`case-overview-stage case-overview-stage--${stage.tone}`} key={stage.label}>
                    <span className="case-overview-stage-marker" aria-hidden="true">{stage.tone === 'done' ? '✓' : ''}</span>
                    <strong>{stage.label}</strong>
                    <span>{stage.detail}</span>
                  </li>
                ))}
              </ol>
            </article>

            <article className="case-overview-panel case-overview-findings">
              <div className="case-overview-panel-heading">
                <div><h2>Key Findings</h2><p>Highest-priority signals from this case</p></div>
                {findingCount > 0 && <button type="button" className="case-overview-text-button" onClick={() => onSelectSection('risk')}>View Risk Details →</button>}
              </div>
              {findings.length ? (
                <ul className="case-overview-finding-list">
                  {findings.map((finding, index) => (
                    <li key={`${finding.document_id}-${finding.finding_id}-${index}`}>
                      <span className={`case-overview-severity case-overview-severity--${finding.severity}`}>{finding.severity}</span>
                      <span className="case-overview-finding-copy"><strong>{finding.summary}</strong><small>{finding.filename}</small></span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="case-overview-empty"><span aria-hidden="true">✓</span><div><strong>{risk?.state === 'scored' ? 'No findings in completed checks' : 'No findings available yet'}</strong><p>{risk?.state === 'scored' ? 'Completed checks did not return findings for this case.' : 'Findings appear here after document detection and case analysis.'}</p></div></div>
              )}
            </article>
          </section>

          <section className="case-overview-metrics" aria-label="Processing and classification metrics">
            <article className="case-overview-panel case-overview-timing">
              <div className="case-overview-panel-heading">
                <div><h2>Processing Time</h2><p>How long each stage took on this case</p></div>
              </div>
              <div className="case-overview-timing-values">
                {timings.map((metric) => (
                  <div key={metric.label}>
                    <span>{metric.label}<small>{metric.detail}</small></span>
                    <strong>{timingLoading ? '…' : metric.value}</strong>
                  </div>
                ))}
              </div>
              <p className="case-overview-metric-note">
                {timingError ? "Couldn't load the timings." : 'Time the work took, from the audit trail; waiting in the queue is not counted.'}
              </p>
            </article>

            <article className="case-overview-panel case-overview-cost">
              <div className="case-overview-panel-heading">
                <div><h2>Claude Cost</h2><p>What this case's runs cost at list prices</p></div>
              </div>
              <div className="case-overview-timing-values">
                <div><span>Total</span><strong>{cost ? formatUsd(cost.total_usd) : '…'}</strong></div>
                <div><span>Documents</span><strong>{cost ? formatUsd(cost.total_usd - cost.case_analysis_usd) : '…'}</strong></div>
                <div><span>Case analysis</span><strong>{cost ? formatUsd(cost.case_analysis_usd) : '…'}</strong></div>
              </div>
              <p className="case-overview-metric-note">
                {timingError ? "Couldn't load the cost." : "From each run's record in the audit trail; runs from before cost was recorded aren't included."}
              </p>
            </article>

            <article className="case-overview-panel case-overview-classification">
              <div className="case-overview-panel-heading">
                <div><h2>Detection Accuracy</h2><p>How often this case's findings were right, from the officer's review</p></div>
                <button type="button" className="case-overview-text-button" onClick={() => onSelectSection('risk')}>Review findings →</button>
              </div>
              <div className="case-overview-accuracy">
                <span title={RATE_MEANING.precision}>Precision <strong><Rate rate={accuracy?.case.precision ?? null} kind="precision" /></strong></span>
                <span title={RATE_MEANING.recall}>Recall <strong><Rate rate={accuracy?.case.recall ?? null} kind="recall" /></strong></span>
              </div>
              {checksByReviews.length > 0 && (
                <div className="case-overview-classification-grid">
                  {checksByReviews.map((check) => (
                    <div className="case-overview-classification-card case-overview-classification-card--check" key={`${check.scope}-${check.check_name}`}>
                      <strong>{formatCheck(check.check_name)}</strong>
                      <span>Precision <b><Rate rate={check.precision} kind="precision" /></b></span>
                      <span>Recall <b><Rate rate={check.recall} kind="recall" /></b></span>
                    </div>
                  ))}
                </div>
              )}
              <p className="case-overview-metric-note">
                {reviewed === 0
                  ? 'No findings reviewed yet. In Risk Summary, tick the real issues for each document, save its review, and report anything detection missed.'
                  : `${accuracy!.case.correct} correct · ${accuracy!.case.wrong} wrong · ${accuracy!.case.missed} missed`}
              </p>
            </article>
          </section>

          <section className="case-overview-panel case-overview-documents">
            <div className="case-overview-panel-heading">
              <div><h2>Recent Documents</h2><p>Latest uploads and their review status</p></div>
              <button type="button" className="case-overview-text-button" onClick={() => onSelectSection('documents')}>Open Document List →</button>
            </div>
            {recentDocuments.length ? (
              <ul className="case-overview-document-list">
                {recentDocuments.map((document) => {
                  const status = documentStatus(document);
                  return <li key={document.id}><span className="case-overview-document-icon" aria-hidden="true">▤</span><span className="case-overview-document-copy"><strong>{document.filename}</strong><small>{document.document_type ? formatDocumentType(document.document_type) : 'Type not classified'} · {dateLabel(document.uploaded_at)}</small></span><span className={`case-overview-document-status case-overview-document-status--${status.tone}`}>{status.label}</span></li>;
                })}
              </ul>
            ) : (
              <div className="case-overview-empty"><span aria-hidden="true">＋</span><div><strong>No documents uploaded</strong><p>Uploaded documents will appear here with their current review status.</p></div></div>
            )}
          </section>
        </>
      )}
    </main>
  );
}
