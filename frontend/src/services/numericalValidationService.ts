import { authFetch } from '../lib/api';

// Mirrors NumericalFinding / SkippedCheck / BenfordDigit / BenfordAnalysis /
// NumericalValidationResult in backend/app/fraud_detection/numerical_validation.py
export interface NumericalFinding {
  check_code: string;
  // impossible = the document cannot be genuine, or wasn't extracted
  // correctly, as it stands. inconsistent = two values contradict each
  // other. implausible = possible but very unlikely. signal = weak,
  // non-conclusive evidence worth a look.
  severity: 'impossible' | 'inconsistent' | 'implausible' | 'signal';
  field: string | null;
  // The value as the document states it, and the value the check
  // calculated for it. Both present for a reconciliation mismatch.
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

export interface BenfordDigit {
  digit: number;
  count: number;
  observed_percentage: number;
  expected_percentage: number;
}

export interface BenfordAnalysis {
  sample_size: number;
  digits: BenfordDigit[];
  mean_absolute_deviation: number | null;
  chi_square: number | null;
  chi_square_critical: number | null;
  conforms: boolean | null;
  unable_to_assess: boolean;
  unable_to_assess_reason: string | null;
}

export interface NumericalValidationResult {
  document_id: string;
  document_type: string | null;
  values_analysed: number;
  findings: NumericalFinding[];
  skipped_checks: SkippedCheck[];
  benford: BenfordAnalysis;
  // Only populated for bank_statement — the second Benford pass with
  // likely bank fees excluded.
  benford_excluding_fees: BenfordAnalysis | null;
  unable_to_assess: boolean;
  unable_to_assess_reason: string | null;
}

// No file: the check runs entirely from extracted_fields, which a dev
// call has none of — this just confirms which checks are wired up for a
// document type. documentType is optional (see backend DOCUMENT_TYPE_CHECKS);
// omitted, only the universal GEN.*/ID.* checks run.
export async function testNumericalValidation(
  documentType?: string,
): Promise<NumericalValidationResult> {
  const formData = new FormData();
  if (documentType) {
    formData.append('document_type', documentType);
  }

  const response = await authFetch('/extraction/test-numerical-validation', {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Numerical validation failed (${response.status}): ${detail}`);
  }

  return (await response.json()) as NumericalValidationResult;
}

// Dev-only: runs the real GET endpoint against an existing document_id
// (its actual stored extracted_fields), instead of an ad-hoc upload with
// no extracted fields behind it. Used by TestAnomalyPage's optional
// "document ID" field — not part of the delivered app flow.
export async function getNumericalValidation(documentId: string): Promise<NumericalValidationResult> {
  const response = await authFetch(`/documents/${documentId}/detection/numerical-validation`);

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Numerical validation failed (${response.status}): ${detail}`);
  }

  return (await response.json()) as NumericalValidationResult;
}
