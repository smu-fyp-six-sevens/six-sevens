import type { Dispatch, ReactNode, SetStateAction } from 'react';
import type { DocumentRecord, DocumentCaseMatch, CaseMatchComparison, DocumentDetectionStage } from '../../services/uploadService';
import {
  CLASSIFICATION_CONFIDENCE_THRESHOLD,
  DOCUMENT_TYPE_OPTIONS,
  documentStatusDisplay,
  formatDocumentType,
  formatRoutingOutcome,
  isDocumentStuckProcessing,
} from '../../services/uploadService';
import {
  formatDate,
} from './formatters';
import { ErrorMessage } from '../../components/ErrorMessage';

type StatusTone = 'success' | 'failed' | 'rejected' | 'cancelled' | 'stuck';

const STATUS_CLASSES: Record<StatusTone, { wrapper: string; dot: string }> = {
  success: { wrapper: 'document-status--success', dot: 'status-dot--success' },
  failed: { wrapper: 'document-status--failed', dot: 'status-dot--failed' },
  rejected: { wrapper: 'document-status--rejected', dot: 'status-dot--rejected' },
  cancelled: { wrapper: 'document-status--cancelled', dot: 'status-dot--cancelled' },
  stuck: { wrapper: 'document-status--stuck', dot: 'status-dot--stuck' },
};

function statusTone(document: DocumentRecord, detectionStage: DocumentDetectionStage): StatusTone | null {
  if (document.status === 'analyzed' || detectionStage === 'detected' || detectionStage === 'skipped') return 'success';
  if (document.status === 'failed' || detectionStage === 'failed') return 'failed';
  if (document.status === 'rejected') return 'rejected';
  if (document.status === 'cancelled') return 'cancelled';
  if (isDocumentStuckProcessing(document)) return 'stuck';
  return null;
}

export function DocumentSummaryCard({
  canCorrectType,
  caseMatch,
  caseMatchMismatches,
  chosenType,
  detectionStage,
  document,
  failureReason,
  handleSaveType,
  hasDetectionReport,
  headerActions,
  isSavingType,
  isUnconfirmedUnrecognised,
  lowClassificationConfidence,
  setSelectedType,
  setTypeError,
  typeError,
}: {
  canCorrectType: boolean;
  // Shown beside the status, e.g. the document's actions menu.
  headerActions?: ReactNode;
  caseMatch: DocumentCaseMatch | null;
  caseMatchMismatches: CaseMatchComparison[];
  chosenType: string;
  detectionStage: DocumentDetectionStage;
  document: DocumentRecord;
  failureReason: { title: string; detail: string; fileProblem: boolean } | null;
  handleSaveType: () => void | Promise<void>;
  hasDetectionReport: boolean;
  isSavingType: boolean;
  isUnconfirmedUnrecognised: boolean;
  lowClassificationConfidence: boolean;
  setSelectedType: Dispatch<SetStateAction<string>>;
  setTypeError: Dispatch<SetStateAction<string | null>>;
  typeError: string | null;
}) {
  const tone = statusTone(document, detectionStage);
  const statusDisplay = documentStatusDisplay(
    document.status,
    hasDetectionReport,
    document.document_type,
    document.has_detection_error ?? false,
  );

  return (
        <section className="details-card">
          <div className="document-header">
            <div className="document-header-main">
              <h2>{document.filename}</h2>

              <div className="document-meta">
                <span>
                  Uploaded on {formatDate(document.uploaded_at)}
                </span>

                <span>
                  Document ID: {document.id}
                </span>
              </div>
            </div>

            <div className="document-header-side">
              <div
                className={`document-status ${tone ? STATUS_CLASSES[tone].wrapper : ''}`}
              >
                <span
                  className={`status-dot ${tone ? STATUS_CLASSES[tone].dot : ''}`}
                />
                {isDocumentStuckProcessing(document) ? `${statusDisplay.label} (Stuck)` : statusDisplay.label}
                {statusDisplay.inProgress && '…'}
              </div>
              {headerActions}
            </div>
          </div>

          <div className="details-info">
            <div>
              <span className="details-label">Document Type</span>
              {canCorrectType ? (
                <div className="details-type-editor">
                  <select
                    className="details-type-select"
                    value={chosenType}
                    disabled={isSavingType}
                    onChange={(event) => {
                      setSelectedType(event.target.value);
                      setTypeError(null);
                    }}
                  >
                    {(!document.document_type || isUnconfirmedUnrecognised) && (
                      <option value="" disabled>
                        {isUnconfirmedUnrecognised ? 'Unrecognised — select a type…' : 'Select a type…'}
                      </option>
                    )}
                    {DOCUMENT_TYPE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>

                  <button
                    type="button"
                    className="details-save-button"
                    disabled={isSavingType || !chosenType}
                    onClick={() => void handleSaveType()}
                  >
                    {isSavingType
                      ? 'Saving…'
                      : chosenType === document.document_type
                        ? 'Confirm document type'
                        : 'Confirm document type and rerun'}
                  </button>

                  {typeError && <ErrorMessage inline message={typeError} />}
                </div>
              ) : (
                <strong>
                  {formatDocumentType(document.document_type, document.classification_confidence)}
                </strong>
              )}
            </div>

            <div>
              <span className="details-label">
                Classification Confidence
              </span>
              <strong>
                {document.classification_confidence != null
                  ? document.classification_confidence.toFixed(2)
                  : '—'}
              </strong>
            </div>

            <div>
              <span className="details-label">Routing Outcome</span>
              <strong>
                {formatRoutingOutcome(document, caseMatch)}
              </strong>
            </div>
          </div>

          {caseMatch?.status === 'manual_review' && (
            <div className="review-message review-message--warning details-failure-reason">
              <div>
                <strong>Sent to manual review</strong>
                {caseMatchMismatches.length > 0 ? (
                  caseMatchMismatches.map((comparison) => (
                    <div key={`${comparison.document_field}:${comparison.document_value}`}>
                      {comparison.reason}
                      {comparison.case_value && (
                        <> ({comparison.other_filename ?? comparison.reference_filename ?? 'certificate of incumbency'}: {comparison.case_value})</>
                      )}
                    </div>
                  ))
                ) : (
                  <div>{caseMatch.reason}</div>
                )}
              </div>
            </div>
          )}

          {failureReason && (
            <div className="review-message review-message--error details-failure-reason">
              <div>
                <strong>{failureReason.title}</strong>
                <div>{failureReason.detail}</div>
                {canCorrectType && (
                  <div className="details-failure-reason-hint">
                    You can correct the document type above and re-run extraction.
                  </div>
                )}
              </div>
            </div>
          )}

          {lowClassificationConfidence && (
            <div className="review-message review-message--warning details-failure-reason">
              <div>
                <strong>Classification confidence below threshold</strong>
                <div>
                  This document was classified as{' '}
                  {formatDocumentType(document.document_type, document.classification_confidence)} with{' '}
                  {document.classification_confidence!.toFixed(2)} confidence, below the
                  required {CLASSIFICATION_CONFIDENCE_THRESHOLD.toFixed(2)}.
                </div>
                {canCorrectType && !failureReason && (
                  <div className="details-failure-reason-hint">
                    You can correct the document type above and re-run extraction.
                  </div>
                )}
              </div>
            </div>
          )}
        </section>
  );
}
