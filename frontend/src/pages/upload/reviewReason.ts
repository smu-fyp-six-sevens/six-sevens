import type { DocumentCaseMatch, DocumentRecord } from '../../services/uploadService';
import {
  CLASSIFICATION_CONFIDENCE_THRESHOLD,
  EXTRACTION_CONFIDENCE_THRESHOLD,
  hasExtractedInformation,
  isBlockedByContentGuard,
  isClassificationConfirmed,
  lowestFieldConfidence,
} from '../../services/uploadService';
import { humanizeFieldKey } from '../../utils/fieldLabels';
import { describeFailureReason } from '../document-details/formatters';

// Why a flagged document waits on the officer, in one or two sentences: shown under its name in the document list.
// Classification problems come first, then what is wrong with the extracted fields.
export function reviewReason(
  document: DocumentRecord,
  caseMatch: DocumentCaseMatch | undefined,
  caseMatchNeedsReview: (document: DocumentRecord) => boolean,
): string {
  const lowestConfidence = lowestFieldConfidence(document.extracted_fields);
  const lowExtractionConfidence = lowestConfidence != null && lowestConfidence < EXTRACTION_CONFIDENCE_THRESHOLD;
  const missingRequiredFields = document.missing_required_fields ?? [];
  const classificationNeedsReview = document.routing_status === 'manual_review' || !isClassificationConfirmed(document);

  if (isBlockedByContentGuard(document)) {
    return 'Blocked by the content guard: text in this document is worded as an instruction to the AI. Open it to see what was found, then reject it or ask for a clean copy.';
  }
  if (document.status === 'failed') {
    return `Processing failed: ${describeFailureReason(document.failure_reason ?? 'no reason was recorded').title}. Retry or reject it.`;
  }
  if (!classificationNeedsReview && caseMatch && caseMatchNeedsReview(document)) {
    return `${caseMatch.reason}. Verify the document belongs to this case before approving.`;
  }
  if (classificationNeedsReview) {
    if (document.document_type === 'unrecognised') {
      return 'The document type could not be recognised. Verify it before approving.';
    }
    if (document.classification_confidence != null && document.classification_confidence < CLASSIFICATION_CONFIDENCE_THRESHOLD) {
      return `Classified with ${document.classification_confidence.toFixed(2)} confidence, under the required ${CLASSIFICATION_CONFIDENCE_THRESHOLD.toFixed(2)}. Verify the document type before approving.`;
    }
    return (
      document.escalation_reason ??
      'This document was routed to manual review during classification. Verify it before approving.'
    );
  }
  if (!hasExtractedInformation(document.extracted_fields)) {
    return 'Nothing was extracted from this document. Add the fields manually or reject it.';
  }
  if (lowExtractionConfidence) {
    return `Lowest field confidence is ${lowestConfidence.toFixed(2)}, under the required ${EXTRACTION_CONFIDENCE_THRESHOLD.toFixed(2)}. Check the fields before approving.`;
  }
  if (missingRequiredFields.length > 0) {
    return `Missing required field${missingRequiredFields.length > 1 ? 's' : ''}: ${missingRequiredFields
      .map((fieldName) => humanizeFieldKey(fieldName))
      .join(', ')}.`;
  }
  return 'This document requires manual review.';
}
