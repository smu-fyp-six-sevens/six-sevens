import type { DocumentCaseMatch, DocumentRecord } from '../../services/uploadService';
import {
  TERMINAL_DOCUMENT_STATUSES,
  canCancelDocument,
  canRetryDocument,
  formatDocumentStatus,
  formatDocumentType,
  formatRoutingOutcome,
  isDocumentStuckProcessing,
} from '../../services/uploadService';
import { LiveBadge } from './LiveBadge';
import type { useDocumentRowActions } from './useDocumentRowActions';
import type { useDocumentSelection } from './useDocumentSelection';
import { ErrorMessage } from '../../components/ErrorMessage';

export type DocumentSelection = ReturnType<typeof useDocumentSelection>;
export type DocumentRowActions = ReturnType<typeof useDocumentRowActions>;

interface ExtractedDocumentsCardProps {
  extractedDocuments: DocumentRecord[];
  caseMatches: Map<string, DocumentCaseMatch>;
  wsConnected: boolean;
  onOpenDocument: (documentId: string) => void;
  selection: DocumentSelection;
  rowActions: DocumentRowActions;
}

// Extracted documents awaiting the officer: bulk select, approve, move and delete, plus per-row retry and cancel.
export function ExtractedDocumentsCard({
  extractedDocuments,
  caseMatches,
  wsConnected,
  onOpenDocument,
  selection,
  rowActions,
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

  return (
    <section className="documents-card">
      <div className="documents-card-header">
        <div className="documents-card-title">
          <span className="documents-card-icon">▣</span>
          <span>Uploaded Documents</span>
        </div>

        <div className="documents-card-actions">
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
              <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
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
              <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round">
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
              <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
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
              <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 7h16" />
                <path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
                <path d="M7 7l1 12a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1l1-12" />
                <path d="M10 11v6" />
                <path d="M14 11v6" />
              </svg>
              <span className="bulk-icon-count">{selectedMovableOrDeletableCount}</span>
            </button>
          </div>
          <LiveBadge connected={wsConnected} />
        </div>
      </div>

      {[approveError, cancelActionError, retryActionError].map(
        (actionError, index) => actionError && <ErrorMessage key={index} className="toast-error" message={actionError} />,
      )}

      <div className="documents-table documents-table--selectable">
        <div className="documents-table-header">
          <label className="document-select-cell">
            <input
              type="checkbox"
              aria-label="Select all extracted files"
              checked={allExtractedSelected}
              onChange={() =>
                setSelectedDocumentIds(
                  allExtractedSelected ? new Set() : new Set(extractedDocuments.map((document) => document.id)),
                )
              }
            />
          </label>
          <span>Filename</span>
          <span>Document Type</span>
          <span>Status</span>
          <span>Routing Outcome</span>
        </div>

        {extractedDocuments.map((document) => (
          <ExtractedDocumentRow
            key={document.id}
            document={document}
            caseMatch={caseMatches.get(document.id)}
            isSelected={selectedDocumentIds.has(document.id)}
            onToggleSelected={selection.toggleDocumentSelected}
            onOpenDocument={onOpenDocument}
            rowActions={rowActions}
          />
        ))}
      </div>
    </section>
  );
}

function statusDotClass(status: DocumentRecord['status'], isStuck: boolean): string {
  if (status === 'analyzed') return 'status-dot--success';
  if (status === 'failed') return 'status-dot--failed';
  if (status === 'rejected') return 'status-dot--rejected';
  if (status === 'cancelled') return 'status-dot--cancelled';
  return isStuck ? 'status-dot--stuck' : '';
}

function ExtractedDocumentRow({
  document,
  caseMatch,
  isSelected,
  onToggleSelected,
  onOpenDocument,
  rowActions,
}: {
  document: DocumentRecord;
  caseMatch: DocumentCaseMatch | undefined;
  isSelected: boolean;
  onToggleSelected: (documentId: string) => void;
  onOpenDocument: (documentId: string) => void;
  rowActions: DocumentRowActions;
}) {
  const { cancellingDocumentId, retryingDocumentId, handleCancelDocument, handleRetryDocument } = rowActions;
  const status = document.status;
  const isTerminal = TERMINAL_DOCUMENT_STATUSES.has(status);
  const isStuck = isDocumentStuckProcessing(document);
  const isCancellingRow = cancellingDocumentId === document.id;
  const isRetryingRow = retryingDocumentId === document.id;
  const routingOutcome = formatRoutingOutcome(document, caseMatch);
  const manualReviewTitle = caseMatch?.status === 'manual_review' ? caseMatch.reason : 'Requires manual review';

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
      <label
        className="document-select-cell"
        // A <label> wrapping the checkbox makes the whole padded cell a click target, not just the tiny.
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.stopPropagation()}
      >
        <input
          type="checkbox"
          checked={isSelected}
          aria-label={`Select ${document.filename}`}
          onChange={() => onToggleSelected(document.id)}
        />
      </label>

      <span className="document-filename">
        {routingOutcome === 'Manual Review' && (
          <span className="document-manual-review-icon" title={manualReviewTitle} aria-label={manualReviewTitle}>
            ⚠
          </span>
        )}
        {document.filename}
      </span>

      <span className="document-type-cell">
        {formatDocumentType(document.document_type, document.classification_confidence)}
      </span>

      <span className="document-status-cell">
        <span className={`status-dot ${statusDotClass(status, isStuck)}`} />
        <span>
          {formatDocumentStatus(status)}
          {!isTerminal && '…'}
        </span>

        {isStuck && <span className="document-status-stuck-badge">Stuck</span>}

        {canRetryDocument(document) && (
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
            <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M20 11A8 8 0 1 0 18.6 16" />
              <path d="M20 5v6h-6" />
            </svg>
          </button>
        )}

        {canCancelDocument(status) && (
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
            <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M6 6l12 12" />
              <path d="M18 6L6 18" />
            </svg>
          </button>
        )}
      </span>

      <span className="document-routing">{routingOutcome}</span>
    </div>
  );
}
