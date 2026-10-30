import type { CaseAnalysisOverview, CaseRisk, CaseRiskFinding, DocumentCaseMatch, DocumentRecord } from '../../services/uploadService';
import { formatDocumentType } from '../../services/uploadService';
import { Rate } from '../../components/Rate';
import { RATE_MEANING, belowTargetNote, formatCheck } from '../../utils/checkLabels';
import { InfoLabel } from '../../components/InfoTip';
import { isEligibleForApproval } from './uploadHelpers';
import { findingScope, useFindingReviews } from './useFindingReviews';
import type { CaseSection } from './caseSections';
import { formatUsd, processingTimes } from './processingTimes';
import { useCaseCost } from './useCaseCost';
import { CaseOverviewSkeleton } from '../../components/Skeletons';
import { useFindingReviewsOn } from '../../lib/appSettings';

const SEVERITY_RANK: Record<string, number> = { high: 0, medium: 1, low: 2, info: 3 };
const SHOWN_SEVERITIES = ['high', 'medium', 'low'] as const;
const FLAGGED_DOCUMENTS_SHOWN = 5;

interface FlaggedDocument {
  key: string;
  filename: string;
  documentType: string | null;
  // Most severe first, then most points.
  findings: CaseRiskFinding[];
  points: number;
}

function compareFindings(a: CaseRiskFinding, b: CaseRiskFinding): number {
  return (SEVERITY_RANK[a.severity] ?? 4) - (SEVERITY_RANK[b.severity] ?? 4) || b.points - a.points;
}

// The case's findings by document (cross-document ones as a group of their own), worst document first: by its
// most severe finding, then its total points. The same order as the Risk Summary's list.
function documentsByWorstFinding(findings: CaseRiskFinding[]): FlaggedDocument[] {
  const groups = new Map<string, FlaggedDocument>();
  for (const finding of [...findings].sort(compareFindings)) {
    const crossDocument = findingScope(finding) === 'case';
    const key = crossDocument ? 'case' : finding.document_id;
    const group = groups.get(key) ?? {
      key,
      filename: crossDocument ? 'Across documents' : finding.filename,
      documentType: crossDocument ? null : finding.document_type,
      findings: [],
      points: 0,
    };
    group.findings.push(finding);
    group.points += finding.points;
    groups.set(key, group);
  }
  return [...groups.values()].sort((a, b) => compareFindings(a.findings[0], b.findings[0]) || b.points - a.points);
}

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

function fileExtension(filename: string): string {
  const dot = filename.lastIndexOf('.');
  return dot > 0 ? filename.slice(dot + 1, dot + 5).toUpperCase() : 'FILE';
}

function Stat({ label, value, detail, tone = 'blue', onClick }: { label: string; value: string | number; detail?: string; tone?: string; onClick?: () => void }) {
  const className = `case-overview-stat case-overview-stat--${tone}${onClick ? ' case-overview-stat--interactive' : ''}`;
  const content = <>
      <span className="case-overview-stat-label">{label}{onClick && <span className="case-overview-stat-arrow" aria-hidden="true">→</span>}</span>
      <strong className="case-overview-stat-value">{value}</strong>
      {detail && <span className="case-overview-stat-detail">{detail}</span>}
    </>;
  return onClick
    ? <button className={className} type="button" onClick={onClick} aria-label={`${label}: ${value}.${detail ? ` ${detail}.` : ''} Open documents.`}>{content}</button>
    : <article className={className}>{content}</article>;
}

export function CaseOverview({
  documents,
  flaggedDocuments,
  approvedDocuments,
  caseMatches,
  risk,
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
  analysisOverview: CaseAnalysisOverview | null;
  snapshotLoaded: boolean;
  caseId: string;
  onSelectSection: (section: CaseSection) => void;
}) {
  const scored = risk?.state === 'scored' && risk.level;
  // The officer's reviews of this case's findings (Risk Summary), as precision and recall. An admin can turn
  // reviews off (Settings): then there is no Detection Accuracy panel and no Review Findings step.
  const reviewsOn = useFindingReviewsOn();
  const accuracy = useFindingReviews(caseId, reviewsOn).data;
  const reviewed = accuracy ? accuracy.case.correct + accuracy.case.wrong + accuracy.case.missed : 0;
  const checksByReviews = [...(accuracy?.case.by_check ?? [])]
    .sort((a, b) => b.correct + b.wrong + b.missed - (a.correct + a.wrong + a.missed))
    .slice(0, 4);
  const reviewCount = flaggedDocuments.length;
  const awaitingApprovalCount = documents.filter((document) => isEligibleForApproval(document, caseMatches)).length;
  const findingCount = risk?.contributing_findings.length ?? 0;
  const documentsWithFindings = documentsByWorstFinding(risk?.contributing_findings ?? []);
  const worstDocuments = documentsWithFindings.slice(0, FLAGGED_DOCUMENTS_SHOWN);
  const recentDocuments = [...documents].reverse().slice(0, 5);
  // The accuracy is from the latest analysis's findings; an out-of-date run (a document added since) may change them.
  const analysisStale = Boolean(analysisOverview && (analysisOverview.stale || analysisOverview.status === 'superseded'));
  const { cost, error: timingError, loading: timingLoading } = useCaseCost(caseId);
  const timings = processingTimes(cost?.timings);
  return (
    <main className="case-overview" aria-label="Case summary dashboard">
      {!snapshotLoaded ? (
        <CaseOverviewSkeleton />
      ) : (
        <>
          <section className="case-overview-topline" aria-label="Case risk and document counts">
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
              <Stat label="Documents" value={approvedDocuments.length} detail={`of ${documents.length} approved`} />
              <Stat label="Awaiting Approval" value={awaitingApprovalCount} detail="Eligible to approve" tone="blue" onClick={() => onSelectSection('documents')} />
              <Stat label="Needs Attention" value={reviewCount} detail={reviewCount ? 'Review before approval' : 'No documents waiting'} tone={reviewCount ? 'amber' : 'green'} />
              <Stat label="Findings" value={findingCount} detail={risk?.unassessed_checks.length ? `${risk.unassessed_checks.length} checks unassessed` : 'From completed checks'} tone={findingCount ? 'red' : 'green'} />
            </div>
          </section>

          <section
            className={`case-overview-metrics${reviewsOn ? '' : ' case-overview-metrics--no-accuracy'}`}
            aria-label="Processing and classification metrics"
          >
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

            {reviewsOn && (
            <article className="case-overview-panel case-overview-classification">
              <div className="case-overview-panel-heading">
                <div>
                  <h2>Detection Accuracy</h2>
                  <p>Based on the officer's latest reviews of this case's findings</p>
                  {/* The reviews are of the last run's findings; a rerun over the newer documents may change them. */}
                  {reviewed > 0 && analysisStale && <p className="case-overview-accuracy-stale">From before the latest documents · rerun the analysis to update</p>}
                </div>
                {reviewed > 0 && <button type="button" className="case-overview-text-button" onClick={() => onSelectSection('risk')}>Review findings →</button>}
              </div>
              {reviewed === 0 && (
                <div className="case-overview-accuracy-empty">
                  <span className="case-overview-accuracy-empty-icon" aria-hidden="true">✓</span>
                  <strong>No findings reviewed yet</strong>
                </div>
              )}
              {reviewed > 0 && (
                <table className="case-overview-classification-table">
                  <thead><tr><th scope="col">Check</th><th scope="col"><InfoLabel label="Precision" tip={RATE_MEANING.precision} /></th><th scope="col"><InfoLabel label="Recall" tip={RATE_MEANING.recall} /></th></tr></thead>
                  <tbody>
                    {checksByReviews.map((check) => (
                      <tr key={`${check.scope}-${check.check_name}`}>
                        <th scope="row">{formatCheck(check.check_name)}</th>
                        <td><Rate rate={check.precision} kind="precision" target={accuracy?.target_precision} /></td>
                        <td><Rate rate={check.recall} kind="recall" target={accuracy?.target_recall} /></td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <th scope="row">All checks</th>
                      <td><Rate rate={accuracy?.case.precision ?? null} kind="precision" target={accuracy?.target_precision} /></td>
                      <td><Rate rate={accuracy?.case.recall ?? null} kind="recall" target={accuracy?.target_recall} /></td>
                    </tr>
                  </tfoot>
                </table>
              )}
              {reviewed > 0 && (
                <p className="case-overview-metric-note">
                  {accuracy!.case.correct} correct · {accuracy!.case.wrong} wrong · {accuracy!.case.missed} missed
                  <br />
                  {belowTargetNote(accuracy!.target_precision, accuracy!.target_recall)}
                </p>
              )}
            </article>
            )}
          </section>

          <section className="case-overview-grid">
            <article className="case-overview-panel case-overview-documents">
              <div className="case-overview-panel-heading">
                <div><h2>Recent Documents</h2><p>Latest uploads and their review status</p></div>
                <button type="button" className="case-overview-text-button" onClick={() => onSelectSection('documents')}>Open Document List →</button>
              </div>
              {recentDocuments.length ? (
                <ul className="case-overview-document-list">
                  {recentDocuments.map((document) => {
                    const status = documentStatus(document);
                    return <li key={document.id}><span className="case-overview-document-icon" aria-hidden="true">{fileExtension(document.filename)}</span><span className="case-overview-document-copy"><strong>{document.filename}</strong><small>{document.document_type ? formatDocumentType(document.document_type) : 'Type not classified'} · {dateLabel(document.uploaded_at)}</small></span><span className={`case-overview-document-status case-overview-document-status--${status.tone}`}>{status.label}</span></li>;
                  })}
                </ul>
              ) : (
                <div className="case-overview-empty"><span aria-hidden="true">＋</span><div><strong>No documents uploaded</strong><p>Uploaded documents will appear here with their current review status.</p></div></div>
              )}
            </article>

            <article className="case-overview-panel case-overview-findings">
              <div className="case-overview-panel-heading">
                <div><h2>Key Findings</h2><p>Documents with the most severe findings, worst first</p></div>
                {findingCount > 0 && <button type="button" className="case-overview-text-button" onClick={() => onSelectSection('risk')}>View Risk Details →</button>}
              </div>
              {worstDocuments.length ? (
                <>
                  <ul className="case-overview-flagged-list">
                    {worstDocuments.map((document) => (
                      <li key={document.key}>
                        <button type="button" className="case-overview-flagged-document" onClick={() => onSelectSection('risk')}>
                          <span className="case-overview-flagged-copy">
                            <strong>{document.filename}</strong>
                            <span className="case-overview-flagged-meta">
                              <span>{document.documentType ? formatDocumentType(document.documentType) : 'Cross-document findings'}</span>
                              {SHOWN_SEVERITIES.map((severity) => {
                                const count = document.findings.filter((finding) => finding.severity === severity).length;
                                return count > 0 && (
                                  <span key={severity} className={`case-overview-severity case-overview-severity--${severity}`}>{count} {severity}</span>
                                );
                              })}
                            </span>
                          </span>
                          <span className="case-overview-flagged-chevron" aria-hidden="true">›</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                  {documentsWithFindings.length > worstDocuments.length && (
                    <p className="case-overview-flagged-more">
                      +{documentsWithFindings.length - worstDocuments.length} more {documentsWithFindings.length - worstDocuments.length === 1 ? 'document' : 'documents'} with findings in Risk Summary
                    </p>
                  )}
                </>
              ) : (
                <div className="case-overview-empty"><span aria-hidden="true">✓</span><div><strong>{risk?.state === 'scored' ? 'No findings in completed checks' : 'No findings available yet'}</strong><p>{risk?.state === 'scored' ? 'Completed checks did not return findings for this case.' : 'Findings appear here after document detection and case analysis.'}</p></div></div>
              )}
            </article>
          </section>
        </>
      )}
    </main>
  );
}
