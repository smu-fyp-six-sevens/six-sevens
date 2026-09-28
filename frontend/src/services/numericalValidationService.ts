import { authFetch } from '../lib/api';

// Mirrors NumericalFinding / BenfordDigit / BenfordAnalysis /
// NumericalValidationResult in backend/app/fraud_detection/numerical_validation.py
export interface NumericalFinding {
  finding_type: 'format' | 'arithmetic' | 'benford';
  field: string | null;
  // AC4: the value as the document states it, and the value the check
  // calculated for it. Both present for a reconciliation mismatch.
  stated: string | null;
  expected: string | null;
  // AC6: populated where the finding traces to a place in the text; an
  // arithmetic finding from extracted fields legitimately has neither.
  page: number | null;
  location: string | null;
  severity: 'high' | 'medium' | 'low';
  severity_note: string | null;
  summary: string;
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
  values_analysed: number;
  findings: NumericalFinding[];
  benford: BenfordAnalysis;
  unable_to_assess: boolean;
  unable_to_assess_reason: string | null;
}

export async function testNumericalValidation(
  file: File,
): Promise<NumericalValidationResult> {
  const formData = new FormData();
  formData.append('file', file);

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
