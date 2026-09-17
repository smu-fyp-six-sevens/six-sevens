import { authFetch } from '../lib/api';

// Mirrors SpellingAnomaly / SpellingAnomalyResult in
// backend/app/fraud_detection/spelling_anomaly.py
export interface SpellingAnomaly {
  page: number;
  section: number;
  word: string;
  surrounding_text: string;
  suggested_correction: string | null;
}

export interface SpellingAnomalyResult {
  document_id: string;
  anomalies: SpellingAnomaly[];
  unable_to_assess: boolean;
  unable_to_assess_reason: string | null;
}

export async function testSpellingAnomaly(file: File): Promise<SpellingAnomalyResult> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await authFetch('/extraction/test-spelling-anomaly', {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Spelling anomaly check failed (${response.status}): ${detail}`);
  }

  return (await response.json()) as SpellingAnomalyResult;
}
