import type { AdditionalDocument, DocumentRecord } from '../../services/uploadService';
import {
  TERMINAL_DOCUMENT_STATUSES,
  canCancelDocument,
  canRetryDocument,
  documentDetectionStage,
  formatDocumentStatus,
  formatDocumentStatusLabel,
  formatDocumentLabel,
  isDocumentStuckProcessing,
} from '../../services/uploadService';
import { LiveBadge } from './LiveBadge';
import { useDetectionOutcome } from './useDetectionOutcome';
import { useState } from 'react';
import { CANCELLED_REASON, documentGroup, missingAdditional, missingRequirements, type Coverage, type DocumentGroup } from './documentChecklist';
import { REQUIRED_DOCUMENT_TYPES } from '../../services/uploadService';
import { requestKey, requestedDocumentName } from './requestedDocument';
import type { useDocumentRowActions } from './useDocumentRowActions';
import type { useDocumentSelection } from './useDocumentSelection';
import { ErrorMessage } from '../../components/ErrorMessage';
import { Icon } from '../../components/Icon';

export type DocumentSelection = ReturnType<typeof useDocumentSelection>;
export type DocumentRowActions = ReturnType<typeof useDocumentRowActions>;

interface ExtractedDocumentsCardProps {
  extractedDocuments: DocumentRecord[];
  // Why each document that needs the officer's review does (by document id); the others have no entry.
  reviewReasons?: Map<string, string>;
  // Which required documents the case already has, so the card can list the ones it still lacks.
  fulfilledRequirements: Set<string>;
  coverage: Coverage;
  // The documents already decided on, listed under their own folded groups at the foot of the table.
  approvedDocuments: DocumentRecord[];
  rejectedDocuments: DocumentRecord[];
  wsConnected: boolean;
  onOpenDocument: (documentId: string) => void;
  selection: DocumentSelection;
  rowActions: DocumentRowActions;
  // A finalised case: documents can be opened, but nothing in the list can be changed.
  readOnly?: boolean;
  // What the latest case analysis asks the client for (the Additional Documents tab), listed so they are uploaded here.
  requestedDocuments?: AdditionalDocument[];
  // The group to open first, when the officer came here for it (the Additional Documents tab's Upload in Documents).
  initialGroup?: GroupId;
  // Goes to one request on the Additional Documents tab (by requestKey), where it says why it is asked for.
  onOpenRequest?: (key: string) => void;
}

// Extracted documents awaiting the officer: bulk select, approve, move and delete, plus per-row retry and cancel.
export function ExtractedDocumentsCard({
  extractedDocuments,
  reviewReasons,
  fulfilledRequirements,
  coverage,
  approvedDocuments,
  rejectedDocuments,
  wsConnected,
  onOpenDocument,
  selection,
  rowActions,
  readOnly = false,
  requestedDocuments = [],
  initialGroup,
  onOpenRequest,
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

  const requiredReceived = REQUIRED_DOCUMENT_TYPES.filter((requirement) => fulfilledRequirements.has(requirement.value)).length;
  const missing = missingRequirements(fulfilledRequirements, coverage);
  const optionalMissing = missingAdditional(fulfilledRequirements);
  const groups: Record<DocumentGroup, DocumentRecord[]> = { review: [], processing: [], pending: [] };
  extractedDocuments.forEach((document) => groups[documentGroup(document, reviewReasons?.has(document.id) ?? false)].push(document));

  // Documents still being worked on wait for approval too: they sit first under Pending Approval, with a spinner.
  const pendingDocuments = [...groups.processing, ...groups.pending];

  // Only one category is open at a time. Until the officer picks one, the first category that has rows is open, other
  // than Optional (optional documents aren't what the officer came to do). Opening one closes the rest; opening
  // the open one closes it. A choice whose category has since emptied falls back to that first one.
  const present: GroupId[] = [
    missing.length > 0 && 'missing',
    requestedDocuments.length > 0 && 'requested',
    optionalMissing.length > 0 && 'additional',
    groups.review.length > 0 && 'review',
    pendingDocuments.length > 0 && 'pending',
    approvedDocuments.length > 0 && 'approved',
    rejectedDocuments.length > 0 && 'rejected',
  ].filter((id): id is GroupId => id !== false);
  const defaultGroup = present.find((id) => id !== 'additional') ?? null;
  const [chosenGroup, setChosenGroup] = useState<GroupId | null | undefined>(initialGroup);
  const openGroup = chosenGroup === undefined || (chosenGroup !== null && !present.includes(chosenGroup)) ? defaultGroup : chosenGroup;
  const toggle = (id: GroupId) => setChosenGroup(openGroup === id ? null : id);

  const rowsFor = (documents: DocumentRecord[]) =>
    [...documents].sort(byDocumentType).map((document) => {
      const group = documentGroup(document, reviewReasons?.has(document.id) ?? false);
      return (
        <ExtractedDocumentRow
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
    });

  return (
    <section className="documents-card" data-testid="documents-card">
      <div className="documents-card-header">
        <div className="documents-card-title">
          <span className="documents-card-icon"><Icon name="documents" size={20} /></span>
          <span>Documents</span>
          <span className="checklist-progress">
            <strong>{requiredReceived}</strong> of {REQUIRED_DOCUMENT_TYPES.length} required
            <span className="checklist-bar" aria-hidden="true">
              <i style={{ width: `${(requiredReceived / REQUIRED_DOCUMENT_TYPES.length) * 100}%` }} />
            </span>
          </span>
        </div>

        <div className="documents-card-actions">
          {!readOnly && (
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
          )}
          <LiveBadge connected={wsConnected} />
        </div>
      </div>

      {[approveError, cancelActionError, retryActionError].map(
        (actionError, index) => actionError && <ErrorMessage key={index} className="toast-error" message={actionError} />,
      )}

      <div className={`documents-table documents-table--selectable${selectedDocumentIds.size > 0 ? ' documents-table--has-selection' : ''}`}>
        <div className="documents-table-header">
          <label className="document-select-cell">
            <input
              type="checkbox"
              aria-label="Select all extracted files"
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
        </div>

        {missing.length > 0 && (
          <>
            <GroupToggle id="missing" label="Missing" count={missing.length} open={openGroup === 'missing'} onToggle={toggle} />
            {openGroup === 'missing' && missing.map((requirement) => (
              <div key={requirement.value} className="checklist-missing-row" data-testid="missing-document">
                <span className="document-select-cell" aria-hidden="true">
                  <span className="row-mark row-mark--missing" />
                </span>
                <span className="document-type-cell">{requirement.label}</span>
                <span className="document-filename">
                  <span className="document-filename-line checklist-hint">{requirement.hint}</span>
                </span>
                <span className="document-status-cell">
                  <span className="status-pill">Missing</span>
                </span>
              </div>
            ))}
          </>
        )}

        {requestedDocuments.length > 0 && (
          <div className="checklist-requested">
            <GroupToggle id="requested" label="Requested" count={requestedDocuments.length} note="by the case analysis" open={openGroup === 'requested'} onToggle={toggle} />
            {openGroup === 'requested' && requestedDocuments.map((item) => (
              <div key={requestKey(item)} className="checklist-missing-row checklist-requested-row" data-testid="requested-document">
                <span className="document-select-cell" aria-hidden="true">
                  <span className="row-mark row-mark--missing" />
                </span>
                {/* The document's name, and the request as the Additional tab heads it, linking to it there. */}
                <span className="document-type-cell">{requestedDocumentName(item.title)}</span>
                <span className="document-filename">
                  {onOpenRequest ? (
                    <button type="button" className="checklist-request-link" onClick={() => onOpenRequest(requestKey(item))}>
                      <span className="checklist-request-link-text">{item.title}</span>
                      <span className="checklist-request-link-arrow" aria-hidden="true">→</span>
                    </button>
                  ) : (
                    <span className="document-filename-line checklist-hint">{item.title}</span>
                  )}
                </span>
                <span className="document-status-cell">
                  <span className="status-pill status-pill--requested">Requested</span>
                </span>
              </div>
            ))}
          </div>
        )}

        {optionalMissing.length > 0 && (
          <div className="checklist-additional">
            <GroupToggle id="additional" label="Optional" count={optionalMissing.length} open={openGroup === 'additional'} onToggle={toggle} />
            {openGroup === 'additional' && optionalMissing.map((label) => (
              <div key={label} className="checklist-missing-row checklist-optional-row" data-testid="optional-document">
                <span className="document-select-cell" aria-hidden="true">
                  <span className="row-mark row-mark--missing" />
                </span>
                <span className="document-type-cell">{label}</span>
                <span className="document-filename">
                  <span className="document-filename-line checklist-hint">Not needed unless the case calls for it</span>
                </span>
                <span className="document-status-cell">
                  <span className="status-pill">Optional</span>
                </span>
              </div>
            ))}
          </div>
        )}

        {groups.review.length > 0 && (
          <>
            <GroupToggle id="review" label="Needs Review" count={groups.review.length} open={openGroup === 'review'} onToggle={toggle} />
            {openGroup === 'review' && rowsFor(groups.review)}
          </>
        )}

        {pendingDocuments.length > 0 && (
          <>
            <GroupToggle id="pending" label="Pending Approval" count={pendingDocuments.length} open={openGroup === 'pending'} onToggle={toggle} />
            {openGroup === 'pending' && rowsFor(pendingDocuments)}
          </>
        )}

        {(['approved', 'rejected'] as const).map((kind) => {
          const decided = kind === 'approved' ? approvedDocuments : rejectedDocuments;
          if (decided.length === 0) return null;
          const isOpen = openGroup === kind;
          return (
            <div key={kind} className="checklist-decided">
              <GroupToggle id={kind} label={kind === 'approved' ? 'Approved' : 'Rejected'} count={decided.length} open={isOpen} onToggle={toggle} />
              {isOpen && [...decided].sort(byDocumentType).map((document) => (
                <DecidedRow key={document.id} document={document} kind={kind} onOpenDocument={onOpenDocument} />
              ))}
            </div>
          );
        })}
      </div>
    </section>
  );
}

// A category's documents A to Z by the type as listed (so "NRIC" comes before "NRIC (Proof of Address)"), then by
// filename. One not yet classified has no type to sort by, and goes last.
function byDocumentType(a: DocumentRecord, b: DocumentRecord): number {
  if (!a.document_type !== !b.document_type) return a.document_type ? -1 : 1;
  const type = a.document_type
    ? formatDocumentLabel(a).localeCompare(formatDocumentLabel(b), undefined, { sensitivity: 'base' })
    : 0;
  return type || a.filename.localeCompare(b.filename, undefined, { numeric: true, sensitivity: 'base' });
}

function statusPillClass(status: DocumentRecord['status'], isStuck: boolean, isTerminal: boolean): string {
  if (status === 'analyzed') return 'status-pill--ok';
  if (status === 'failed' || status === 'rejected') return 'status-pill--fail';
  if (status === 'cancelled') return '';
  if (isStuck) return 'status-pill--warn';
  return isTerminal ? '' : 'status-pill--proc';
}

function ExtractedDocumentRow({
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
  // Set in Needs Review: why the document waits on the officer.
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

  return (
    <div
      className={`documents-table-row documents-table-row--clickable${reason ? ' documents-table-row--review' : ''}`}
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
        {reason && <span className="document-review-reason">{reason}</span>}
      </span>

      <span className="document-status-cell">
        <span className={`status-pill ${statusPillClass(status, isStuck, isTerminal)}`}>
          {formatDocumentStatus(status)}
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

    </div>
  );
}

// The status pill for a decided document. An approved document is on its way through fraud detection, so it reads
// "Scanning" (a grey pill) from approval until the scan finishes; it never rests on a bare "Approved" that hides that
// scanning is still to come. Once scanned it reads "Clean" when detection found nothing and "Detected" when it found
// at least one issue ("Scanned" while the report loads). A document with no type skips detection and stays "Approved".
function ApprovedStatusPill({ document, kind }: { document: DocumentRecord; kind: 'approved' | 'rejected' }) {
  const outcome = useDetectionOutcome(document.id, kind === 'approved' ? document.status : '');
  if (kind === 'rejected') {
    return <span className="status-pill status-pill--fail">Rejected</span>;
  }
  if (documentDetectionStage(document.status, false, document.document_type) === 'detected') {
    if (outcome === 'clean') return <span className="status-pill status-pill--ok">Clean</span>;
    if (outcome === 'findings') return <span className="status-pill status-pill--warn">Detected</span>;
    return <span className="status-pill">Scanned</span>;
  }
  return <span className="status-pill">{formatDocumentStatusLabel(document.status, false, document.document_type)}</span>;
}

// An approved or rejected document, listed read-only under its folded group: its type, filename and where it stands.
function DecidedRow({
  document,
  kind,
  onOpenDocument,
}: {
  document: DocumentRecord;
  kind: 'approved' | 'rejected';
  onOpenDocument: (documentId: string) => void;
}) {
  return (
    <div
      className="documents-table-row documents-table-row--clickable"
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
        <ApprovedStatusPill document={document} kind={kind} />
      </span>
    </div>
  );
}

export type GroupId = DocumentGroup | 'missing' | 'requested' | 'additional' | 'approved' | 'rejected';

// A group's label as a button that folds and unfolds it; the chevron sits in the first column, over the marks. The group's
// colour (documents-table.css, by id) is how a category reads at a glance.
function GroupToggle({
  id,
  label,
  count,
  note,
  open,
  onToggle,
}: {
  id: GroupId;
  label: string;
  count: number;
  note?: string;
  open: boolean;
  onToggle: (id: GroupId) => void;
}) {
  return (
    <button
      type="button"
      className={`documents-table-group documents-table-group--toggle documents-table-group--${id}`}
      data-testid="document-group"
      aria-expanded={open}
      onClick={() => onToggle(id)}
    >
      <svg className="group-chevron" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M9 6l6 6-6 6" />
      </svg>
      {label} <span className="num">{count}</span>
      {note && <span className="muted-note">{note}</span>}
    </button>
  );
}
