import type { DocumentRecord } from '../../services/uploadService';
import {
  TERMINAL_DOCUMENT_STATUSES,
  canCancelDocument,
  canRetryDocument,
  documentDetectionStage,
  formatDocumentStatus,
  formatDocumentLabel,
  isDocumentStuckProcessing,
} from '../../services/uploadService';
import { LiveBadge } from './LiveBadge';
import { useDetectionCounts, type DetectionCounts } from './useDetectionCounts';
import { useState, type ReactNode } from 'react';
import { CANCELLED_REASON, documentGroup, type DocumentGroup } from './documentChecklist';
import type { useDocumentRowActions } from './useDocumentRowActions';
import type { useDocumentSelection } from './useDocumentSelection';
import { ErrorMessage } from '../../components/ErrorMessage';
import { Icon } from '../../components/Icon';

export type DocumentSelection = ReturnType<typeof useDocumentSelection>;
export type DocumentRowActions = ReturnType<typeof useDocumentRowActions>;

interface ExtractedDocumentsCardProps {
  // Every document not yet decided on, cancelled ones included (they wait on the officer under Uploaded).
  extractedDocuments: DocumentRecord[];
  // Why each document that needs the officer's review does (by document id); the others have no entry.
  reviewReasons?: Map<string, string>;
  // Approved ones stay under Approved through detection, a failed or cancelled scan included.
  approvedDocuments: DocumentRecord[];
  rejectedDocuments: DocumentRecord[];
  wsConnected: boolean;
  onOpenDocument: (documentId: string) => void;
  selection: DocumentSelection;
  rowActions: DocumentRowActions;
  // A finalised case: documents can be opened, but nothing in the list can be changed.
  readOnly?: boolean;
}

type GroupId = 'uploaded' | 'approved' | 'rejected';

// The case's documents in one table: Uploaded (waiting on the officer or the pipeline), Approved (through detection),
// Rejected. Several groups can be open at once; Rejected starts folded.
export function ExtractedDocumentsCard({
  extractedDocuments,
  reviewReasons,
  approvedDocuments,
  rejectedDocuments,
  wsConnected,
  onOpenDocument,
  selection,
  rowActions,
  readOnly = false,
}: ExtractedDocumentsCardProps) {
  const {
    allExtractedSelected,
    clearDocumentSelection,
    eligibleForApprovalCount,
    handleSelectAllEligible,
    openMoveDialog,
    selectedApprovableCount,
    selectedDocumentIds,
    selectedExtractedDocuments,
    selectedMovableOrDeletableCount,
    setDocumentsToDelete,
    setSelectedDocumentIds,
  } = selection;
  const { approveError, cancelActionError, retryActionError, isApprovingSelected, handleApproveSelected } = rowActions;
  const counts = useDetectionCounts(approvedDocuments);

  const [open, setOpen] = useState<Record<GroupId, boolean>>({ uploaded: true, approved: true, rejected: false });
  const toggle = (id: GroupId) => setOpen((current) => ({ ...current, [id]: !current[id] }));

  const groupOf = (document: DocumentRecord) => documentGroup(document, reviewReasons?.has(document.id) ?? false);
  const byGroup: Record<DocumentGroup, DocumentRecord[]> = { review: [], pending: [], processing: [] };
  extractedDocuments.forEach((document) => byGroup[groupOf(document)].push(document));
  // What waits on the officer first, then what's ready to approve, then what's still being read.
  const uploaded = [
    ...[...byGroup.review].sort(byDocumentType),
    ...[...byGroup.pending].sort(byDocumentType),
    ...[...byGroup.processing].sort(byDocumentType),
  ];

  const scanned = approvedDocuments.filter((document) => counts.has(document.id));
  const withAnomalies = scanned.filter((document) => totalFindings(counts.get(document.id)) > 0).length;
  const scanning = approvedDocuments.filter((document) => approvedStage(document) === 'scanning').length;

  const uploadedNote = [
    byGroup.review.length > 0 && { text: `${byGroup.review.length} need review`, tone: 'review' },
    byGroup.pending.length > 0 && { text: `${byGroup.pending.length} ready to approve`, tone: 'pending' },
    byGroup.processing.length > 0 && { text: `${byGroup.processing.length} processing`, tone: 'processing' },
  ].filter((chip): chip is { text: string; tone: string } => chip !== false);
  const approvedNote = [
    withAnomalies > 0 && { text: `${withAnomalies} with anomalies`, tone: 'anomalies' },
    scanning > 0 && { text: `${scanning} scanning`, tone: 'processing' },
  ].filter((chip): chip is { text: string; tone: string } => chip !== false);

  const isEmpty = uploaded.length + approvedDocuments.length + rejectedDocuments.length === 0;

  // The bulk actions act only on uploaded documents, so they sit in the Uploaded band.
  const bulkToolbar = !readOnly && (
          <div className="bulk-icon-toolbar">
            {selectedDocumentIds.size > 0 && (
              <button type="button" className="bulk-icon-clear" title="Clear selection" onClick={clearDocumentSelection}>
                Clear ({selectedDocumentIds.size})
              </button>
            )}

            <button
              type="button"
              className="bulk-icon-btn"
              title="Select all eligible for approval"
              aria-label="Select all eligible for approval"
              disabled={eligibleForApprovalCount === 0}
              onClick={handleSelectAllEligible}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 11l3 3L22 4" />
                <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
              </svg>
              <span className="bulk-icon-count">{eligibleForApprovalCount}</span>
            </button>

            <button
              type="button"
              className="bulk-icon-btn bulk-icon-btn--approve"
              title="Approve selected"
              aria-label="Approve selected"
              disabled={isApprovingSelected || selectedApprovableCount === 0}
              onClick={() => void handleApproveSelected()}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 12l5 5L20 6" />
              </svg>
              <span className="bulk-icon-count">{selectedApprovableCount}</span>
            </button>

            <button
              type="button"
              className="bulk-icon-btn"
              title="Move selected to another case"
              aria-label="Move selected to another case"
              disabled={selectedMovableOrDeletableCount === 0}
              onClick={() => void openMoveDialog()}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 12h14" />
                <path d="M13 6l6 6-6 6" />
              </svg>
              <span className="bulk-icon-count">{selectedMovableOrDeletableCount}</span>
            </button>

            <button
              type="button"
              className="bulk-icon-btn bulk-icon-btn--danger"
              title="Delete selected"
              aria-label="Delete selected"
              disabled={selectedMovableOrDeletableCount === 0}
              onClick={() => setDocumentsToDelete(
                selectedExtractedDocuments.filter((document) => TERMINAL_DOCUMENT_STATUSES.has(document.status)),
              )}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 7h16" />
                <path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
                <path d="M7 7l1 12a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1l1-12" />
                <path d="M10 11v6" />
                <path d="M14 11v6" />
              </svg>
              <span className="bulk-icon-count">{selectedMovableOrDeletableCount}</span>
            </button>
          </div>
  );

  return (
    <section className="documents-card" data-testid="documents-card">
      <div className="documents-card-header">
        <div className="documents-card-title">
          <span className="documents-card-icon"><Icon name="documents" size={20} /></span>
          <span>Documents</span>
        </div>
        <LiveBadge connected={wsConnected} />
      </div>

      {[approveError, cancelActionError, retryActionError].map(
        (actionError, index) => actionError && <ErrorMessage key={index} className="toast-error" message={actionError} />,
      )}

      <div className={`documents-table documents-table--selectable${selectedDocumentIds.size > 0 ? ' documents-table--has-selection' : ''}`}>
        {isEmpty && <p className="documents-table-empty">No documents yet. Upload them above; the checklist at the top shows what the case needs.</p>}

        {uploaded.length > 0 && (
          <>
            <GroupToggle id="uploaded" label="Uploaded" count={uploaded.length} chips={uploadedNote} open={open.uploaded} onToggle={toggle} actions={open.uploaded ? bulkToolbar : undefined} />
            {open.uploaded && (
              <div className="documents-table-header documents-table-header--uploaded">
                <label className="document-select-cell">
                  <input
                    type="checkbox"
                    aria-label="Select all uploaded files"
                    disabled={readOnly}
                    checked={allExtractedSelected}
                    onChange={() =>
                      setSelectedDocumentIds(
                        allExtractedSelected ? new Set() : new Set(extractedDocuments.map((document) => document.id)),
                      )
                    }
                  />
                </label>
                <span>Document Type</span>
                <span>Filename</span>
                <span>Status</span>
                <span>Remarks</span>
              </div>
            )}
            {open.uploaded && uploaded.map((document) => {
              const group = groupOf(document);
              return (
                <UploadedRow
                  key={document.id}
                  document={document}
                  group={group}
                  reason={group === 'review' ? (reviewReasons?.get(document.id) ?? CANCELLED_REASON) : undefined}
                  isSelected={selectedDocumentIds.has(document.id)}
                  onToggleSelected={selection.toggleDocumentSelected}
                  onOpenDocument={onOpenDocument}
                  rowActions={rowActions}
                  readOnly={readOnly}
                />
              );
            })}
          </>
        )}

        {approvedDocuments.length > 0 && (
          <>
            <GroupToggle id="approved" label="Approved" count={approvedDocuments.length} chips={approvedNote} open={open.approved} onToggle={toggle} />
            {open.approved && (
              <div className="documents-table-header documents-table-header--approved">
                <span className="document-select-cell" aria-hidden="true" />
                <span>Document Type</span>
                <span>Filename</span>
                <span>Status</span>
                <span>Remarks</span>
              </div>
            )}
            {open.approved && [...approvedDocuments].sort(byDocumentType).map((document) => (
              <DecidedRow key={document.id} document={document} kind="approved" counts={counts.get(document.id)} onOpenDocument={onOpenDocument} />
            ))}
          </>
        )}

        {rejectedDocuments.length > 0 && (
          <>
            <GroupToggle id="rejected" label="Rejected" count={rejectedDocuments.length} note="not used by the case analysis" open={open.rejected} onToggle={toggle} />
            {open.rejected && [...rejectedDocuments].sort(byDocumentType).map((document) => (
              <DecidedRow key={document.id} document={document} kind="rejected" onOpenDocument={onOpenDocument} />
            ))}
          </>
        )}
      </div>
    </section>
  );
}

// A group's documents A to Z by the type as listed (so "NRIC" comes before "NRIC (Proof of Address)"), then by
// filename. One not yet classified has no type to sort by, and goes last.
function byDocumentType(a: DocumentRecord, b: DocumentRecord): number {
  if (!a.document_type !== !b.document_type) return a.document_type ? -1 : 1;
  const type = a.document_type
    ? formatDocumentLabel(a).localeCompare(formatDocumentLabel(b), undefined, { sensitivity: 'base' })
    : 0;
  return type || a.filename.localeCompare(b.filename, undefined, { numeric: true, sensitivity: 'base' });
}

function totalFindings(counts: DetectionCounts | undefined): number {
  return counts ? counts.high + counts.medium + counts.low + counts.info : 0;
}

function approvedStage(document: DocumentRecord) {
  return documentDetectionStage(
    document.status,
    document.has_detection_report ?? false,
    document.document_type,
    document.has_detection_error ?? false,
  );
}

function statusPillClass(status: DocumentRecord['status'], isStuck: boolean, isTerminal: boolean): string {
  if (status === 'analyzed') return 'status-pill--ok';
  if (status === 'failed' || status === 'rejected') return 'status-pill--fail';
  if (status === 'cancelled') return 'status-pill--warn';
  if (isStuck) return 'status-pill--warn';
  return isTerminal ? '' : 'status-pill--proc';
}

function UploadedRow({
  document,
  group,
  reason,
  isSelected,
  onToggleSelected,
  onOpenDocument,
  rowActions,
  readOnly,
}: {
  document: DocumentRecord;
  group: DocumentGroup;
  // Set when the document waits on the officer: why.
  reason?: string;
  isSelected: boolean;
  onToggleSelected: (documentId: string) => void;
  onOpenDocument: (documentId: string) => void;
  rowActions: DocumentRowActions;
  readOnly: boolean;
}) {
  const { cancellingDocumentId, retryingDocumentId, handleCancelDocument, handleRetryDocument } = rowActions;
  const status = document.status;
  const isTerminal = TERMINAL_DOCUMENT_STATUSES.has(status);
  const isStuck = isDocumentStuckProcessing(document);
  const isCancellingRow = cancellingDocumentId === document.id;
  const isRetryingRow = retryingDocumentId === document.id;
  // An extracted document reads by what it waits on: the officer's review (why, under the pill) or their approval.
  const needsReview = status === 'analyzed' && group === 'review';
  const statusLabel = needsReview
    ? 'Needs review'
    : status === 'analyzed' && group === 'pending' ? 'Ready to approve' : formatDocumentStatus(status);
  const pillClass = needsReview ? 'status-pill--review' : statusPillClass(status, isStuck, isTerminal);
  // Why it needs the officer, and for a failed or stuck run what to do about it.
  const issue = reason
    ?? (status === 'failed' ? "Couldn't be processed. Retry or reject it." : null)
    ?? (isStuck ? 'This is taking too long. Please retry it.' : null);

  return (
    <div
      className={`documents-table-row documents-table-row--uploaded documents-table-row--clickable${reason ? ' documents-table-row--review' : ''}`}
      data-testid="document-row"
      role="button"
      tabIndex={0}
      aria-label={`View ${document.filename}`}
      onClick={() => onOpenDocument(document.id)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onOpenDocument(document.id);
        }
      }}
    >
      <label
        className="document-select-cell"
        // A <label> wrapping the checkbox makes the whole padded cell a click target, not just the tiny.
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.stopPropagation()}
      >
        {/* The row's mark is what the first column shows; the checkbox takes its place on hover, once a selection
            has begun, and for keyboard focus, so every row starts with the same kind of thing. */}
        <span className={`row-mark row-mark--${group}`} aria-hidden="true">
          {group === 'review' ? (
            <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" aria-hidden="true">
              <path d="M12 6v8M12 18.5v.01" />
            </svg>
          ) : group === 'pending' ? (
            <Icon name="check" size={11} />
          ) : null}
        </span>
        <input
          type="checkbox"
          checked={isSelected}
          aria-label={`Select ${document.filename}`}
          disabled={readOnly}
          onChange={() => onToggleSelected(document.id)}
        />
      </label>

      <span className="document-type-cell">
        {document.document_type ? formatDocumentLabel(document) : 'Not classified'}
      </span>

      <span className="document-filename">
        <span className="document-filename-line">{document.filename}</span>
      </span>

      <span className="document-status-cell">
        <span className={`status-pill ${pillClass}`}>
          {statusLabel}
          {!isTerminal && '…'}
        </span>

        {isStuck && <span className="document-status-stuck-badge">Stuck</span>}

        {!readOnly && canRetryDocument(document) && (
          <button
            type="button"
            className="document-row-action-icon document-row-action-icon--retry"
            aria-label={isRetryingRow ? `Retrying ${document.filename}` : `Retry ${document.filename}`}
            title={isRetryingRow ? 'Retrying…' : 'Retry'}
            disabled={isRetryingRow}
            onClick={(event) => {
              event.stopPropagation();
              void handleRetryDocument(document.id);
            }}
            onKeyDown={(event) => event.stopPropagation()}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
              <path d="M20 11A8 8 0 1 0 18.6 16" />
              <path d="M20 5v6h-6" />
            </svg>
          </button>
        )}

        {!readOnly && canCancelDocument(status) && (
          <button
            type="button"
            className="document-row-action-icon document-row-action-icon--cancel"
            aria-label={isCancellingRow ? `Cancelling ${document.filename}` : `Cancel ${document.filename}`}
            title={isCancellingRow ? 'Cancelling…' : 'Cancel'}
            disabled={isCancellingRow}
            onClick={(event) => {
              event.stopPropagation();
              void handleCancelDocument(document.id);
            }}
            onKeyDown={(event) => event.stopPropagation()}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round">
              <path d="M6 6l12 12" />
              <path d="M18 6L6 18" />
            </svg>
          </button>
        )}
      </span>

      <span className="document-issue-cell">
        {issue ? (
          <span className="document-issue" title={issue}>{issue}</span>
        ) : (
          <span className="document-remark">
            {group === 'pending'
              ? 'Document ready for approval'
              : status === 'extracting' ? 'Extracting…' : status === 'processing' ? 'Classifying…' : 'Waiting to start…'}
          </span>
        )}
      </span>
    </div>
  );
}

// Where an approved document is in detection, and a rejected one's status. "Scanning" from approval until the scan
// ends, so it never rests on a bare "Approved" that hides the scan still to come; then Clean or Flagged.
function DecidedStatus({
  document,
  kind,
  counts,
}: {
  document: DocumentRecord;
  kind: 'approved' | 'rejected';
  counts?: DetectionCounts;
}) {
  if (kind === 'rejected') return <span className="status-pill status-pill--fail">Rejected</span>;
  const stage = approvedStage(document);
  if (stage === 'detected') {
    if (!counts) return <span className="status-pill status-pill--ok">Scanned</span>;
    return totalFindings(counts) > 0
      ? <span className="status-pill status-pill--warn">Flagged</span>
      : <span className="status-pill status-pill--ok">Clean</span>;
  }
  if (stage === 'failed') return <span className="status-pill status-pill--fail">Scan failed or stopped</span>;
  if (stage === 'skipped') return <span className="status-pill">Not scanned</span>;
  return <span className="status-pill status-pill--proc">Scanning…</span>;
}

// What the scan means for the officer: what to do on a failed scan, and how much a flagged one found.
function ApprovedRemark({ document, counts }: { document: DocumentRecord; counts?: DetectionCounts }) {
  const stage = approvedStage(document);
  if (stage === 'failed') return <span className="document-issue">The scan didn't finish. Open it to retry.</span>;
  if (stage === 'skipped') return <span className="document-remark">Not scanned for tampering.</span>;
  if (stage !== 'detected') return <span className="document-remark">Anomaly detection in progress</span>;
  const found = totalFindings(counts);
  if (!counts) return <span className="document-remark">Scan finished.</span>;
  return found > 0
    ? <span className="document-remark">{found} finding{found === 1 ? '' : 's'} to review.</span>
    // What was checked, not a promise the document is genuine.
    : <span className="document-remark">Checks completed, no anomalies flagged</span>;
}

// An approved or rejected document, read-only: its type, filename, where it stands and, once scanned, what was found.
function DecidedRow({
  document,
  kind,
  counts,
  onOpenDocument,
}: {
  document: DocumentRecord;
  kind: 'approved' | 'rejected';
  counts?: DetectionCounts;
  onOpenDocument: (documentId: string) => void;
}) {
  return (
    <div
      className="documents-table-row documents-table-row--approved documents-table-row--clickable"
      data-testid="document-row"
      role="button"
      tabIndex={0}
      aria-label={`View ${document.filename}`}
      onClick={() => onOpenDocument(document.id)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onOpenDocument(document.id);
        }
      }}
    >
      <span className="document-select-cell" aria-hidden="true">
        <span className={`row-mark row-mark--${kind}`}>
          <Icon name={kind === 'approved' ? 'check' : 'x'} size={11} />
        </span>
      </span>
      <span className="document-type-cell">
        {document.document_type ? formatDocumentLabel(document) : 'Not classified'}
      </span>
      <span className="document-filename">
        <span className="document-filename-line">{document.filename}</span>
      </span>
      <span className="document-status-cell">
        <DecidedStatus document={document} kind={kind} counts={counts} />
      </span>
      <span className="document-issue-cell">
        {kind === 'approved'
          ? <ApprovedRemark document={document} counts={counts} />
          : <span className="document-remark">Not used in the case analysis.</span>}
      </span>
    </div>
  );
}

// A group's label as a button that folds and unfolds it; the chevron sits in the first column, over the marks. The
// group's colour (documents-table.css, by id) is how it reads at a glance; its chips say what is inside while folded.
function GroupToggle({
  id,
  label,
  count,
  chips = [],
  note,
  open,
  onToggle,
  actions,
}: {
  id: GroupId;
  label: string;
  count: number;
  chips?: { text: string; tone: string }[];
  note?: string;
  open: boolean;
  onToggle: (id: GroupId) => void;
  // Controls that act on this group's documents, at the band's right end (outside the toggle button).
  actions?: ReactNode;
}) {
  return (
    <div className={`documents-table-band documents-table-group--${id}`}>
      <button
        type="button"
        className="documents-table-group documents-table-group--toggle"
        data-testid="document-group"
        aria-expanded={open}
        onClick={() => onToggle(id)}
      >
        <svg className="group-chevron" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M9 6l6 6-6 6" />
        </svg>
        {label} <span className="num">{count}</span>
        {chips.map((chip) => (
          <span key={chip.text} className={`group-chip group-chip--${chip.tone}`}>{chip.text}</span>
        ))}
        {note && <span className="muted-note">{note}</span>}
      </button>
      {actions && <div className="documents-table-band-actions">{actions}</div>}
    </div>
  );
}
