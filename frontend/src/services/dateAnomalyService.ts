import { authFetch } from '../lib/api';

// Mirrors DateFinding / ExtractedDate / DateAnomalyResult in
// backend/app/fraud_detection/date_anomalies.py
export interface DateFinding {
  check_code: string;
  severity: 'impossible' | 'inconsistent' | 'implausible' | 'signal';
  field: string | null;
  // The date as the document states it, and the date the check expected
  // instead. Both present for a reconciliation mismatch.
  stated: string | null;
  expected: string | null;
  // True when the failing value's field has low extraction confidence —
  // the failure might be an extraction artefact rather than a genuine
  // anomaly.
  needs_review: boolean;
  severity_note: string | null;
  summary: string;
}

export interface SkippedCheck {
  check_code: string;
  reason: string;
}

export interface ExtractedDate {
  field: string;
  label: string;
  value: string; // ISO date
  raw_value: string;
  confidence: number | null;
}

export interface DateAnomalyResult {
  document_id: string;
  document_type: string | null;
  dates_extracted: ExtractedDate[];
  findings: DateFinding[];
  skipped_checks: SkippedCheck[];
  unable_to_assess: boolean;
  unable_to_assess_reason: string | null;
}

// No file: the check runs entirely from extracted_fields, which a dev
// call has none of — this just confirms which checks are wired up for a
// document type. documentType is optional (see backend
// DATE_DOCUMENT_TYPE_CHECKS); omitted, only the universal GEN.*/ID.*
// checks run.
export async function testDateAnomalies(
  documentType?: string,
): Promise<DateAnomalyResult> {
  const formData = new FormData();
  if (documentType) {
    formData.append('document_type', documentType);
  }

  const response = await authFetch('/extraction/test-date-anomalies', {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Date anomaly check failed (${response.status}): ${detail}`);
  }

  return (await response.json()) as DateAnomalyResult;
}

// Dev-only: runs the real GET endpoint against an existing document_id
// (its actual stored extracted_fields), instead of an ad-hoc upload with
// no extracted fields behind it. Used by TestAnomalyPage's optional
// "document ID" field — not part of the delivered app flow.
export async function getDateAnomalies(documentId: string): Promise<DateAnomalyResult> {
  const response = await authFetch(`/documents/${documentId}/detection/date-anomalies`);

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Date anomaly check failed (${response.status}): ${detail}`);
  }

  return (await response.json()) as DateAnomalyResult;
}
