import { authFetch } from '../lib/api';

// Mirrors MetadataAnomaly / MetadataAnalysisResult in
// backend/app/fraud_detection/metadata_analysis.py
export interface MetadataAnomaly {
  field: string;
  value: string | null;
  reason: string;
  // How much weight the finding carries on its own — "high" has no
  // innocent explanation in a KYC workflow, "low" is weak corroboration.
  severity: 'high' | 'medium' | 'low';
}

export interface MetadataAnalysisResult {
  document_id: string;
  extracted_metadata: Record<string, string | null>;
  anomalies: MetadataAnomaly[];
  ai_generation_indicators: MetadataAnomaly[];
  unable_to_assess: boolean;
  unable_to_assess_reason: string | null;
}

// documentType is optional: some rules only apply to documents an
// institution issued (a bank statement shouldn't have been through a PDF
// editor; a declaration form the applicant fills in legitimately has).
// The real per-document endpoint takes this from the classified document —
// here it's supplied by hand so the dev tool can exercise those rules.
export async function testMetadataAnomaly(
  file: File,
  documentType?: string,
): Promise<MetadataAnalysisResult> {
  const formData = new FormData();
  formData.append('file', file);
  if (documentType) {
    formData.append('document_type', documentType);
  }

  const response = await authFetch('/extraction/test-metadata-anomaly', {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Metadata anomaly check failed (${response.status}): ${detail}`);
  }

  return (await response.json()) as MetadataAnalysisResult;
}

// Dev-only: runs the real GET endpoint against an existing document_id
// (its actual stored file + document_type), instead of an ad-hoc upload.
// Used by TestAnomalyPage's optional "document ID" field — not part of
// the delivered app flow.
export async function getMetadataAnomaly(documentId: string): Promise<MetadataAnalysisResult> {
  const response = await authFetch(`/documents/${documentId}/detection/metadata-anomaly`);

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Metadata anomaly check failed (${response.status}): ${detail}`);
  }

  return (await response.json()) as MetadataAnalysisResult;
}
