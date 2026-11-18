import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
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
import type { ReviewReason, ReviewVerdict } from '../../services/uploadService';
import { formatCheck as formatTool } from '../../utils/checkLabels';
import type { GroupReviewStatus } from './FindingReviewControls';
import { GroupReviewBar, MetricsLine, MissedIssues, ReasonChips, ReviewSummary } from './FindingReviewControls';
import { ShownFeedback } from './ShownFeedback';
import { findingScope, useFindingReviews, verdictFor } from './useFindingReviews';
import type { VerdictRow } from './useFindingReviews';
import { compareFindings } from './uploadHelpers';
import DocumentReviewModal from './DocumentReviewModal';
import CrossDocCompareModal from './CrossDocCompareModal';
import type { ReviewLink } from './DocumentReviewModal';
import { ErrorMessage } from '../../components/ErrorMessage';
import ConfirmDialog from '../../components/ConfirmDialog';
import { SCORE_BANDS } from './scoreBands';
import { SeverityGauge } from './SeverityGauge';
import { readingOrder } from '../../components/findingHighlights';
import type { FindingHighlight, HighlightArea } from '../../components/findingHighlights';
import { Icon } from '../../components/Icon';
import { CASE_GROUP_ID } from './analysisSummary';

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

// Why a finding has no box on the document: it's about the file rather than the page, or it's on the page but the
// search couldn't pin it down (printed in another form, or in too many places).
function unplacedNote(finding: CaseRiskFinding): string {
  const aboutTheFile =
    finding.tool === 'metadata_tool' ||
    (finding.tool === 'prompt_injection_tool' && /^(The file name|Metadata field)\b/.test(finding.summary));
  if (aboutTheFile) return "About the file's details, not a place on the page";
  if (finding.tool === 'ai_generation_tool' && /\.(png|jpe?g|webp|heic|tiff?)$/i.test(finding.filename)) {
    return 'About the whole image';
  }
  return "On the document, but couldn't pinpoint where";
}

// A row's findings as one to compare: the first one's summary and severity, with every document and value any of
// them rests on (a row merges identical findings).
function comparable(members: CaseRiskFinding[]): CaseRiskFinding {
  const [first] = members;
  const documentIds = [...new Set(members.flatMap((member) => member.document_ids ?? []))];
  const citedValues = members.flatMap((member) => member.cited_values ?? []);
  return { ...first, document_ids: documentIds, cited_values: citedValues };
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
    ? ['Not scored yet.', 'The case analysis scores the case once every approved document has been scanned.', 'info']
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
      <span className="summary-message-icon"><Icon name={tone === 'warning' ? 'alert' : 'info'} size={18} /></span>
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
  part,
}: {
  risk: CaseRisk | null;
  caseId: string;
  // The Risk & Analysis page's two tabs: the score on Overview, every finding (and their review) on Findings.
  part: 'score' | 'findings';
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
  // A cross-document finding open with its documents side by side.
  const [comparing, setComparing] = useState<CaseRiskFinding | null>(null);
  // Ticks (the agents got it wrong) the officer changed but hasn't saved, by row key. A row without one shows its saved
  // verdict. The reasons given for false alarms, likewise.
  const [drafts, setDrafts] = useState<Record<string, boolean>>({});
  const [reasonDrafts, setReasonDrafts] = useState<Record<string, ReviewReason | null>>({});
  const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({});
  // Showing the confirm for "Save all reviews": every group's Save review in one go.
  const [confirmSaveAll, setConfirmSaveAll] = useState(false);
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
  // A reason in the Overview tab's summary links here with from=overview to open the document; closing it should
  // return there, not leave the officer on the Findings tab they never chose.
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const cameFromOverview = searchParams.get('from') === 'overview';
  function closeReview() {
    setReviewing(null);
    if (cameFromOverview) navigate({ search: '?section=risk', hash: '' }, { replace: true });
  }
  const linkedId = hash.startsWith('#risk-finding-') ? decodeURIComponent(hash.slice(1)) : null;
  const linkedGroupId =
    findings.find((finding) => findingScope(finding) !== 'case' && linkedId === `risk-finding-${finding.document_id}-${finding.finding_id}`)
      ?.document_id ?? null;
  useEffect(() => {
    if (linkedGroupId) setReviewing(linkedGroupId);
  }, [linkedGroupId]);
  // A cross-document finding opens in place: expand its group.
  const linksToCaseGroup = findings.some(
    (finding) => findingScope(finding) === 'case' && linkedId === `risk-finding-${CASE_GROUP_ID}-${finding.finding_id}`,
  );
  useEffect(() => {
    if (linksToCaseGroup) setExpanded((open) => new Set(open).add(CASE_GROUP_ID));
  }, [linksToCaseGroup]);
  const caseGroupOpen = expanded.has(CASE_GROUP_ID);
  const reviewsSettled = reviews.data !== null || reviews.error !== null;
  useEffect(() => {
    if (!linkedId || !reviewsSettled) return;
    window.document.getElementById(linkedId)?.scrollIntoView({ block: 'center' });
  }, [linkedId, reviewing, reviewsSettled, caseGroupOpen]);

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

  // A tick marks a finding the agents got wrong. On a scored finding that is a false alarm (verdict wrong); on one the
  // specialists set aside it is a real issue they dismissed (verdict correct). Unticked is the agents being right.
  function verdictOf(tick: ReturnType<typeof ticks>[number], ticked: boolean): ReviewVerdict {
    return ticked === tick.setAside ? 'correct' : 'wrong';
  }

  function savedTick(tick: ReturnType<typeof ticks>[number]) {
    const saved = verdictFor(allReviews, tick.shown)?.verdict ?? null;
    return saved !== null && saved === verdictOf(tick, true);
  }

  function isTicked(tick: ReturnType<typeof ticks>[number]) {
    return drafts[tick.key] ?? savedTick(tick);
  }

  // Why a ticked finding is one the agents got wrong (a false alarm, or a set-aside one that is real): the officer's pick,
  // else the saved one.
  function reasonOf(tick: ReturnType<typeof ticks>[number]): ReviewReason | null {
    return reasonDrafts[tick.key] !== undefined ? reasonDrafts[tick.key] : (verdictFor(allReviews, tick.shown)?.reason ?? null);
  }

  // The note an "Other" reason needs: the officer's, else the saved one.
  function noteOf(tick: ReturnType<typeof ticks>[number]): string {
    return noteDrafts[tick.key] ?? verdictFor(allReviews, tick.shown)?.note ?? '';
  }

  // A ticked finding (the agents got it wrong) is complete once it has a reason, and for "Other" a note. Unticked, the
  // officer agrees with the agents and there is nothing to explain.
  function lacksReason(tick: ReturnType<typeof ticks>[number]): boolean {
    if (!isTicked(tick)) return false;
    const reason = reasonOf(tick);
    return reason === null || (reason === 'other' && noteOf(tick).trim() === '');
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
    const changed = all.some((tick) => {
      const draft = drafts[tick.key];
      const reason = reasonDrafts[tick.key];
      const note = noteDrafts[tick.key];
      return (
        (draft !== undefined && draft !== savedTick(tick)) ||
        (reason !== undefined && reason !== (verdictFor(allReviews, tick.shown)?.reason ?? null)) ||
        (note !== undefined && note.trim() !== (verdictFor(allReviews, tick.shown)?.note ?? ''))
      );
    });
    if (changed) return 'unsaved';
    return saved.every((verdict) => verdict !== null) ? 'saved' : 'not_reviewed';
  }

  function setTicked(keys: string[], checked: boolean) {
    setDrafts((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, checked])) }));
  }

  // What a group's Save review sends: each tick's verdict, and for a ticked false alarm its reason (and note for
  // "other"). Save all reviews sends the same rows for every group, so the two never disagree.
  function groupRows(group: DocumentGroup): VerdictRow[] {
    return ticks(group).map((tick) => {
      const ticked = isTicked(tick);
      return {
        members: tick.members,
        verdict: verdictOf(tick, ticked),
        reason: ticked ? reasonOf(tick) : null,
        note: ticked && reasonOf(tick) === 'other' ? noteOf(tick).trim() : null,
      };
    });
  }

  async function saveGroup(group: DocumentGroup) {
    const all = ticks(group);
    const saved =
      all.length > 0
        ? await reviews.saveGroup(`group:${group.documentId}`, groupRows(group))
        : await reviews.signOff(
            `group:${group.documentId}`,
            group.scope,
            group.scope === 'case' ? null : group.documentId,
          );
    // Saved: the rows show their saved verdicts again.
    const otherGroups = <T,>(current: Record<string, T>) =>
      Object.fromEntries(Object.entries(current).filter(([key]) => !key.startsWith(`${group.documentId}:`)));
    setDrafts(otherGroups);
    setReasonDrafts(otherGroups);
    setNoteDrafts(otherGroups);
    // Done with it: close it, so it moves into the reviewed list rather than jumping down the page open. Saving
    // from the document opened via a Case Analysis reason returns there too, like closing it.
    if (saved) {
      setExpanded((current) => {
        const next = new Set(current);
        next.delete(group.documentId);
        return next;
      });
      if (reviewing === group.documentId) closeReview();
      else setReviewing((current) => (current === group.documentId ? null : current));
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
        {document.dismissed.length > 0 && (
          <span className="risk-severity risk-severity--dismissed">{document.dismissed.length} set aside</span>
        )}
        {missedFor(document).length > 0 && (
          <span className="risk-severity risk-severity--missed">{missedFor(document).length} missed</span>
        )}
        {/* Nothing scored against it and nothing reported missed: clean, whatever the specialists set aside. */}
        {document.findings.length === 0 && missedFor(document).length === 0 && (
          <span className="risk-document-none">Clean</span>
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
          <Icon
            name="chevronRight"
            size={16}
            className={`risk-chevron ${isOpen && document.scope === 'case' ? 'risk-chevron--open' : ''}`}
          />
        </button>

        {isOpen && document.scope === 'case' && renderGroupBody(document)}
      </div>
    );
  }

  // Select all and Save review, the group's findings with a tick each, then the issues reported as missed. With
  // `link`, beside the document: a finding the checks placed can be shown on it, and the one picked there stands out.
  // Beside the document, the rows run by severity, then by where on the document each first appears (page, then top
  // to bottom), so a severity's findings follow the pages; a row with nothing placed goes last in its severity.
  // Without the document, by severity then points.
  function rowsInPageOrder(rows: FindingRow[], located?: Map<string, FindingHighlight>): FindingRow[] {
    if (!located) return rows;
    const firstPlace = (row: FindingRow) =>
      row.members
        .map(reportFindingId)
        .flatMap((id) => located.get(id)?.areas ?? [])
        .sort(readingOrder)[0];
    const rank = (row: FindingRow) => {
      const index = SEVERITY_ORDER.indexOf(row.finding.severity);
      return index < 0 ? SEVERITY_ORDER.length : index;
    };
    return [...rows].sort((a, b) => {
      const [placeA, placeB] = [firstPlace(a), firstPlace(b)];
      return rank(a) - rank(b) || (placeA && placeB ? readingOrder(placeA, placeB) : placeA ? -1 : placeB ? 1 : 0);
    });
  }

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
            incomplete={all.filter(lacksReason).length}
            onToggleAll={(checked) => setTicked(scoredTicks.map((tick) => tick.key), checked)}
            onSave={() => void saveGroup(document)}
          />
        )}
        <ul className="risk-findings">
          {rowsInPageOrder(document.rows, link?.located).map((row) => {
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
                    aria-label={`Agents got this wrong: ${finding.summary}`}
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
                  {reviews.data && isTicked(tick) && (
                    <ReasonChips
                      kind="false_alarm"
                      reason={reasonOf(tick)}
                      note={noteOf(tick)}
                      disabled={saving}
                      readOnly={readOnly}
                      onChange={(reason) => setReasonDrafts((current) => ({ ...current, [tick.key]: reason }))}
                      onNoteChange={(note) => setNoteDrafts((current) => ({ ...current, [tick.key]: note }))}
                    />
                  )}
                  <ShownFeedback ids={finding.feedback_ids} />
                  {document.scope === 'case' && comparable(row.members).document_ids.length >= 2 && (
                    <button
                      type="button"
                      className="risk-compare-button"
                      onClick={() => setComparing(comparable(row.members))}
                    >
                      <Icon name="documents" size={14} />
                      Compare documents
                    </button>
                  )}
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
                      <span className="risk-finding-unplaced">{unplacedNote(finding)}</span>
                    ))}
                </span>
              </li>
            );
          })}
          {document.dismissed.map((finding) => {
            const tick = tickFor.get(`${document.documentId}:dismissed:${finding.finding_id}`)!;
            return (
              <li key={`dismissed:${finding.finding_id}`}>
                {reviews.data && (
                  <input
                    type="checkbox"
                    className="review-tick risk-finding-tick"
                    aria-label={`Agents got this wrong (set aside, but real): ${finding.summary}`}
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
                  {reviews.data && !readOnly && !isTicked(tick) && (
                    <span className="risk-finding-rationale">Tick it if it is a real issue after all.</span>
                  )}
                  {reviews.data && isTicked(tick) && (
                    <ReasonChips
                      kind="set_aside"
                      reason={reasonOf(tick)}
                      note={noteOf(tick)}
                      disabled={saving}
                      readOnly={readOnly}
                      onChange={(reason) => setReasonDrafts((current) => ({ ...current, [tick.key]: reason }))}
                      onNoteChange={(note) => setNoteDrafts((current) => ({ ...current, [tick.key]: note }))}
                    />
                  )}
                  <ShownFeedback ids={finding.feedback_ids} />
                </span>
              </li>
            );
          })}
        </ul>
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

  // "Save all reviews": every group still to review, saved exactly as its own Save review would save it (groupRows):
  // nothing is ticked for the officer, and a group with no findings is signed off. Like Save review, it waits until
  // every ticked false alarm has a reason. The officer can still open any group afterwards to change a verdict.
  const toSave = toReview.map((group) => ({ group, all: ticks(group) }));
  const saveAllRows = toSave.flatMap(({ group }) => groupRows(group));
  const findingsAs = (verdict: ReviewVerdict) =>
    saveAllRows.filter((row) => row.verdict === verdict).reduce((n, row) => n + row.members.length, 0);
  const realIssues = findingsAs('correct');
  const falseAlarms = findingsAs('wrong');
  const cleanToSignOff = toSave.filter(({ all }) => all.length === 0).length;
  const missingReasons = toSave.flatMap(({ all }) => all).filter(lacksReason).length;

  async function saveAllReviews() {
    const signOffs = toSave
      .filter(({ all }) => all.length === 0)
      .map(({ group }) => ({ scope: group.scope, documentId: group.scope === 'case' ? null : group.documentId }));
    const saved = await reviews.saveAll('save-all', saveAllRows, signOffs);
    if (saved) {
      setDrafts({});
      setReasonDrafts({});
      setNoteDrafts({});
      setReviewing(null);
      setExpanded(new Set());
    }
    setConfirmSaveAll(false);
  }

  const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`;
  const saveAllMessage = [
    realIssues > 0 && `${plural(realIssues, 'finding')} will be saved as ${realIssues === 1 ? 'a real issue' : 'real issues'}`,
    falseAlarms > 0 && `${plural(falseAlarms, 'finding')} as ${falseAlarms === 1 ? 'a false alarm' : 'false alarms'}`,
    cleanToSignOff > 0 && `${plural(cleanToSignOff, 'document')} with no findings will be signed off`,
  ].filter(Boolean).join(', ');

  if (part === 'findings' && (!scored || allGroups.length === 0)) {
    return (
      <section className="summary-card">
        <div className="summary-card-header">
          <div className="summary-card-title">
            <span className="summary-icon"><Icon name="documents" size={20} /></span>
            <h2>Findings</h2>
          </div>
        </div>
        {!scored ? (
          <NotScoredMessage risk={risk} />
        ) : (
          <div className="summary-message summary-message--success">
            <span className="summary-message-icon"><Icon name="check" size={18} /></span>
            <div>
              <strong>No findings.</strong>
              <p>Detection and the case analysis found nothing worth flagging across this case's documents.</p>
            </div>
          </div>
        )}
      </section>
    );
  }

  // Overview: the score, its level and what it rests on, at the top of the combined Case Risk card
  // (CaseAnalysisCard). The case analysis's summary under it explains why.
  if (part === 'score') {
    if (!scored) return <NotScoredMessage risk={risk} />;
    const band = SCORE_BANDS.find((b) => b.level === scored.level);
    return (
      <>
      <div className="risk-score">
        {scored.score !== null && scored.score !== undefined && (
          <SeverityGauge score={scored.score} level={scored.level!} />
        )}
        <div className="risk-score-detail">
          <span className={`risk-score-level risk-score-level--${scored.level!.toLowerCase()}`}>
            <span data-testid="risk-badge">{scored.level} Risk</span>
            {band ? ` · ${band.min}–${band.max}` : ''}
          </span>
          {/* "18 findings across 16 documents", then the findings by severity, each with its severity's dot, and the
              ones the specialists set aside (not scored) after them, quieter. */}
          <p className="risk-facts">
            {findings.length > 0
              ? `${findings.length} ${findings.length === 1 ? 'finding' : 'findings'} across `
              : 'Based on '}
            {scored.documents_scanned} {scored.documents_scanned === 1 ? 'document' : 'documents'}
          </p>
          {(findings.length > 0 || dismissedFindings.length > 0) && (
            <ul className="risk-tally" aria-label="Findings by severity">
              {countBySeverity(findings).map(([severity, count]) => (
                <li key={severity}>
                  <span className={`analysis-brief-dot analysis-brief-dot--${severity}`} aria-hidden="true" />
                  <strong>{count}</strong> {severity.charAt(0).toUpperCase() + severity.slice(1)}
                </li>
              ))}
              {dismissedFindings.length > 0 && (
                <li className="risk-tally-aside">
                  <strong>{dismissedFindings.length}</strong> Set Aside
                </li>
              )}
            </ul>
          )}
        </div>
      </div>
      </>
    );
  }

  return (
    <>
      {part === 'findings' && (
      <>
      {/* With reviews off, each document's findings, to open beside the document. */}
      {scored && !reviewsOn && allGroups.length > 0 && (
        <section className="summary-card">
          <div className="summary-card-header">
            <div className="summary-card-title">
              <span className="summary-icon"><Icon name="documents" size={20} /></span>
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
              <span className="summary-icon"><Icon name="documents" size={20} /></span>
              <h2>Documents to Review</h2>
            </div>
            {reviews.data && (
              <div className="risk-review-actions">
                {/* Counts documents (a clean one still needs signing off), not findings: the Findings tab's pill
                    and the next step count findings without a verdict. */}
                <span className="risk-review-count">
                  {toReview.length === 0
                    ? 'All documents reviewed'
                    : `${toReview.length} of ${allGroups.length} ${allGroups.length === 1 ? 'document' : 'documents'} to review`}
                </span>
                {!readOnly && toReview.length > 0 && (
                  <>
                    {missingReasons > 0 && (
                      <span className="group-review-incomplete" role="status">
                        {missingReasons === 1 ? '1 finding needs a reason' : `${missingReasons} findings need a reason`}
                      </span>
                    )}
                    <button
                      type="button"
                      className="risk-save-all"
                      disabled={reviews.savingKey !== null || missingReasons > 0}
                      onClick={() => setConfirmSaveAll(true)}
                    >
                      {reviews.savingKey === 'save-all' ? 'Saving…' : 'Save all reviews'}
                    </button>
                  </>
                )}
              </div>
            )}
          </div>

          {/* At the top, for the whole card: without the saved verdicts every document reads as still to review. */}
          {reviews.error && (
            <div className="risk-reviews-error">
              <ErrorMessage
                message={
                  reviews.data
                    ? reviews.error
                    : "Couldn't load your earlier verdicts, so every document shows as still to review."
                }
              />
              {!reviews.data && (
                <button type="button" className="text-button" onClick={reviews.retry}>
                  Try again
                </button>
              )}
            </div>
          )}

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
              <span className="summary-message-icon"><Icon name="check" size={18} /></span>
              <div>
                <strong>No more documents to review.</strong>
                <p>Every document's findings have been reviewed. Open one below to change a verdict or report a missed issue.</p>
              </div>
            </div>
          )}
          {!reviewsLoading && done.length > 0 && (
            <details className="risk-reviewed">
              <summary>
                <Icon name="chevronRight" size={16} className="risk-chevron" />
                Reviewed ({done.length})
                <span className="risk-reviewed-hint">Saved by an officer</span>
              </summary>
              <div className="flagged-list">{done.map(renderGroup)}</div>
            </details>
          )}
          {reviews.data && (
            <ReviewSummary
              caseSummary={reviews.data.case}
              targetPrecision={reviews.data.target_precision}
              targetRecall={reviews.data.target_recall}
            />
          )}
        </section>
      )}

      {comparing && (
        <CrossDocCompareModal finding={comparing} documents={caseDocuments} onClose={() => setComparing(null)} />
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
          onClose={closeReview}
          order={(located) => rowsInPageOrder(reviewingGroup.rows, located).map((row) => row.members.map(reportFindingId))}
        >
          {(link) => renderGroupBody(reviewingGroup, link)}
        </DocumentReviewModal>
      )}

      {confirmSaveAll && (
        <ConfirmDialog
          title="Save all reviews?"
          message={`${saveAllMessage}. You can still open any document to change a verdict or report a missed issue.`}
          confirmLabel="Save all"
          busyLabel="Saving…"
          tone="primary"
          isConfirming={reviews.savingKey === 'save-all'}
          onCancel={() => setConfirmSaveAll(false)}
          onConfirm={() => void saveAllReviews()}
        />
      )}
      </>
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
        <Icon name="locate" size={14} /> Show on page {places[0].place.page}
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
        <Icon name="locate" size={14} /> Show on document · {places.length} places
      </button>
    );
  }
  const go = (step: 1 | -1) => onShow(places[(shownAt + step + places.length) % places.length]);
  return (
    <span className="risk-finding-places" role="group" aria-label="Places on the document">
      <button type="button" onClick={() => go(-1)} aria-label="Previous place">
        <Icon name="chevronLeft" size={14} />
      </button>
      <span>
        Place {shownAt + 1} of {places.length} (Page {places[shownAt].place.page})
      </span>
      <button type="button" onClick={() => go(1)} aria-label="Next place">
        <Icon name="chevronRight" size={14} />
      </button>
    </span>
  );
}

