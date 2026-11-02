import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { officerName } from '../../lib/officers';
import type {
  CaseRisk,
  CaseRiskFinding,
  DismissedFinding,
  DocumentRecord,
  FindingReview,
  ReviewScope,
} from '../../services/uploadService';
import { formatDocumentType } from '../../services/uploadService';
import { formatCheck as formatTool } from '../../utils/checkLabels';
import type { GroupReviewStatus } from './FindingReviewControls';
import { GroupReviewBar, MetricsLine, MissedIssues, ReviewSummary } from './FindingReviewControls';
import { findingScope, useFindingReviews, verdictFor } from './useFindingReviews';
import { compareFindings } from './uploadHelpers';
import DocumentReviewModal from './DocumentReviewModal';
import type { ReviewLink } from './DocumentReviewModal';
import { ErrorMessage } from '../../components/ErrorMessage';
import { SCORE_BANDS } from './scoreBands';
import { readingOrder } from '../../components/findingHighlights';
import type { HighlightArea } from '../../components/findingHighlights';

// The group cross-document findings sit in: they belong to the case, not to one document.
const CASE_GROUP_ID = '__case__';

type Severity = CaseRiskFinding['severity'];
const SEVERITY_ORDER: Severity[] = ['high', 'medium', 'low'];

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
  // What the specialists set aside on this document, dismissed or info: reviewed alongside, an officer ticks any
  // that is real.
  dismissed: DismissedFinding[];
}

// Grouped per document (cross-document findings in a group of their own), most severe first (documents by their
// most severe finding, then total points); identical findings (same check, same summary) collapse into one row.
// A document whose findings were all set aside gets a group too, after the scored ones.
function groupByDocument(findings: CaseRiskFinding[], dismissed: DismissedFinding[]): DocumentGroup[] {
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
        dismissed: [],
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
  for (const finding of dismissed) {
    let group = groups.get(finding.document_id);
    if (!group) {
      group = {
        documentId: finding.document_id,
        scope: 'document',
        filename: finding.filename,
        documentType: finding.document_type ?? null,
        findings: [],
        rows: [],
        dismissed: [],
      };
      groups.set(finding.document_id, group);
    }
    group.dismissed.push(finding);
  }
  const total = (group: DocumentGroup) => group.findings.reduce((sum, f) => sum + f.points, 0);
  return Array.from(groups.values()).sort((a, b) => {
    if (a.findings.length === 0 || b.findings.length === 0) return b.findings.length - a.findings.length;
    return compareFindings(a.findings[0], b.findings[0]) || total(b) - total(a);
  });
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
      dismissed: [],
    }));
  if (!skip.has(CASE_GROUP_ID)) {
    groups.push({
      documentId: CASE_GROUP_ID, scope: 'case', filename: 'Across documents', documentType: null, findings: [], rows: [],
      dismissed: [],
    });
  }
  return groups;
}

function countBySeverity(findings: CaseRiskFinding[]): [Severity, number][] {
  return SEVERITY_ORDER.map(
    (severity): [Severity, number] => [severity, findings.filter((f) => f.severity === severity).length],
  ).filter(([, count]) => count > 0);
}

// The detection report's own id for a finding: the case prefixes it with its document ("0c5640e8:font_consistency_tool-1").
function reportFindingId(finding: CaseRiskFinding): string {
  return finding.finding_id.slice(finding.finding_id.indexOf(':') + 1);
}

function reviewedOn(value: string): string {
  return new Date(value).toLocaleDateString('en-SG', { day: 'numeric', month: 'short', year: 'numeric' });
}

// `latest` is the group's most recently saved verdict: who last reviewed it, and when.
function ReviewTag({ status, latest }: { status: GroupReviewStatus; latest?: Pick<FindingReview, 'reviewer' | 'updated_at'> }) {
  // Waiting on a review needs no tag: the group is under Documents to Review.
  if (status === 'not_reviewed') return null;
  return (
    <span className={`risk-review-tag risk-review-tag--${status}`}>
      {status === 'saved'
        ? latest
          ? `✓ Reviewed by ${officerName(latest.reviewer)} · ${reviewedOn(latest.updated_at)}`
          : '✓ Reviewed'
        : 'Unsaved changes'}
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
        ? [
            'Detection failed on a document.',
            `${risk.documents_scanned} of ${risk.documents_total} documents scanned. Retry detection on the failed document to complete the risk score.`,
            'warning',
          ]
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
  readOnly = false,
  reviewsRequired,
}: {
  risk: CaseRisk | null;
  caseId: string;
  caseDocuments?: DocumentRecord[];
  // A finalised case: the officer's verdicts and missed issues show, but can't be changed.
  readOnly?: boolean;
  // Whether the case was created with finding reviews: without them the findings are listed with no ticks, missed
  // issues or metrics.
  reviewsRequired: boolean;
}) {
  const scored = risk?.state === 'scored' && risk.level ? risk : null;
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // The document open for review beside its findings, by id.
  const [reviewing, setReviewing] = useState<string | null>(null);
  // Ticks the officer changed but hasn't saved, by row key. A row without one shows its saved verdict.
  const [drafts, setDrafts] = useState<Record<string, boolean>>({});
  const reviewsOn = reviewsRequired;
  const reviews = useFindingReviews(caseId, scored !== null && reviewsOn);
  const allReviews = reviews.data?.reviews ?? [];

  const findings = scored?.contributing_findings ?? [];
  const dismissedFindings = scored?.dismissed_findings ?? [];
  // Every finding is listed, lows and ones a specialist didn't assess included: all of them count towards the
  // score, and all of them are the officer's to review, as are the ones the specialists set aside.
  const documents = groupByDocument(findings, dismissedFindings);
  // Every group that has findings: only the rest are added as empty groups.
  const withFindings = new Set(documents.map((group) => group.documentId));
  const reviewGroups = emptyGroups(withFindings, caseDocuments);

  // A reason in the case analysis summary links here as #risk-finding-<document>-<finding>: open that document for
  // review, then bring the finding into view once the reviews have loaded.
  const { hash } = useLocation();
  const linkedId = hash.startsWith('#risk-finding-') ? decodeURIComponent(hash.slice(1)) : null;
  const linkedGroupId =
    findings.find((finding) => findingScope(finding) !== 'case' && linkedId === `risk-finding-${finding.document_id}-${finding.finding_id}`)
      ?.document_id ?? null;
  useEffect(() => {
    if (linkedGroupId) setReviewing(linkedGroupId);
  }, [linkedGroupId]);
  const reviewsSettled = reviews.data !== null || reviews.error !== null;
  useEffect(() => {
    if (!linkedId || !reviewsSettled) return;
    window.document.getElementById(linkedId)?.scrollIntoView({ block: 'center' });
  }, [linkedId, reviewing, reviewsSettled]);

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

  // Every tick in a group, scored rows then set-aside findings: its draft key, the findings it marks, and the one
  // whose saved verdict it shows.
  function ticks(group: DocumentGroup) {
    return [
      ...group.rows.map((row) => ({
        key: `${group.documentId}:${row.finding.finding_id}`, members: row.members, shown: row.finding, setAside: false,
      })),
      ...group.dismissed.map((finding) => ({
        key: `${group.documentId}:dismissed:${finding.finding_id}`, members: [finding], shown: finding, setAside: true,
      })),
    ];
  }

  function isTicked(tick: ReturnType<typeof ticks>[number]) {
    return drafts[tick.key] ?? verdictFor(allReviews, tick.shown)?.verdict === 'correct';
  }

  // A group with no findings to mark is reviewed once an officer saves it.
  function signOffFor(group: DocumentGroup) {
    return reviews.data?.sign_offs.find((signOff) =>
      group.scope === 'case' ? signOff.scope === 'case' : signOff.document_id === group.documentId,
    );
  }

  function groupStatus(group: DocumentGroup): GroupReviewStatus {
    const all = ticks(group);
    if (all.length === 0) return signOffFor(group) ? 'saved' : 'not_reviewed';
    const saved = all.map((tick) => verdictFor(allReviews, tick.shown)?.verdict ?? null);
    const changed = all.some((tick, index) => {
      const draft = drafts[tick.key];
      return draft !== undefined && draft !== (saved[index] === 'correct');
    });
    if (changed) return 'unsaved';
    return saved.every((verdict) => verdict !== null) ? 'saved' : 'not_reviewed';
  }

  function setTicked(keys: string[], checked: boolean) {
    setDrafts((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, checked])) }));
  }

  async function saveGroup(group: DocumentGroup) {
    const all = ticks(group);
    const saved =
      all.length > 0
        ? await reviews.saveGroup(
            `group:${group.documentId}`,
            all.map((tick) => ({ members: tick.members, verdict: isTicked(tick) ? 'correct' : 'wrong' })),
          )
        : await reviews.signOff(
            `group:${group.documentId}`,
            group.scope,
            group.scope === 'case' ? null : group.documentId,
          );
    // Saved: the rows show their saved verdicts again.
    setDrafts((current) =>
      Object.fromEntries(Object.entries(current).filter(([key]) => !key.startsWith(`${group.documentId}:`))),
    );
    // Done with it: close it, so it moves into the reviewed list rather than jumping down the page open.
    if (saved) {
      setReviewing((current) => (current === group.documentId ? null : current));
      setExpanded((current) => {
        const next = new Set(current);
        next.delete(group.documentId);
        return next;
      });
    }
  }

  function latestVerdict(group: DocumentGroup) {
    if (ticks(group).length === 0) return signOffFor(group);
    return ticks(group)
      .map((tick) => verdictFor(allReviews, tick.shown))
      .filter((review): review is FindingReview => review !== undefined)
      .reduce<FindingReview | undefined>((latest, review) => (!latest || review.updated_at > latest.updated_at ? review : latest), undefined);
  }

  // Still the officer's to do: its review isn't saved, findings or none. Unknown until the reviews load, so the
  // list waits for them rather than showing every document first; if they fail to load, every document is listed.
  const reviewsLoading = reviewsOn && scored !== null && reviews.data === null && reviews.error === null;

  function needsReview(group: DocumentGroup) {
    return !reviews.data || groupStatus(group) !== 'saved';
  }

  // One group's heading: its document, severity counts and review state.
  function renderGroupMeta(document: DocumentGroup) {
    return (
      <span className="risk-document-meta">
        {document.scope === 'case' ? 'Cross-document findings' : formatDocumentType(document.documentType)}
        {countBySeverity(document.findings).map(([severity, count]) => (
          <span key={severity} className={`risk-severity risk-severity--${severity}`}>
            {count} {severity}
          </span>
        ))}
        {missedFor(document).length > 0 && (
          <span className="risk-severity risk-severity--missed">{missedFor(document).length} missed</span>
        )}
        {document.findings.length === 0 && document.dismissed.length === 0 && missedFor(document).length === 0 && (
          <span className="risk-document-none">✓ No findings</span>
        )}
        {reviews.data && (
          <ReviewTag status={groupStatus(document)} latest={latestVerdict(document)} />
        )}
        <MetricsLine summary={metricsFor(document)} />
      </span>
    );
  }

  // One group: a document's opens for review beside the document; the cross-document group opens in place.
  function renderGroup(document: DocumentGroup) {
    const isOpen = document.scope === 'document' ? reviewing === document.documentId : expanded.has(document.documentId);
    return (
      <div key={document.documentId} className="risk-document">
        <button
          type="button"
          className="risk-document-toggle"
          aria-expanded={isOpen}
          aria-haspopup={document.scope === 'document' ? 'dialog' : undefined}
          onClick={() =>
            document.scope === 'document' ? setReviewing(document.documentId) : toggleDocument(document.documentId)
          }
        >
          <span className="risk-document-heading">
            <strong>{document.filename}</strong>
            {renderGroupMeta(document)}
            {!(isOpen && document.scope === 'case') && document.rows.length > 0 && (
              <span className="risk-document-preview">{document.rows[0].finding.summary}</span>
            )}
          </span>
          <span
            className={`risk-chevron ${isOpen && document.scope === 'case' ? 'risk-chevron--open' : ''}`}
            aria-hidden="true"
          >
            ›
          </span>
        </button>

        {isOpen && document.scope === 'case' && renderGroupBody(document)}
      </div>
    );
  }

  // Select all and Save review, the group's findings with a tick each, then the issues reported as missed. With
  // `link`, beside the document: a finding the checks placed can be shown on it, and the one picked there stands out.
  function renderGroupBody(document: DocumentGroup, link?: ReviewLink) {
    const all = ticks(document);
    const tickFor = new Map(all.map((tick) => [tick.key, tick]));
    // Select all covers the scored findings only: a set-aside one is ticked on its own, once the officer has read it.
    const scoredTicks = all.filter((tick) => !tick.setAside);
    const saving = reviews.savingKey !== null || reviews.data === null;
    return (
      <>
        {reviews.data && !readOnly && (
          <GroupReviewBar
            checkedCount={scoredTicks.filter(isTicked).length}
            selectable={scoredTicks.length}
            total={all.length}
            status={groupStatus(document)}
            saving={saving}
            onToggleAll={(checked) => setTicked(scoredTicks.map((tick) => tick.key), checked)}
            onSave={() => void saveGroup(document)}
          />
        )}
        <ul className="risk-findings">
          {document.rows.map((row) => {
            const tick = tickFor.get(`${document.documentId}:${row.finding.finding_id}`)!;
            const { finding, count } = row;
            const detail = finding.unreviewed
              ? 'Not reviewed by a specialist.'
              : addedDetail(finding.rationale, finding.summary);
            const ids = row.members.map(reportFindingId);
            // Every place the row's findings point at, in reading order, as the document steps through them.
            const places: Place[] = link
              ? ids
                  .flatMap((id) => (link.located.get(id)?.areas ?? []).map((place, area) => ({ id, area, place })))
                  .sort((a, b) => readingOrder(a.place, b.place))
              : [];
            const isActive = link?.activeId !== null && link?.activeId !== undefined && ids.includes(link.activeId);
            const rowId = `risk-finding-${document.documentId}-${finding.finding_id}`;
            const rowClass = [isActive && 'risk-finding--active', linkedId === rowId && 'risk-finding--linked']
              .filter(Boolean)
              .join(' ');
            return (
              <li
                key={finding.finding_id}
                id={rowId}
                data-finding-ids={ids.join(' ')}
                className={rowClass || undefined}
              >
                {reviews.data && (
                  <input
                    type="checkbox"
                    className="review-tick risk-finding-tick"
                    aria-label={`Real issue: ${finding.summary}`}
                    checked={isTicked(tick)}
                    disabled={saving || readOnly}
                    onChange={(event) => setTicked([tick.key], event.target.checked)}
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
                  {link &&
                    (places.length > 0 ? (
                      <PlaceLocator
                        places={places}
                        shownAt={places.findIndex((p) => p.id === link.activeId && p.area === link.activeArea)}
                        onShow={(p) => link.showOnDocument(p.id, p.area)}
                      />
                    ) : (
                      <span className="risk-finding-unplaced">Not tied to one place on the document</span>
                    ))}
                </span>
              </li>
            );
          })}
        </ul>
        {document.dismissed.length > 0 && (
          <details className="risk-dismissed">
            <summary className="risk-dismissed-heading">
              <span className="risk-chevron" aria-hidden="true">›</span>
              <span>
                Set aside by the specialists ({document.dismissed.length}): not scored.
                {reviews.data && ' Tick any that is a real issue after all.'}
              </span>
            </summary>
            <ul className="risk-findings">
              {document.dismissed.map((finding) => {
                const tick = tickFor.get(`${document.documentId}:dismissed:${finding.finding_id}`)!;
                return (
                  <li key={`dismissed:${finding.finding_id}`}>
                    {reviews.data && (
                      <input
                        type="checkbox"
                        className="review-tick risk-finding-tick"
                        aria-label={`Real issue (set aside): ${finding.summary}`}
                        checked={isTicked(tick)}
                        disabled={saving || readOnly}
                        onChange={(event) => setTicked([tick.key], event.target.checked)}
                      />
                    )}
                    <span className="risk-severity risk-severity--dismissed">set aside</span>
                    <span className="risk-finding-text">
                      <span className="risk-finding-check">{formatTool(finding.tool)}</span>
                      <span className="risk-finding-summary">{finding.summary}</span>
                      {finding.reason && <span className="risk-finding-rationale">Why set aside: {finding.reason}</span>}
                    </span>
                  </li>
                );
              })}
            </ul>
          </details>
        )}
        {reviews.data && (
          <MissedIssues
            readOnly={readOnly}
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
    );
  }

  // The documents with no findings are only listed to report what detection missed, which is part of reviewing.
  const allGroups = reviewsOn ? [...documents, ...reviewGroups] : documents;
  const reviewingGroup = allGroups.find((group) => group.scope === 'document' && group.documentId === reviewing);
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

      {/* With reviews off, each document's findings, to open beside the document. */}
      {scored && !reviewsOn && allGroups.length > 0 && (
        <section className="summary-card">
          <div className="summary-card-header">
            <div className="summary-card-title">
              <span className="summary-icon">▤</span>
              <h2>Findings by Document</h2>
            </div>
          </div>
          <div className="flagged-list">{allGroups.map(renderGroup)}</div>
        </section>
      )}

      {/* The officer's review of each document's findings: what's left to do, then what's done folded away
          (still there to open, to change a verdict or report a miss). */}
      {scored && reviewsOn && allGroups.length > 0 && (
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
                <span className="risk-reviewed-hint">Saved by an officer</span>
              </summary>
              <div className="flagged-list">{done.map(renderGroup)}</div>
            </details>
          )}
          {reviews.error && <ErrorMessage className="card-error" message={reviews.error} />}
          {reviews.data && (
            <ReviewSummary
              caseSummary={reviews.data.case}
              targetPrecision={reviews.data.target_precision}
              targetRecall={reviews.data.target_recall}
            />
          )}
        </section>
      )}

      {reviewingGroup && (
        <DocumentReviewModal
          documentId={reviewingGroup.documentId}
          filename={reviewingGroup.filename}
          heading={
            <>
              <strong className="review-modal-filename">{reviewingGroup.filename}</strong>
              {renderGroupMeta(reviewingGroup)}
            </>
          }
          onClose={() => setReviewing(null)}
          order={reviewingGroup.rows.map((row) => row.members.map(reportFindingId))}
        >
          {(link) => renderGroupBody(reviewingGroup, link)}
        </DocumentReviewModal>
      )}
    </>
  );
}

// One of the places a row's findings point at: a report finding id, which of its areas, and where that is.
interface Place {
  id: string;
  area: number;
  place: HighlightArea;
}

// Takes the document to a row's findings. One place: a button to it. Several (a misspelling can recur dozens
// of times): one button to the first, then, while the row is the one shown, a stepper through its places.
function PlaceLocator({ places, shownAt, onShow }: { places: Place[]; shownAt: number; onShow: (place: Place) => void }) {
  if (places.length === 1) {
    return (
      <button type="button" className="risk-finding-locate" onClick={() => onShow(places[0])}>
        ⌖ Show on page {places[0].place.page}
      </button>
    );
  }
  if (shownAt < 0) {
    const pages = [...new Set(places.map((p) => p.place.page))];
    return (
      <button
        type="button"
        className="risk-finding-locate"
        title={`On page${pages.length > 1 ? 's' : ''} ${pages.join(', ')}`}
        onClick={() => onShow(places[0])}
      >
        ⌖ Show on document · {places.length} places
      </button>
    );
  }
  const go = (step: 1 | -1) => onShow(places[(shownAt + step + places.length) % places.length]);
  return (
    <span className="risk-finding-places" role="group" aria-label="Places on the document">
      <button type="button" onClick={() => go(-1)} aria-label="Previous place">
        ‹
      </button>
      <span>
        Place {shownAt + 1} of {places.length} (Page {places[shownAt].place.page})
      </span>
      <button type="button" onClick={() => go(1)} aria-label="Next place">
        ›
      </button>
    </span>
  );
}

