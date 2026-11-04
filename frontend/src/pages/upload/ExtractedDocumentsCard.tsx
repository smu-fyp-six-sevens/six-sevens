import type { DocumentRecord } from '../../services/uploadService';
import {
  TERMINAL_DOCUMENT_STATUSES,
  canCancelDocument,
  canRetryDocument,
  formatDocumentStatus,
  formatDocumentType,
  isDocumentStuckProcessing,
} from '../../services/uploadService';
import { LiveBadge } from './LiveBadge';
import { useState } from 'react';
import { CANCELLED_REASON, documentGroup, missingAdditional, missingRequirements, type Coverage, type DocumentGroup } from './documentChecklist';
import { REQUIRED_DOCUMENT_TYPES } from '../../services/uploadService';
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
  const [openGroups, setOpenGroups] = useState<Record<GroupId, boolean>>({
    review: true,
    missing: true,
    processing: true,
    pending: true,
    additional: true,
    approved: false,
    rejected: false,
  });
  const missing = missingRequirements(fulfilledRequirements, coverage);
  const optionalMissing = missingAdditional(fulfilledRequirements);
  const groups: Record<DocumentGroup, DocumentRecord[]> = { review: [], processing: [], pending: [] };
  extractedDocuments.forEach((document) => groups[documentGroup(document, reviewReasons?.has(document.id) ?? false)].push(document));

  // Documents still being worked on wait for approval too: they sit first under Pending Approval, with a spinner.
  const pendingDocuments = [...groups.processing, ...groups.pending];

  const toggle = (id: GroupId) => setOpenGroups((open) => ({ ...open, [id]: !open[id] }));

  const rowsFor = (documents: DocumentRecord[]) =>
    documents.map((document) => {
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
    <section className="documents-card">
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
            <GroupToggle id="missing" label="Missing" count={missing.length} open={openGroups.missing} onToggle={toggle} />
            {openGroups.missing && missing.map((requirement) => (
              <div key={requirement.value} className="checklist-missing-row">
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

        {optionalMissing.length > 0 && (
          <div className="checklist-additional">
            <GroupToggle id="additional" label="Additional" count={optionalMissing.length} note="only if the case needs them" open={openGroups.additional} onToggle={toggle} />
            {openGroups.additional && optionalMissing.map((label) => (
              <div key={label} className="checklist-missing-row checklist-optional-row">
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
            <GroupToggle id="review" label="Needs Review" count={groups.review.length} open={openGroups.review} onToggle={toggle} tone="review" />
            {openGroups.review && rowsFor(groups.review)}
          </>
        )}

        {pendingDocuments.length > 0 && (
          <>
            <GroupToggle id="pending" label="Pending Approval" count={pendingDocuments.length} open={openGroups.pending} onToggle={toggle} />
            {openGroups.pending && rowsFor(pendingDocuments)}
          </>
        )}

        {(['approved', 'rejected'] as const).map((kind) => {
          const decided = kind === 'approved' ? approvedDocuments : rejectedDocuments;
          if (decided.length === 0) return null;
          const isOpen = openGroups[kind];
          return (
            <div key={kind} className="checklist-decided">
              <GroupToggle id={kind} label={kind === 'approved' ? 'Approved' : 'Rejected'} count={decided.length} open={isOpen} onToggle={toggle} />
              {isOpen && decided.map((document) => (
                <DecidedRow key={document.id} document={document} kind={kind} onOpenDocument={onOpenDocument} />
              ))}
            </div>
          );
        })}
      </div>
    </section>
  );
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
        {document.document_type ? formatDocumentType(document.document_type, document.classification_confidence) : 'Not classified'}
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
        <span className={`row-mark row-mark--${kind === 'approved' ? 'pending' : 'rejected'}`}>
          <Icon name={kind === 'approved' ? 'check' : 'x'} size={11} />
        </span>
      </span>
      <span className="document-type-cell">
        {document.document_type ? formatDocumentType(document.document_type, document.classification_confidence) : 'Not classified'}
      </span>
      <span className="document-filename">
        <span className="document-filename-line">{document.filename}</span>
      </span>
      <span className="document-status-cell">
        <span className={`status-pill ${kind === 'rejected' ? 'status-pill--fail' : document.status === 'detecting' ? 'status-pill--proc' : 'status-pill--ok'}`}>
          {kind === 'rejected' ? 'Rejected' : formatDocumentStatus(document.status)}
        </span>
      </span>
    </div>
  );
}

type GroupId = DocumentGroup | 'missing' | 'additional' | 'approved' | 'rejected';

// A group's label as a button that folds and unfolds it; the chevron sits in the first column, over the marks.
function GroupToggle({
  id,
  label,
  count,
  note,
  open,
  onToggle,
  tone,
}: {
  id: GroupId;
  label: string;
  count: number;
  note?: string;
  open: boolean;
  onToggle: (id: GroupId) => void;
  tone?: 'review';
}) {
  return (
    <button
      type="button"
      className={`documents-table-group documents-table-group--toggle${tone === 'review' ? ' documents-table-group--review' : ''}`}
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
