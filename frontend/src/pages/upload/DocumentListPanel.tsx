import type { Dispatch, SetStateAction } from 'react';
import type { DocumentRecord, DocumentCaseMatch } from '../../services/uploadService';
import {
  TERMINAL_DOCUMENT_STATUSES,
  canCancelDocument,
  canRetryDocument,
  documentDetectionStage,
  formatDocumentStatus,
  formatDocumentStatusLabel,
  formatDocumentType,
  formatRoutingOutcome,
  isDocumentStatusInProgress,
  isDocumentStuckProcessing,
} from '../../services/uploadService';

export function DocumentListPanel({
  handleCancelDocument,
  handleRetryDocument,
  allExtractedSelected,
  approveError,
  approvedDocuments,
  approvingDocumentId,
  cancelActionError,
  cancellingDocumentId,
  caseMatches,
  clearDocumentSelection,
  documents,
  eligibleForApprovalCount,
  extractedDocuments,
  handleApproveSelected,
  handleSelectAllEligible,
  isApprovingSelected,
  onOpenDocument,
  openMoveDialog,
  retryActionError,
  retryingDocumentId,
  selectedApprovableCount,
  selectedDocumentIds,
  selectedExtractedDocuments,
  selectedMovableOrDeletableCount,
  setDocumentsToDelete,
  setSelectedDocumentIds,
  toggleDocumentSelected,
  wsConnected,
}: {
  handleCancelDocument: (documentId: string) => void | Promise<void>;
  handleRetryDocument: (documentId: string) => void | Promise<void>;
  allExtractedSelected: boolean;
  approveError: string | null;
  approvedDocuments: DocumentRecord[];
  approvingDocumentId: string | null;
  cancelActionError: string | null;
  cancellingDocumentId: string | null;
  caseMatches: Map<string, DocumentCaseMatch>;
  clearDocumentSelection: () => void;
  documents: DocumentRecord[];
  eligibleForApprovalCount: number;
  extractedDocuments: DocumentRecord[];
  handleApproveSelected: () => void | Promise<void>;
  handleSelectAllEligible: () => void;
  isApprovingSelected: boolean;
  onOpenDocument: (documentId: string) => void;
  openMoveDialog: () => void | Promise<void>;
  retryActionError: string | null;
  retryingDocumentId: string | null;
  selectedApprovableCount: number;
  selectedDocumentIds: Set<string>;
  selectedExtractedDocuments: DocumentRecord[];
  selectedMovableOrDeletableCount: number;
  setDocumentsToDelete: Dispatch<SetStateAction<DocumentRecord[]>>;
  setSelectedDocumentIds: Dispatch<SetStateAction<Set<string>>>;
  toggleDocumentSelected: (documentId: string) => void;
  wsConnected: boolean;
}) {
  return (
    <>
              {documents.length > 0 && (
                <>
                  {/* EXTRACTED DOCUMENTS */}
                  {extractedDocuments.length > 0 && (
                    <section className="documents-card">
                      <div className="documents-card-header">
                        <div className="documents-card-title">
                          <span className="documents-card-icon">▣</span>
                          <span>Extracted Files</span>
                        </div>

                        <div className="documents-card-actions">
                          <div className="bulk-icon-toolbar">
                            {selectedDocumentIds.size > 0 && (
                              <button
                                type="button"
                                className="bulk-icon-clear"
                                title="Clear selection"
                                onClick={clearDocumentSelection}
                              >
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
                                selectedExtractedDocuments.filter((document) =>
                                  TERMINAL_DOCUMENT_STATUSES.has(document.status),
                                ),
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
                          <span
                            className={`mock-badge${wsConnected ? ' live' : ''}`}
                            title={
                              wsConnected
                                ? 'Connected — updates push in real time'
                                : 'Disconnected — reconnecting…'
                            }
                          >
                            {wsConnected ? 'LIVE' : 'OFFLINE'}
                          </span>
                        </div>
                      </div>

                      {approveError && (
                        <div className="toast show mixed">⚠ {approveError}</div>
                      )}
                      {cancelActionError && (
                        <div className="toast show mixed">⚠ {cancelActionError}</div>
                      )}
                      {retryActionError && (
                        <div className="toast show mixed">⚠ {retryActionError}</div>
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
                                  allExtractedSelected
                                    ? new Set()
                                    : new Set(extractedDocuments.map((document) => document.id)),
                                )
                              }
                            />
                          </label>
                          <span>Filename</span>
                          <span>Document Type</span>
                          <span>Status</span>
                          <span>Routing Outcome</span>
                        </div>

                        {extractedDocuments.map((document) => {
                          const status = document.status;
                          const isTerminal = TERMINAL_DOCUMENT_STATUSES.has(status);
                          const isApproving = approvingDocumentId === document.id;
                          const isStuck = isDocumentStuckProcessing(document);
                          const isCancellingRow = cancellingDocumentId === document.id;
                          const isRetryingRow = retryingDocumentId === document.id;
                          const caseMatch = caseMatches.get(document.id);
                          const routingOutcome = formatRoutingOutcome(document, caseMatch);
                          const manualReviewTitle =
                            caseMatch?.status === 'manual_review'
                              ? caseMatch.reason
                              : 'Requires manual review';

                          const isSelected = selectedDocumentIds.has(document.id);

                          return (
                            <div
                              key={document.id}
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
                                  onChange={() => toggleDocumentSelected(document.id)}
                                />
                              </label>

                              <span className="document-filename">
                                {routingOutcome === 'Manual Review' && (
                                  <span
                                    className="document-manual-review-icon"
                                    title={manualReviewTitle}
                                    aria-label={manualReviewTitle}
                                  >
                                    ⚠
                                  </span>
                                )}

                                {document.filename}
                              </span>

                              <span className="document-type-cell">
                                {formatDocumentType(document.document_type, document.classification_confidence)}
                              </span>

                              <span className="document-status-cell">
                                <span
                                  className={`status-dot ${
                                    status === 'analyzed'
                                      ? 'status-dot--success'
                                      : status === 'failed'
                                        ? 'status-dot--failed'
                                        : status === 'rejected'
                                          ? 'status-dot--rejected'
                                          : status === 'cancelled'
                                          ? 'status-dot--cancelled'
                                          : isStuck
                                            ? 'status-dot--stuck'
                                            : ''
                                  }`}
                                />

                                <span>
                                  {formatDocumentStatus(status)}
                                  {!isTerminal && '…'}
                                </span>

                                {isStuck && (
                                  <span className="document-status-stuck-badge">Stuck</span>
                                )}

                                {canRetryDocument(document) && (
                                  <button
                                    type="button"
                                    className="document-row-action-icon document-row-action-icon--retry"
                                    aria-label={
                                      isRetryingRow
                                        ? `Retrying ${document.filename}`
                                        : `Retry ${document.filename}`
                                    }
                                    title={isRetryingRow ? 'Retrying…' : 'Retry'}
                                    disabled={isRetryingRow}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      void handleRetryDocument(document.id);
                                    }}
                                    onKeyDown={(event) => event.stopPropagation()}
                                  >
                                    <svg
                                      viewBox="0 0 24 24"
                                      aria-hidden="true"
                                      fill="none"
                                      stroke="currentColor"
                                      strokeWidth="2"
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                    >
                                      <path d="M20 11A8 8 0 1 0 18.6 16" />
                                      <path d="M20 5v6h-6" />
                                    </svg>
                                  </button>
                                )}

                                {canCancelDocument(status) && (
                                  <button
                                    type="button"
                                    className="document-row-action-icon document-row-action-icon--cancel"
                                    aria-label={
                                      isCancellingRow
                                        ? `Cancelling ${document.filename}`
                                        : `Cancel ${document.filename}`
                                    }
                                    title={isCancellingRow ? 'Cancelling…' : 'Cancel'}
                                    disabled={isCancellingRow}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      void handleCancelDocument(document.id);
                                    }}
                                    onKeyDown={(event) => event.stopPropagation()}
                                  >
                                    <svg
                                      viewBox="0 0 24 24"
                                      aria-hidden="true"
                                      fill="none"
                                      stroke="currentColor"
                                      strokeWidth="2"
                                      strokeLinecap="round"
                                    >
                                      <path d="M6 6l12 12" />
                                      <path d="M18 6L6 18" />
                                    </svg>
                                  </button>
                                )}
                              </span>

                              <span className="document-routing">
                                {routingOutcome}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </section>
                  )}

                  {/* APPROVED DOCUMENTS */}
                  {approvedDocuments.length > 0 && (
                    <section className="documents-card approved-documents-card">
                      <div className="documents-card-header">
                        <div className="documents-card-title">
                          <span className="documents-card-icon">✓</span>
                          <span>Approved Documents</span>
                        </div>
                        <span
                          className={`mock-badge${wsConnected ? ' live' : ''}`}
                          title={
                            wsConnected
                              ? 'Connected — updates push in real time'
                              : 'Disconnected — reconnecting…'
                          }
                        >
                          {wsConnected ? 'LIVE' : 'OFFLINE'}
                        </span>
                      </div>

                      <div className="documents-table documents-table--selectable">
                        <div className="documents-table-header">
                          {/* Empty spacer matching the Extracted Files table's checkbox
                              column, so both tables' columns line up — see .documents-table--selectable. */}
                          <span className="document-select-cell" />
                          <span>Filename</span>
                          <span>Document Type</span>
                          <span>Status</span>
                          <span>Routing Outcome</span>
                        </div>

                        {approvedDocuments.map((document) => {
                          const hasDetectionReport = document.has_detection_report ?? false;
                          const detectionStage = documentDetectionStage(
                            document.status,
                            hasDetectionReport,
                            document.document_type,
                          );

                          return (
                            <div
                              key={document.id}
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
                              <span className="document-select-cell" />

                              <span className="document-filename">
                                {document.filename}
                              </span>

                              <span className="document-type-cell">
                                {formatDocumentType(document.document_type, document.classification_confidence)}
                              </span>

                              <span className="document-status-cell">
                                <span
                                  className={`status-dot ${
                                    detectionStage === 'detected' || detectionStage === 'skipped'
                                      ? 'status-dot--success'
                                      : ''
                                  }`}
                                />
                                <span>
                                  {formatDocumentStatusLabel(document.status, hasDetectionReport, document.document_type)}
                                  {isDocumentStatusInProgress(
                                    document.status,
                                    hasDetectionReport,
                                    document.document_type,
                                  ) && '…'}
                                </span>
                              </span>

                              <span className="document-routing">
                                Approved
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </section>
                  )}
                </>
              )}
    </>
  );
}
