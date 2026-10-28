import { useState } from 'react';
import { officerName } from '../../lib/officers';
import type { CaseRisk, CaseRiskFinding, DocumentRecord, FindingReview, ReviewScope } from '../../services/uploadService';
import { formatDocumentType } from '../../services/uploadService';
import { formatCheck as formatTool } from '../../utils/checkLabels';
import type { GroupReviewStatus } from './FindingReviewControls';
import { GroupReviewBar, MetricsLine, MissedIssues, ReviewSummary } from './FindingReviewControls';
import { findingScope, useFindingReviews, verdictFor } from './useFindingReviews';
import { ErrorMessage } from '../../components/ErrorMessage';

// The group cross-document findings sit in: they belong to the case, not to one document.
const CASE_GROUP_ID = '__case__';

type Severity = CaseRiskFinding['severity'];
const SEVERITY_ORDER: Severity[] = ['high', 'medium', 'low'];

function severityRank(severity: Severity): number {
  const index = SEVERITY_ORDER.indexOf(severity);
  return index === -1 ? SEVERITY_ORDER.length : index;
}

// Most severe first, then most points. Points alone would put a repeated high (scored at a quarter)
// below a medium.
function compareFindings(a: CaseRiskFinding, b: CaseRiskFinding): number {
  return severityRank(a.severity) - severityRank(b.severity) || b.points - a.points;
}

// The score range of each level; mirrors SCORE_BANDS in backend/app/case_pipeline/risk/case_risk.py.
const SCORE_BANDS = [
  { level: 'Low', min: 0, max: 24 },
  { level: 'Medium', min: 25, max: 49 },
  { level: 'High', min: 50, max: 79 },
  { level: 'Critical', min: 80, max: 100 },
] as const;

function contentWords(text: string): string[] {
  return text.toLowerCase().match(/[a-z0-9][a-z0-9.-]*[a-z0-9]|[a-z0-9]/g)?.filter((w) => w.length > 2 || /\d/.test(w)) ?? [];
}

// The rationale without the sentences that only restate the summary, so each row reads as title + what it adds.
function addedDetail(rationale: string, summary: string): string {
  const said = new Set(contentWords(summary));
  return rationale
    .split(/(?<=[.!?])\s+/)
    .filter((sentence) => {
      const words = contentWords(sentence);
      if (words.length === 0) return false;
      const repeated = words.filter((word) => said.has(word)).length;
      return repeated / words.length < 0.6;
    })
    .join(' ');
}

function ScoreGauge({ score, level }: { score: number; level: string }) {
  const band = SCORE_BANDS.find((b) => b.level === level);
  return (
    <div className="risk-score">
      <div className="risk-score-figure">
        <span className="risk-score-value">{score}</span>
        <span className="risk-score-scale">/100</span>
      </div>
      <div className="risk-score-detail">
        <span className={`risk-score-level risk-score-level--${level.toLowerCase()}`}>
          {level} risk{band ? ` · ${band.min}–${band.max}` : ''}
        </span>
        <div className="risk-gauge" role="img" aria-label={`Score ${score} out of 100, ${level} risk`}>
          {SCORE_BANDS.map((b) => (
            <span
              key={b.level}
              className={`risk-gauge-band risk-gauge-band--${b.level.toLowerCase()} ${b.level === level ? 'risk-gauge-band--current' : ''}`}
              style={{ flexGrow: b.max - b.min + 1 }}
              title={`${b.level}: ${b.min}–${b.max}`}
            />
          ))}
          <span className="risk-gauge-marker" style={{ left: `${Math.min(100, Math.max(0, score))}%` }} />
        </div>
        <div className="risk-gauge-labels" aria-hidden="true">
          {SCORE_BANDS.map((b) => (
            <span key={b.level} style={{ flexGrow: b.max - b.min + 1 }}>
              {b.level}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

interface FindingRow {
  finding: CaseRiskFinding;
  // How many findings on this document share this check and summary.
  count: number;
  // Those findings: a verdict on the row goes on each of them.
  members: CaseRiskFinding[];
}

interface DocumentGroup {
  // A document's id, or CASE_GROUP_ID for the cross-document findings.
  documentId: string;
  scope: ReviewScope;
  filename: string;
  documentType: string | null;
  findings: CaseRiskFinding[];
  rows: FindingRow[];
}

// Grouped per document (cross-document findings in a group of their own), most severe first (documents by their
// most severe finding, then total points); identical findings (same check, same summary) collapse into one row.
function groupByDocument(findings: CaseRiskFinding[]): DocumentGroup[] {
  const groups = new Map<string, DocumentGroup>();
  for (const finding of [...findings].sort(compareFindings)) {
    const scope = findingScope(finding);
    const key = scope === 'case' ? CASE_GROUP_ID : finding.document_id;
    let group = groups.get(key);
    if (!group) {
      group = {
        documentId: key,
        scope,
        filename: scope === 'case' ? 'Across documents' : finding.filename,
        documentType: scope === 'case' ? null : finding.document_type,
        findings: [],
        rows: [],
      };
      groups.set(key, group);
    }
    group.findings.push(finding);
    const repeat = group.rows.find(
      (row) => row.finding.tool === finding.tool && row.finding.summary === finding.summary,
    );
    if (repeat) {
      repeat.count += 1;
      repeat.members.push(finding);
    } else {
      group.rows.push({ finding, count: 1, members: [finding] });
    }
  }
  const total = (group: DocumentGroup) => group.findings.reduce((sum, f) => sum + f.points, 0);
  return Array.from(groups.values()).sort(
    (a, b) => compareFindings(a.findings[0], b.findings[0]) || total(b) - total(a),
  );
}

// Scanned documents with no findings, and the cross-document group, so an officer can still report an issue
// detection missed on them (recall needs the misses). `skip` holds the groups already shown or hidden as minor.
function emptyGroups(skip: Set<string>, documents: DocumentRecord[]): DocumentGroup[] {
  const groups: DocumentGroup[] = documents
    .filter((document) => document.status === 'detected' && !skip.has(document.id))
    .map((document) => ({
      documentId: document.id,
      scope: 'document' as const,
      filename: document.filename,
      documentType: document.document_type,
      findings: [],
      rows: [],
    }));
  if (!skip.has(CASE_GROUP_ID)) {
    groups.push({ documentId: CASE_GROUP_ID, scope: 'case', filename: 'Across documents', documentType: null, findings: [], rows: [] });
  }
  return groups;
}

function countBySeverity(findings: CaseRiskFinding[]): [Severity, number][] {
  return SEVERITY_ORDER.map(
    (severity): [Severity, number] => [severity, findings.filter((f) => f.severity === severity).length],
  ).filter(([, count]) => count > 0);
}

function reviewedOn(value: string): string {
  return new Date(value).toLocaleDateString('en-SG', { day: 'numeric', month: 'short', year: 'numeric' });
}

// `latest` is the group's most recently saved verdict: who last reviewed it, and when.
function ReviewTag({ status, latest }: { status: GroupReviewStatus; latest?: FindingReview }) {
  return (
    <span className={`risk-review-tag risk-review-tag--${status}`}>
      {status === 'saved'
        ? latest
          ? `✓ Reviewed by ${officerName(latest.reviewer)} · ${reviewedOn(latest.updated_at)}`
          : '✓ Reviewed'
        : status === 'unsaved'
          ? 'Unsaved changes'
          : 'Not reviewed'}
    </span>
  );
}

function NotScoredMessage({ risk }: { risk: CaseRisk | null }) {
  // The score only comes from a case analysis run, never computed on its own.
  const [title, body, tone] = !risk
    ? ['Not scored yet.', 'Run the case analysis once every approved document has been scanned.', 'info']
    : risk.state === 'no_documents'
      ? ['No documents to score yet.', 'A risk level appears once documents are uploaded, approved and scanned.', 'info']
      : risk.state === 'failed'
        ? ['Detection failed on a document.', 'Retry detection on the failed document to complete the risk score.', 'warning']
        : [
            `${risk.documents_scanned} of ${risk.documents_total} documents scanned.`,
            'The risk score is calculated once every document has been approved and scanned.',
            'info',
          ];

  return (
    <div className={`summary-message summary-message--${tone}`}>
      <span className="summary-message-icon">{tone === 'warning' ? '⚠' : 'ⓘ'}</span>
      <div>
        <strong>{title}</strong>
        <p>{body}</p>
      </div>
    </div>
  );
}

export function CaseRiskCard({
  risk,
  caseId,
  caseDocuments = [],
}: {
  risk: CaseRisk | null;
  caseId: string;
  caseDocuments?: DocumentRecord[];
}) {
  const scored = risk?.state === 'scored' && risk.level ? risk : null;
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // Ticks the officer changed but hasn't saved, by row key. A row without one shows its saved verdict.
  const [drafts, setDrafts] = useState<Record<string, boolean>>({});
  const reviews = useFindingReviews(caseId, scored !== null);
  const allReviews = reviews.data?.reviews ?? [];

  const findings = scored?.contributing_findings ?? [];
  // Every finding is listed, lows and ones a specialist didn't assess included: all of them count towards the
  // score, and all of them are the officer's to review.
  const documents = groupByDocument(findings);
  // Every group that has findings, shown or hidden as minor: only the rest are added as empty groups.
  const withFindings = new Set(
    findings.map((finding) => (findingScope(finding) === 'case' ? CASE_GROUP_ID : finding.document_id)),
  );
  const reviewGroups = emptyGroups(withFindings, caseDocuments);

  function metricsFor(group: DocumentGroup) {
    return group.scope === 'case' ? reviews.data?.cross_document : reviews.data?.documents[group.documentId];
  }

  function missedFor(group: DocumentGroup) {
    return allReviews.filter(
      (review) =>
        review.kind === 'missed' &&
        review.scope === group.scope &&
        (group.scope === 'case' || review.document_id === group.documentId),
    );
  }

  function toggleDocument(documentId: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(documentId)) {
        next.delete(documentId);
      } else {
        next.add(documentId);
      }
      return next;
    });
  }

  function rowKey(group: DocumentGroup, row: FindingRow) {
    return `${group.documentId}:${row.finding.finding_id}`;
  }

  function isChecked(group: DocumentGroup, row: FindingRow) {
    const draft = drafts[rowKey(group, row)];
    return draft ?? verdictFor(allReviews, row.finding)?.verdict === 'correct';
  }

  function groupStatus(group: DocumentGroup): GroupReviewStatus {
    const saved = group.rows.map((row) => verdictFor(allReviews, row.finding)?.verdict ?? null);
    const changed = group.rows.some((row, index) => {
      const draft = drafts[rowKey(group, row)];
      return draft !== undefined && draft !== (saved[index] === 'correct');
    });
    if (changed) return 'unsaved';
    return saved.every((verdict) => verdict !== null) ? 'saved' : 'not_reviewed';
  }

  function setChecked(group: DocumentGroup, rows: FindingRow[], checked: boolean) {
    setDrafts((current) => ({ ...current, ...Object.fromEntries(rows.map((row) => [rowKey(group, row), checked])) }));
  }

  async function saveGroup(group: DocumentGroup) {
    const saved = await reviews.saveGroup(
      `group:${group.documentId}`,
      group.rows.map((row) => ({ members: row.members, verdict: isChecked(group, row) ? 'correct' : 'wrong' })),
    );
    // Saved: the rows show their saved verdicts again.
    setDrafts((current) =>
      Object.fromEntries(Object.entries(current).filter(([key]) => !key.startsWith(`${group.documentId}:`))),
    );
    // Done with it: close it, so it moves into the reviewed list rather than jumping down the page open.
    if (saved) {
      setExpanded((current) => {
        const next = new Set(current);
        next.delete(group.documentId);
        return next;
      });
    }
  }

  function latestVerdict(group: DocumentGroup): FindingReview | undefined {
    return group.rows
      .map((row) => verdictFor(allReviews, row.finding))
      .filter((review): review is FindingReview => review !== undefined)
      .reduce<FindingReview | undefined>((latest, review) => (!latest || review.updated_at > latest.updated_at ? review : latest), undefined);
  }

  // Still the officer's to do: it has findings and they aren't all saved. Unknown until the reviews load, so the
  // list waits for them rather than showing every document first; if they fail to load, every document is listed.
  const reviewsLoading = scored !== null && reviews.data === null && reviews.error === null;

  function needsReview(group: DocumentGroup) {
    return !reviews.data || (group.rows.length > 0 && groupStatus(group) !== 'saved');
  }

  // One group: Select all and Save review, its findings with a tick each, then the issues reported as missed.
  function renderGroup(document: DocumentGroup) {
    const isOpen = expanded.has(document.documentId);
    const checkedCount = document.rows.filter((row) => isChecked(document, row)).length;
    const saving = reviews.savingKey !== null || reviews.data === null;
    return (
      <div key={document.documentId} className="risk-document">
        <button
          type="button"
          className="risk-document-toggle"
          aria-expanded={isOpen}
          onClick={() => toggleDocument(document.documentId)}
        >
          <span className="risk-document-heading">
            <strong>{document.filename}</strong>
            <span className="risk-document-meta">
              {document.scope === 'case' ? 'Cross-document findings' : formatDocumentType(document.documentType)}
              {countBySeverity(document.findings).map(([severity, count]) => (
                <span key={severity} className={`risk-severity risk-severity--${severity}`}>
                  {count} {severity}
                </span>
              ))}
              {document.findings.length === 0 && <span className="risk-document-none">✓ No findings</span>}
              {reviews.data && document.rows.length > 0 && <ReviewTag status={groupStatus(document)} latest={latestVerdict(document)} />}
              <MetricsLine summary={metricsFor(document)} />
            </span>
            {!isOpen && document.rows.length > 0 && (
              <span className="risk-document-preview">{document.rows[0].finding.summary}</span>
            )}
          </span>
          <span className={`risk-chevron ${isOpen ? 'risk-chevron--open' : ''}`} aria-hidden="true">
            ›
          </span>
        </button>

        {isOpen && (
          <>
          {document.rows.length > 0 && reviews.data && (
            <GroupReviewBar
              checkedCount={checkedCount}
              total={document.rows.length}
              status={groupStatus(document)}
              saving={saving}
              onToggleAll={(checked) => setChecked(document, document.rows, checked)}
              onSave={() => void saveGroup(document)}
            />
          )}
          <ul className="risk-findings">
            {document.rows.map((row) => {
              const { finding, count } = row;
              const detail = finding.unreviewed
                ? 'Not reviewed by a specialist.'
                : addedDetail(finding.rationale, finding.summary);
              return (
                <li key={finding.finding_id}>
                  {reviews.data && (
                    <input
                      type="checkbox"
                      className="review-tick risk-finding-tick"
                      aria-label={`Real issue: ${finding.summary}`}
                      checked={isChecked(document, row)}
                      disabled={saving}
                      onChange={(event) => setChecked(document, [row], event.target.checked)}
                    />
                  )}
                  <span className={`risk-severity risk-severity--${finding.severity}`}>
                    {finding.severity}
                  </span>
                  <span className="risk-finding-text">
                    <span className="risk-finding-check">{formatTool(finding.tool)}</span>
                    <span className="risk-finding-summary">
                      {finding.summary}
                      {count > 1 && <span className="risk-repeat">×{count}</span>}
                    </span>
                    {detail && <span className="risk-finding-rationale">{detail}</span>}
                    {finding.repeat_of && (
                      <span className="risk-finding-rationale">
                        Also found on another document; counted once there, not again here.
                      </span>
                    )}
                    {(finding.also_on ?? 0) > 0 && (
                      <span className="risk-finding-rationale">
                        Also found on {finding.also_on} other{' '}
                        {finding.also_on === 1 ? 'document' : 'documents'}; counted once.
                      </span>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
          {reviews.data && (
            <MissedIssues
              scope={document.scope}
              missed={missedFor(document)}
              checks={reviews.data.checks[document.scope]}
              saving={reviews.savingKey !== null}
              onAdd={(checkName, note) =>
                void reviews.addMissed(
                  `missed:${document.documentId}`,
                  document.scope,
                  document.scope === 'case' ? null : document.documentId,
                  checkName,
                  note,
                )
              }
              onEdit={(reviewId, checkName, note) => void reviews.editMissed(reviewId, checkName, note)}
              onRemove={(reviewId) => void reviews.removeReview(reviewId)}
            />
          )}
          </>
        )}
      </div>
    );
  }

  const allGroups = [...documents, ...reviewGroups];
  const toReview = allGroups.filter(needsReview);
  const done = allGroups.filter((group) => !needsReview(group));

  return (
    <>
      <section className="summary-card">
        <div className="summary-card-header">
          <div className="summary-card-title">
            <span className="summary-icon summary-icon--risk">▥</span>
            <h2>Customer Risk Summary</h2>
          </div>

          {scored && (
            <span className={`risk-badge risk-badge--${scored.level!.toLowerCase()}`}>
              {scored.level} risk
            </span>
          )}
        </div>

        {!scored ? (
          <NotScoredMessage risk={risk} />
        ) : (
          <>
            {scored.score !== null && scored.score !== undefined && (
              <ScoreGauge score={scored.score} level={scored.level!} />
            )}
            <div className="risk-overview">
              <span>
                Based on {scored.documents_scanned}{' '}
                {scored.documents_scanned === 1 ? 'document' : 'documents'}
              </span>
              {findings.length > 0 && (
                <span>
                  {countBySeverity(findings)
                    .map(([severity, count]) => `${count} ${severity}`)
                    .join(' · ')}
                </span>
              )}
              {scored.reasons.map((reason) => (
                <span key={reason} className="risk-reason">
                  {reason}
                </span>
              ))}
            </div>

            {findings.length === 0 && (
              <div className="summary-message summary-message--success">
                <span className="summary-message-icon">✓</span>
                <div>
                  <strong>No contributing findings.</strong>
                  <p>Detection found nothing worth flagging across this case's documents.</p>
                </div>
              </div>
            )}

            {scored.unassessed_checks.length > 0 && (
              <details className="risk-unassessed">
                <summary>Couldn't verify ({scored.unassessed_checks.length})</summary>
                <ul>
                  {scored.unassessed_checks.map((check, index) => (
                    <li key={`${check.document_id}-${check.tool}-${index}`}>
                      {check.filename} — {formatTool(check.tool)}
                      {check.reason ? `: ${check.reason}` : ''}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </>
        )}
      </section>

      {/* The officer's review of each document's findings: what's left to do, then what's done folded away
          (still there to open, to change a verdict or report a miss). */}
      {scored && allGroups.length > 0 && (
        <section className="summary-card">
          <div className="summary-card-header">
            <div className="summary-card-title">
              <span className="summary-icon">▤</span>
              <h2>Documents to Review</h2>
            </div>
            {reviews.data && (
              <span className="risk-review-count">
                {toReview.length === 0 ? 'All reviewed' : `${toReview.length} of ${allGroups.length} to review`}
              </span>
            )}
          </div>

          {reviewsLoading ? (
            <div className="summary-message summary-message--info" role="status">
              <span className="summary-message-icon">ⓘ</span>
              <div>
                <strong>Loading reviews…</strong>
              </div>
            </div>
          ) : toReview.length > 0 ? (
            <div className="flagged-list">{toReview.map(renderGroup)}</div>
          ) : (
            <div className="summary-message summary-message--success">
              <span className="summary-message-icon">✓</span>
              <div>
                <strong>No more documents to review.</strong>
                <p>Every document's findings have been reviewed. Open one below to change a verdict or report a missed issue.</p>
              </div>
            </div>
          )}
          {!reviewsLoading && done.length > 0 && (
            <details className="risk-reviewed">
              <summary>
                <span className="risk-chevron" aria-hidden="true">›</span>
                Reviewed ({done.length})
                <span className="risk-reviewed-hint">Reviewed, or nothing to review</span>
              </summary>
              <div className="flagged-list">{done.map(renderGroup)}</div>
            </details>
          )}
          {reviews.error && <ErrorMessage className="card-error" message={reviews.error} />}
          {reviews.data && <ReviewSummary caseSummary={reviews.data.case} />}
        </section>
      )}
    </>
  );
}
