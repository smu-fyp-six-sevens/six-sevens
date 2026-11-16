import type { DocumentCaseMatch, DocumentRecord } from '../../services/uploadService';
import {
  CLASSIFICATION_CONFIDENCE_THRESHOLD,
  EXTRACTION_CONFIDENCE_THRESHOLD,
  formatDocumentLabel,
  hasExtractedInformation,
  isBlockedByContentGuard,
  isClassificationConfirmed,
} from '../../services/uploadService';
import { humanizeFieldKey } from '../../utils/fieldLabels';
import { describeFailureReason } from '../document-details/formatters';

// How many fields were read with too little confidence to trust without a look.
function unsureFieldCount(fields: Record<string, unknown> | null): number {
  return Object.values(fields ?? {}).filter((raw) => {
    const confidence = raw && typeof raw === 'object' && 'confidence' in raw ? raw.confidence : null;
    return typeof confidence === 'number' && confidence < EXTRACTION_CONFIDENCE_THRESHOLD;
  }).length;
}

// Why a flagged document waits on the officer, in plain words on one line: the problem, then what to do. Shown in
// the document list's Remarks column; the document's own page has the detail. Classification problems come first,
// then what is wrong with the extracted fields.
export function reviewReason(
  document: DocumentRecord,
  caseMatch: DocumentCaseMatch | undefined,
  caseMatchNeedsReview: (document: DocumentRecord) => boolean,
): string {
  const unsureFields = unsureFieldCount(document.extracted_fields);
  const missingRequiredFields = document.missing_required_fields ?? [];
  const classificationNeedsReview = document.routing_status === 'manual_review' || !isClassificationConfirmed(document);

  if (isBlockedByContentGuard(document)) {
    return 'Contains hidden instructions to the AI. Reject it or ask for a clean copy.';
  }
  if (document.status === 'failed') {
    return `Couldn't be processed (${describeFailureReason(document.failure_reason ?? 'no reason recorded').title}). Retry or reject it.`;
  }
  if (!classificationNeedsReview && caseMatch && caseMatchNeedsReview(document)) {
    return 'This may belong to a different case. Check it before approving.';
  }
  if (classificationNeedsReview) {
    if (document.document_type === 'unrecognised') {
      return "We couldn't tell what document this is. Please choose its type.";
    }
    if (document.classification_confidence != null && document.classification_confidence < CLASSIFICATION_CONFIDENCE_THRESHOLD) {
      return `We're not sure this is a ${formatDocumentLabel(document)}. Please confirm its type.`;
    }
    return 'Please confirm the document type before approving.';
  }
  if (!hasExtractedInformation(document.extracted_fields)) {
    return "We couldn't read any details. Add them yourself or reject it.";
  }
  if (unsureFields > 0) {
    return unsureFields === 1
      ? 'One detail may have been misread. Please check it before approving.'
      : `${unsureFields} details may have been misread. Please check them before approving.`;
  }
  if (missingRequiredFields.length === 1) {
    return `${humanizeFieldKey(missingRequiredFields[0])} is missing. Please add it before approving.`;
  }
  if (missingRequiredFields.length > 1) {
    return `${missingRequiredFields.length} required details are missing. Please add them before approving.`;
  }
  return 'Needs a quick check by you. Open it to review.';
}
