import type {
  CaseAnalysisOverview,
  CaseRisk,
  DocumentRecord,
  DocumentCaseMatch,
  SupportingDocumentRecommendation,
} from '../../services/uploadService';
import {
  CLASSIFICATION_CONFIDENCE_THRESHOLD,
  EXTRACTION_CONFIDENCE_THRESHOLD,
  formatDocumentType,
  hasExtractedInformation,
  isClassificationConfirmed,
  lowestFieldConfidence,
} from '../../services/uploadService';
import { humanizeFieldKey } from '../../utils/fieldLabels';
import { CaseAnalysisCard } from './CaseAnalysisCard';
import { CaseRiskCard } from './CaseRiskCard';
import { SupportingDocumentsCard } from './SupportingDocumentsCard';

export function UploadSummaryCards({
  analysisOverview,
  analysisReady,
  caseId,
  caseMatchNeedsReview,
  caseMatches,
  flaggedDocuments,
  onOpenDocument,
  risk,
  supportingDocumentRecommendations,
}: {
  analysisOverview: CaseAnalysisOverview | null;
  analysisReady: boolean;
  caseId: string;
  caseMatchNeedsReview: (document: DocumentRecord) => boolean;
  caseMatches: Map<string, DocumentCaseMatch>;
  flaggedDocuments: DocumentRecord[];
  onOpenDocument: (documentId: string) => void;
  risk: CaseRisk | null;
  supportingDocumentRecommendations: SupportingDocumentRecommendation[];
}) {
  return (
    <>
            <section className="summary-card">
              <div className="summary-card-header">
                <div className="summary-card-title">
                  <span className="summary-icon summary-icon--warning">
                    △
                  </span>
                  <h2>Flagged Items for Manual Review</h2>
                </div>

                {flaggedDocuments.length > 0 && (
                  <span className="summary-count">
                    {flaggedDocuments.length}{' '}
                    {flaggedDocuments.length === 1 ? 'item' : 'items'}
                  </span>
                )}
              </div>

              {flaggedDocuments.length === 0 ? (
                <div className="summary-message summary-message--success">
                  <span className="summary-message-icon">✓</span>

                  <div>
                    <strong>No flagged items at the moment.</strong>

                    <p>
                      If any documents or extracted fields require manual
                      review, they will be displayed here.
                    </p>
                  </div>
                </div>
              ) : (
                <div className="flagged-list">
                  {flaggedDocuments.map((document) => {
                    const lowestConfidence = lowestFieldConfidence(document.extracted_fields);
                    const lowExtractionConfidence =
                      lowestConfidence != null && lowestConfidence < EXTRACTION_CONFIDENCE_THRESHOLD;
                    const missingRequiredFields = document.missing_required_fields ?? [];
                    const caseMatch = caseMatches.get(document.id);

                    // Classification problems first.
                    const classificationNeedsReview =
                      document.routing_status === 'manual_review' ||
                      !isClassificationConfirmed(document);
                    const reason =
                      !classificationNeedsReview &&
                      caseMatch &&
                      caseMatchNeedsReview(document)
                        ? `${caseMatch.reason}. Please verify the document belongs to this case before approving.`
                        : classificationNeedsReview
                        ? document.document_type === 'unrecognised'
                          ? 'Document type could not be confidently recognised. Please verify the document type before approving.'
                          : document.classification_confidence != null &&
                              document.classification_confidence < CLASSIFICATION_CONFIDENCE_THRESHOLD
                            ? `Classification confidence (${document.classification_confidence.toFixed(
                                2,
                              )}) is below the required threshold of ${CLASSIFICATION_CONFIDENCE_THRESHOLD.toFixed(
                                2,
                              )}. Please verify the document type before approving.`
                            : document.escalation_reason ??
                              'This document was routed to manual review during classification. Please verify the document before approving.'
                        : !hasExtractedInformation(document.extracted_fields)
                          ? 'No information was extracted from this document. Add the fields manually or reject it.'
                        : lowExtractionConfidence
                          ? 'One or more extracted fields have low confidence and require review.'
                          : missingRequiredFields.length > 0
                            ? `Missing required field${missingRequiredFields.length > 1 ? 's' : ''}: ${missingRequiredFields
                                .map((fieldName) => humanizeFieldKey(fieldName))
                                .join(', ')}.`
                            : 'This document requires manual review.';
                    return (
                      <div
                        key={document.id}
                        className="flagged-item"
                      >
                        <div className="flagged-item-main">
                          <strong>{document.filename}</strong>

                          <div className="flagged-item-meta">
                            <span>
                              {formatDocumentType(document.document_type, document.classification_confidence)}
                            </span>

                            {lowestConfidence != null && (
                              <span>
                                Lowest field confidence:{' '}
                                {lowestConfidence.toFixed(3)}
                              </span>
                            )}

                            {caseMatch?.case_mismatch && caseMatchNeedsReview(document) && (
                              <span className="flagged-status">
                                Case mismatch
                              </span>
                            )}
                          </div>

                          <p>{reason}</p>
                        </div>

                        <button
                          type="button"
                          className="flagged-view-button"
                          onClick={() =>
                            onOpenDocument(document.id)
                          }
                        >
                          View
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </section>

            <CaseRiskCard risk={risk} />

            <CaseAnalysisCard caseId={caseId} analysisReady={analysisReady} overview={analysisOverview} />

            <SupportingDocumentsCard recommendations={supportingDocumentRecommendations} />

            <section className="summary-card">
              <div className="summary-card-header">
                <div className="summary-card-title">
                  <span className="summary-icon summary-icon--audit">
                    ◴
                  </span>
                  <h2>Case Audit Trail</h2>
                </div>
              </div>

              <div className="summary-message summary-message--info">
                <span className="summary-message-icon">ⓘ</span>

                <div>
                  <strong>No audit events available yet.</strong>

                  <p>
                    Case activity history will appear here once actions
                    are recorded.
                  </p>
                </div>
              </div>
            </section>
    </>
  );
}
