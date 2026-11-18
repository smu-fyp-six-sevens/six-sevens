import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { officerName } from '../../lib/officers';
import {
  INDIVIDUAL_DETECTION_CHECKS,
  formatDocumentType,
  listCaseAuditTrail,
  type AuditEvent,
} from '../../services/uploadService';
import { formatCheck } from '../../utils/checkLabels';
import { humanizeFieldKey } from '../../utils/fieldLabels';
import { describeFailureReason } from '../document-details/formatters';
import { ErrorMessage } from '../../components/ErrorMessage';
import { formatDuration } from './processingTimes';
import { Icon } from '../../components/Icon';

interface AuditDocument {
  id: string;
  filename: string;
}

// What happened, by event type (backend/app/events/types.py). A type not listed is shown as it is.
const ACTION_LABELS: Record<string, string> = {
  'case.created': 'Created case',
  'case.renamed': 'Renamed case',
  'case.deleted': 'Deleted case',
  'case.finalised': 'Finalised case',
  // Before finalising became permanent, a finalised case could be reopened.
  'case.reopened': 'Reopened case',
  'document.uploaded': 'Uploaded document',
  'document.analyzed': 'Classified and extracted document',
  'document.failed': 'Document processing failed',
  'document.retry_requested': 'Retried document processing',
  'document.cancelled': 'Cancelled document processing',
  'document.run_stopped': 'Document processing run stopped',
  'document.retyped': 'Changed document type',
  'document.type_corrected': 'Confirmed document type',
  'document.guard_overridden': 'Processed a document the content guard stopped',
  'document.field_edited': 'Edited extracted fields',
  'document.approved': 'Approved document',
  'document.rejected': 'Rejected document',
  'document.moved': 'Moved document',
  'document.deleted': 'Deleted document',
  'detection.retry_requested': 'Retried anomaly detection',
  'detection.cancel_requested': 'Cancelled anomaly detection',
  'detection.check_run': 'Ran individual check',
  'document.detected': 'Anomaly detection run',
  'detection.failed': 'Anomaly detection run',
  'case.analysis.requested': 'Requested case analysis',
  'case.analysis.completed': 'Case analysis run',
  'case.report_exported': 'Downloaded case report',
  'note.added': 'Added note',
  'note.edited': 'Edited note',
  'note.deleted': 'Deleted note',
  'finding_review.saved': 'Reviewed findings',
  'finding_review.missed_reported': 'Reported a missed issue',
  'finding_review.missed_edited': 'Edited a missed issue',
  'finding_review.removed': 'Removed a finding review',
};

// Stuck-work recovery is the app's own upkeep, not an action on the case, so the trail leaves it out
// (it stays in audit_events for monitoring).
const HIDDEN_TYPES = new Set(['recovery.requested', 'document.recovered', 'recovery.exhausted']);

// What an officer reviewing the case sees: officers' actions and the runs they started.
function isShown(event: AuditEvent): boolean {
  return event.actor_type !== 'system' && !HIDDEN_TYPES.has(event.type);
}

// An officer stopping work: a successful action, but marked like the run it cancelled.
const CANCEL_TYPES = new Set(['document.cancelled', 'detection.cancel_requested']);

// The dot's outcome: the run's own, or "cancelled" for an officer's cancel.
function markerOutcome(event: AuditEvent): AuditEvent['outcome'] {
  return event.outcome === 'success' && CANCEL_TYPES.has(event.type) ? 'cancelled' : event.outcome;
}

// A field edit's three kinds, which share one event type.
const FIELD_EDIT_LABELS: Record<string, string> = {
  saved: 'Edited extracted fields',
  added: 'Added extracted field',
  deleted: 'Removed extracted field',
};

const OUTCOME_LABELS: Record<AuditEvent['outcome'], string> = {
  success: 'Succeeded',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

// A list field's change is its item counts ("4 items") plus the items added and removed, each as JSON. A field
// holding an object or a list is saved as JSON too.
interface FieldChange {
  field: string;
  from: string | null;
  to: string | null;
  added?: string[];
  removed?: string[];
}

function formatTimestamp(value: string): string {
  return new Date(value).toLocaleString('en-SG', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatTimeOfDay(value: string): string {
  return new Date(value).toLocaleTimeString('en-SG', { hour: 'numeric', minute: '2-digit', second: '2-digit' });
}

function formatDay(value: string): string {
  return new Date(value).toLocaleDateString('en-SG', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
}

// The trail split into days, in order, each day's entries in order.
function groupByDay(events: AuditEvent[]): { day: string; events: AuditEvent[] }[] {
  const days: { day: string; events: AuditEvent[] }[] = [];
  for (const event of events) {
    const day = formatDay(event.occurred_at);
    const last = days[days.length - 1];
    if (last?.day === day) last.events.push(event);
    else days.push({ day, events: [event] });
  }
  return days;
}

// The timeline key: who acted (an outlined dot), then the outcomes that fill a dot. "System" is the AI agents'
// work: classification, extraction, detection and case analysis.
const LEGEND: { marker: string; label: string }[] = [
  { marker: 'user', label: 'Loan officer' },
  { marker: 'agent', label: 'System' },
  { marker: 'failed', label: 'Failed' },
  { marker: 'cancelled', label: 'Cancelled' },
];

function text(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null;
}

function count(value: unknown): number {
  return typeof value === 'number' ? value : 0;
}

// The loan officer behind the entry: the actor for an officer's action, or the officer who started an agent's run.
function officerOf(event: AuditEvent): string | null {
  return event.actor_type === 'user' ? event.actor : text(event.detail.requested_by);
}

// The individual checks by id, and the two image checks the Detection panel runs only as "Image integrity".
const CHECK_LABELS: Record<string, string> = {
  ...Object.fromEntries(INDIVIDUAL_DETECTION_CHECKS.map((check) => [check.id, check.label])),
  'image-manipulation': 'Image Resolution & Compression',
  'image-tampering': 'Image Boundary, Scaling & Colour Consistency',
};

function actionLabel(event: AuditEvent): string {
  if (event.type === 'detection.check_run') {
    const check = text(event.detail.check);
    return check ? `Ran ${CHECK_LABELS[check] ?? check} check` : ACTION_LABELS[event.type];
  }
  if (event.type === 'document.field_edited') {
    return FIELD_EDIT_LABELS[text(event.detail.action) ?? ''] ?? ACTION_LABELS[event.type];
  }
  return ACTION_LABELS[event.type] ?? event.type;
}

// A field edit's changes, each value as it was saved (null: the field wasn't there). Absent on older entries.
function fieldChanges(event: AuditEvent): FieldChange[] | null {
  const { changes } = event.detail;
  return event.type === 'document.field_edited' && Array.isArray(changes) ? (changes as FieldChange[]) : null;
}

// A saved value as it reads: an object's filled-in values, e.g. "Grantor: DBS Bank · Account Type: Credit Card"
// (", " between them when it sits inside another value), and a list's items separated by "; ".
function readable(value: unknown, nested = false): string {
  if (Array.isArray(value)) {
    return value.length > 0 ? value.map((item) => readable(item, true)).join('; ') : '(no items)';
  }
  if (value !== null && typeof value === 'object') {
    const filled = Object.entries(value).filter(([, item]) => item !== null && item !== '');
    return (
      filled.map(([key, item]) => `${humanizeFieldKey(key)}: ${readable(item, true)}`).join(nested ? ', ' : ' · ') ||
      '(empty item)'
    );
  }
  return String(value);
}

// A stored value that is an object or a list as JSON, parsed; anything else (including JSON cut to the stored
// length, so no longer whole) as undefined.
function parsedJson(value: string): object | undefined {
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed !== null && typeof parsed === 'object' ? parsed : undefined;
  } catch {
    return undefined;
  }
}

// A list item's filled-in values.
function listItem(json: string): string {
  const item = parsedJson(json);
  return item === undefined ? json : readable(item);
}

function fieldValue(value: string | null): string {
  if (value === null) return '(none)';
  if (value === '') return '(blank)';
  const parsed = parsedJson(value);
  return parsed === undefined ? value : readable(parsed);
}

// "high" or "High" as "High".
function riskLevel(level: string): string {
  return level.charAt(0).toUpperCase() + level.slice(1).toLowerCase();
}

// A finished case analysis's risk level, shown as the risk summary's own badge (case-summary.css).
const RISK_LEVELS = new Set(['low', 'medium', 'high', 'critical']);

function riskOf(event: AuditEvent): string | null {
  if (event.type !== 'case.analysis.completed' || event.outcome !== 'success') return null;
  const level = text(event.detail.risk_level)?.toLowerCase();
  return level && RISK_LEVELS.has(level) ? level : null;
}

// One line on what the entry changed or found, when there is more to say than its action.
function describe(event: AuditEvent): string | null {
  const { detail } = event;
  switch (event.type) {
    case 'case.created':
      return [text(detail.greenlink_case_id), text(detail.company_name)].filter(Boolean).join(' · ') || null;
    case 'case.renamed':
      return `${text(detail.from) ?? '(no name)'} → ${text(detail.to) ?? '(no name)'}`;
    case 'document.retyped':
      return `${formatDocumentType(text(detail.from_type))} → ${formatDocumentType(text(detail.to_type), 1)}, fields re-extracted`;
    case 'document.type_corrected':
      return `Confirmed as ${formatDocumentType(text(detail.to_type), 1)}`;
    case 'document.analyzed':
      return text(detail.document_type) && `Classified as ${formatDocumentType(text(detail.document_type))}`;
    case 'document.failed': {
      // The stored reason can carry an exception's text: a short title here, the reason itself under Details.
      const reason = text(detail.failure_reason);
      return reason && describeFailureReason(reason).title;
    }
    case 'document.field_edited': {
      const changes = fieldChanges(event);
      if (changes === null) {
        const fields = Array.isArray(detail.fields) ? (detail.fields as string[]) : [];
        return fields.length > 0 ? fields.map(humanizeFieldKey).join(', ') : null;
      }
      return changes.length === 0 ? 'Saved and verified the fields; no values changed' : null;
    }
    case 'document.detected':
      return `Verdict: ${text(detail.verdict) ?? 'unknown'} · ${count(detail.findings)} finding(s)`;
    case 'detection.failed':
      // A cancel's error is a sentence ("Cancelled by loan officer"); a crash's is the exception, under Details.
      return event.outcome === 'cancelled' ? text(detail.error) : 'The run stopped before it finished';
    case 'detection.check_run': {
      if (event.outcome !== 'success') return "The check couldn't run";
      if (text(detail.skipped_reason)) return `Skipped: ${text(detail.skipped_reason)}`;
      if (detail.unable_to_assess === true) return 'Could not assess this document';
      const likelihood = text(detail.likelihood);
      if (likelihood) return `AI-generation likelihood: ${likelihood}`;
      return typeof detail.finding_count === 'number' ? `${detail.finding_count} finding(s)` : null;
    }
    case 'finding_review.saved': {
      const parts = [
        count(detail.real) && `${count(detail.real)} real issue(s)`,
        count(detail.false_alarms) && `${count(detail.false_alarms)} false alarm(s)`,
        count(detail.cleared) && `${count(detail.cleared)} cleared`,
      ].filter(Boolean);
      const where = detail.scope === 'case' ? 'Cross-document findings' : null;
      return [where, parts.join(' · ')].filter(Boolean).join(': ') || null;
    }
    case 'finding_review.missed_reported':
      return text(detail.check_name) && `Against ${formatCheck(text(detail.check_name))}`;
    case 'finding_review.missed_edited':
      return text(detail.check_name) && `${formatCheck(text(detail.from_check))} → ${formatCheck(text(detail.check_name))}`;
    case 'finding_review.removed': {
      const what = detail.kind === 'missed' ? 'Missed issue' : `Verdict (${detail.verdict === 'correct' ? 'real issue' : 'false alarm'})`;
      return text(detail.check_name) ? `${what} on ${formatCheck(text(detail.check_name))}` : what;
    }
    case 'case.analysis.completed':
      // The risk level is shown as a badge before this (riskOf).
      if (event.outcome !== 'success') return "The analysis didn't finish";
      return `${count(detail.findings)} cross-document finding(s)`;
    default:
      return null;
  }
}

// The raw text behind a failed entry's plain line (describe): an exception's text, for the console only.
function rawError(event: AuditEvent): string | null {
  if (event.outcome !== 'failed') return null;
  if (event.type === 'document.failed') return text(event.detail.failure_reason);
  const errorTypes = ['detection.failed', 'detection.check_run', 'case.analysis.completed'];
  return errorTypes.includes(event.type) ? text(event.detail.error) : null;
}

// Who the entry belongs to: the officer, or the System (an AI agent's run) and the officer who started it.
function attribution(event: AuditEvent): string {
  const officer = officerOf(event);
  if (event.actor_type === 'user') return `By ${officerName(event.actor)}`;
  return officer ? `System · started by ${officerName(officer)}` : 'System';
}

// Every recorded action on the case, oldest first: uploads, classification and extraction, field edits,
// approvals and type changes, detection runs and case analyses, each with its time and the loan officer behind it.
export function AuditTrailCard({
  caseId,
  caseNumber,
  documents,
}: {
  caseId: string;
  // The case's GLDB number, naming a note on the whole case as the Notes card does.
  caseNumber?: string | null;
  documents: AuditDocument[];
}) {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  // Only the newest load may land: switching case mid-load must not show the previous case's trail.
  const latestLoad = useRef(0);

  const load = useCallback(async () => {
    const thisLoad = ++latestLoad.current;
    setIsRefreshing(true);
    setLoadError(null);
    try {
      const trail = (await listCaseAuditTrail(caseId)).filter(isShown);
      if (thisLoad === latestLoad.current) {
        setEvents(trail);
        for (const event of trail) {
          const raw = rawError(event);
          if (raw) console.error(`Audit entry ${event.type} (${event.occurred_at}) failed:`, raw);
        }
      }
    } catch (caught) {
      if (thisLoad === latestLoad.current) {
        setLoadError(caught instanceof Error ? caught.message : 'Failed to load the audit trail');
      }
    } finally {
      if (thisLoad === latestLoad.current) setIsRefreshing(false);
    }
  }, [caseId]);

  useEffect(() => {
    setEvents(null);
    void load();
  }, [load]);

  const filenames = useMemo(() => new Map(documents.map((document) => [document.id, document.filename])), [documents]);

  // What the entry's pill names: its document, or for a note or a finding review on the whole case, the case.
  function documentName(event: AuditEvent): string | null {
    const recorded = text(event.detail.filename);
    if (event.document_id) return filenames.get(event.document_id) ?? recorded ?? 'Removed document';
    if (event.type.startsWith('note.') || event.type.startsWith('finding_review.')) return caseNumber ?? 'This case';
    return recorded;
  }

  return (
    <section className="summary-card audit-card">
      <div className="summary-card-header">
        <div className="summary-card-title">
          <span className="summary-icon summary-icon--audit"><Icon name="audit" size={20} /></span>
          <h2>
            Case Audit Trail{events && events.length > 0 && <span className="audit-count"> · {events.length}</span>}
          </h2>
        </div>
        <button type="button" className="text-button" onClick={() => void load()} disabled={isRefreshing}>
          {isRefreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {loadError && <ErrorMessage className="card-error" message={loadError} />}

      {events === null && !loadError && <p className="audit-empty">Loading the audit trail…</p>}

      {events?.length === 0 && (
        <div className="summary-message summary-message--info">
          <span className="summary-message-icon">ⓘ</span>
          <div>
            <strong>No audit events available yet.</strong>
            <p>Case activity history will appear here once actions are recorded.</p>
          </div>
        </div>
      )}

      {events && events.length > 0 && (
        <>
          <ul className="audit-legend" aria-label="Timeline key">
            {LEGEND.map(({ marker, label }) => (
              <li key={marker}>
                <span className={`audit-marker audit-marker--${marker}`} aria-hidden="true" />
                {label}
              </li>
            ))}
          </ul>
          <div className="audit-timeline" aria-label="Audit trail, oldest first">
            {groupByDay(events).map(({ day, events: dayEvents }) => (
              <section key={day} className="audit-day">
                <h3 className="audit-day-heading">{day}</h3>
                <ol className="audit-list">
                  {dayEvents.map((event) => {
                    const documentLabel = documentName(event);
                    const summary = describe(event);
                    const risk = riskOf(event);
                    const changes = fieldChanges(event);
                    return (
                      <li
                        key={event.event_id}
                        className={`audit-entry audit-entry--${event.outcome}`}
                        data-testid="audit-entry"
                      >
                        <span
                          className={`audit-marker audit-marker--${event.actor_type} audit-marker--${markerOutcome(event)}`}
                          aria-hidden="true"
                        />
                        <div className="audit-entry-card">
                          <div className="audit-entry-main">
                            <time
                              className="audit-time"
                              dateTime={event.occurred_at}
                              title={formatTimestamp(event.occurred_at)}
                            >
                              {formatTimeOfDay(event.occurred_at)}
                            </time>
                            <strong className="audit-action" data-testid="audit-action">{actionLabel(event)}</strong>
                            {documentLabel && <span className="audit-document" data-testid="audit-document">{documentLabel}</span>}
                            {event.outcome !== 'success' && (
                              <span className={`audit-outcome audit-outcome--${event.outcome}`}>
                                {OUTCOME_LABELS[event.outcome]}
                              </span>
                            )}
                          </div>
                          {(risk || summary) && (
                            <p className="audit-summary">
                              {risk && (
                                <span className={`risk-badge risk-badge--${risk} audit-risk-badge`} data-testid="risk-badge">{riskLevel(risk)} Risk</span>
                              )}
                              {summary}
                            </p>
                          )}
                          {changes && changes.length > 0 && (
                            <ul className="audit-changes" aria-label="Field changes">
                              {changes.map((change) => (
                                <li key={change.field} data-testid="audit-change">
                                  <span className="audit-change-field">{humanizeFieldKey(change.field)}:</span>
                                  <del>{fieldValue(change.from)}</del>
                                  <span aria-label="changed to">→</span>
                                  <ins>{fieldValue(change.to)}</ins>
                                  {(change.added?.length || change.removed?.length) ? (
                                    <ul className="audit-list-items">
                                      {change.removed?.map((item, index) => (
                                        <li key={`removed-${index}`} className="audit-list-item--removed" data-testid="audit-list-item">
                                          <span aria-label="Removed item">−</span> {listItem(item)}
                                        </li>
                                      ))}
                                      {change.added?.map((item, index) => (
                                        <li key={`added-${index}`} className="audit-list-item--added" data-testid="audit-list-item">
                                          <span aria-label="Added item">+</span> {listItem(item)}
                                        </li>
                                      ))}
                                    </ul>
                                  ) : null}
                                </li>
                              ))}
                            </ul>
                          )}
                          <div className="audit-meta">
                            <span className="audit-officer" data-testid="audit-officer">{attribution(event)}</span>
                            {event.duration_ms !== null && <span>took {formatDuration(event.duration_ms)}</span>}
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ol>
              </section>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
