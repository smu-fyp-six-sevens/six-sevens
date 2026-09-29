import { authFetch } from '../lib/api';

// Mirrors SpellingAnomaly / SpellingAnomalyResult in
// backend/app/fraud_detection/spelling_anomaly.py
export interface SpellingAnomaly {
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

// Dev-only: runs the real GET endpoint against an existing document_id
// (its actual stored file), instead of an ad-hoc upload. Used by
// TestAnomalyPage's optional "document ID" field — not part of the
// delivered app flow.
export async function getSpellingAnomaly(documentId: string): Promise<SpellingAnomalyResult> {
  const response = await authFetch(`/documents/${documentId}/detection/spelling-anomaly`);

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Spelling anomaly check failed (${response.status}): ${detail}`);
  }

  return (await response.json()) as SpellingAnomalyResult;
}
