import type {
  DocumentCaseMatch,
  DocumentDetectionStage,
  DocumentRecord,
} from './types';

// Mirrors settings.EXTRACTION_CONFIDENCE_THRESHOLD in backend/app/config.py.
export const EXTRACTION_CONFIDENCE_THRESHOLD = 0.9;

// The single source of truth for "does this document's extraction quality need a human to look at it".
export function hasExtractedInformation(fields: Record<string, unknown> | null): boolean {
  if (!fields) {
    return false;
  }
  return Object.values(fields).some((raw) => {
    const value = raw && typeof raw === 'object' && 'value' in raw ? (raw as { value?: unknown }).value : raw;
    if (value == null) return false;
    if (typeof value === 'string') return value.trim() !== '';
    if (Array.isArray(value)) return value.length > 0;
    if (typeof value === 'object') return Object.keys(value).length > 0;
    return true;
  });
}

export function lowestFieldConfidence(fields: Record<string, unknown> | null): number | null {
  if (!fields) {
    return null;
  }

  let lowest: number | null = null;
  for (const raw of Object.values(fields)) {
    if (!raw || typeof raw !== 'object' || !('confidence' in raw)) {
      continue;
    }
    const confidence = (raw as { confidence?: unknown }).confidence;
    if (typeof confidence === 'number' && (lowest === null || confidence < lowest)) {
      lowest = confidence;
    }
  }
  return lowest;
}

// Overall routing outcome shown in the UI (view only).
export function formatRoutingOutcome(
  document: Pick<
    DocumentRecord,
    | 'status'
    | 'routing_status'
    | 'extracted_fields'
    | 'missing_required_fields'
    | 'document_type'
    | 'classification_confidence'
  >,
  caseMatch?: Pick<DocumentCaseMatch, 'status'> | null,
): string {
  if (document.status === 'rejected') {
    return 'Rejected';
  }

  // Manual Review -> Accepted (officer saved the fields) -> Approved: only the Approve Document action.
  if (document.status === 'approved') {
    return 'Approved';
  }

  const lowestConfidence = lowestFieldConfidence(document.extracted_fields);

  if (
    document.status === 'failed' ||
    document.routing_status === 'manual_review' ||
    // Saving fields flips routing_status to accepted without confirming the type, so an unconfirmed.
    (document.status === 'analyzed' && !isClassificationConfirmed(document)) ||
    (document.status === 'analyzed' && !hasExtractedInformation(document.extracted_fields)) ||
    caseMatch?.status === 'manual_review' ||
    (lowestConfidence != null &&
      lowestConfidence < EXTRACTION_CONFIDENCE_THRESHOLD) ||
    (document.missing_required_fields?.length ?? 0) > 0
  ) {
    return 'Manual Review';
  }

  if (document.routing_status === 'accepted') {
    return document.status === 'analyzed' ? 'Accepted' : 'Pending';
  }

  return '—';
}

// Statuses that no longer change on their own.
export const TERMINAL_DOCUMENT_STATUSES = new Set([
  'analyzed',
  'approved',
  'rejected',
  'failed',
  'cancelled',
]);

// Mirrors settings.STUCK_PROCESSING_RETRY_THRESHOLD_SECONDS in backend/app/config.py.
export const STUCK_PROCESSING_RETRY_THRESHOLD_MS = 10 * 60 * 1000;

// True once a PROCESSING document has been sitting there long enough that it's more likely orphaned.
export function isDocumentStuckProcessing(
  document: Pick<DocumentRecord, 'status' | 'processing_started_at'>,
): boolean {
  if (document.status !== 'processing' || !document.processing_started_at) {
    return false;
  }
  const startedAt = new Date(document.processing_started_at).getTime();
  if (Number.isNaN(startedAt)) {
    return false;
  }
  return Date.now() - startedAt > STUCK_PROCESSING_RETRY_THRESHOLD_MS;
}

// Mirrors routers/documents.py's cancel_document eligibility.
export function canCancelDocument(status: string): boolean {
  return status === 'queued' || status === 'processing' || status === 'extracting';
}

// Mirrors routers/documents.py's retry_document eligibility: FAILED, or PROCESSING long enough to be stuck.
export function canRetryDocument(
  document: Pick<DocumentRecord, 'status' | 'processing_started_at'>,
): boolean {
  return document.status === 'failed' || isDocumentStuckProcessing(document);
}

// Single source of truth for how a raw backend DocumentStatus reads in the UI.
// 'processing' is the classification stage.
export function formatDocumentStatus(status: string): string {
  switch (status) {
    case 'uploaded':
      return 'Uploaded';
    case 'queued':
      return 'Queued';
    case 'processing':
      return 'Classifying';
    case 'extracting':
      return 'Extracting';
    case 'analyzed':
      return 'Extracted';
    case 'approved':
      return 'Approved';
    case 'rejected':
      return 'Rejected';
    case 'failed':
      return 'Failed';
    case 'cancelled':
      return 'Cancelled';
    default:
      return status;
  }
}

export function skipsDetection(documentType: string | null | undefined): boolean {
  return !documentType || documentType === 'unrecognised';
}

export function documentDetectionStage(
  status: string,
  hasDetectionReport: boolean,
  documentType?: string | null,
): DocumentDetectionStage {
  if (status !== 'approved') {
    return null;
  }
  if (hasDetectionReport) {
    return 'detected';
  }
  return skipsDetection(documentType) ? 'skipped' : 'scanning';
}

export function formatDocumentStatusLabel(
  status: string,
  hasDetectionReport: boolean,
  documentType?: string | null,
): string {
  const stage = documentDetectionStage(status, hasDetectionReport, documentType);
  if (stage === 'scanning') return 'Scanning';
  if (stage === 'detected') return 'Detected';
  return formatDocumentStatus(status);
}

// Same "…" suffix / non-terminal treatment formatDocumentStatus's other callers already use, extended.
export function isDocumentStatusInProgress(
  status: string,
  hasDetectionReport: boolean,
  documentType?: string | null,
): boolean {
  if (documentDetectionStage(status, hasDetectionReport, documentType) === 'scanning') {
    return true;
  }
  return !TERMINAL_DOCUMENT_STATUSES.has(status);
}

// Mirrors settings.CLASSIFICATION_CONFIDENCE_THRESHOLD in backend/app/config.py.
export const CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.85;

// Mirrors case_service.is_classification_confirmed in the backend, which the approve endpoint enforces.
export function isClassificationConfirmed(
  document: Pick<DocumentRecord, 'document_type' | 'classification_confidence'>,
): boolean {
  const confidence = document.classification_confidence;
  if (confidence == null) {
    return false;
  }
  if (!document.document_type || document.document_type === 'unrecognised') {
    return confidence >= 1;
  }
  return confidence >= CLASSIFICATION_CONFIDENCE_THRESHOLD;
}
