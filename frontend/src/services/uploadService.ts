// Mirrors BulkUploadItemResult / BulkUploadResponse in backend/app/models.py
export interface BulkUploadItemResult {
  filename: string;
  success: boolean;
  document_id?: string | null;
  status?: string | null;
  error?: string | null;
}

export interface BulkUploadResponse {
  results: BulkUploadItemResult[];
}

// Mirrors DocumentRecord in backend/app/models.py
export interface DocumentRecord {
  id: string;
  case_id: string | null;
  filename: string;
  document_type: string | null;
  confidence: number | null;
  classification_outcome: 'accepted' | 'manual_review' | null;
  storage_path: string | null;
  status: string;
  retry_count: number;
  uploaded_at: string;
  queued_at: string | null;
  processing_started_at: string | null;
  failure_reason: string | null;
  evidence: string[];
  extracted_fields: Record<string, unknown> | null;
  // Extraction's own confidence in extracted_fields — separate from
  // `confidence` above (the classifier's), never overwrites it.
  extraction_confidence: number | null;
  escalation_reason: string | null;
  metadata: Record<string, unknown> | null;
}

// Mirrors settings.EXTRACTION_CONFIDENCE_THRESHOLD in backend/app/config.py —
// below this, the UI renders extracted fields as editable so a loan officer
// can correct values the LLM wasn't confident about.
export const EXTRACTION_CONFIDENCE_THRESHOLD = 0.7;

const apiBaseUrl = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export async function getDocument(documentId: string): Promise<DocumentRecord> {
  const response = await fetch(`${apiBaseUrl}/documents/${documentId}`);

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Failed to fetch document (${response.status}): ${detail}`);
  }

  return (await response.json()) as DocumentRecord;
}

// Mirrors ClassificationPreviewResponse in backend/app/models.py — evidence
// held in-memory on the backend only, not persisted to Supabase (confidence
// lives on DocumentRecord.confidence, a real column, instead).
export interface ClassificationPreview {
  evidence: string[];
}

export async function getClassificationPreview(documentId: string): Promise<ClassificationPreview> {
  const response = await fetch(`${apiBaseUrl}/documents/${documentId}/classification-preview`);

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Failed to fetch classification preview (${response.status}): ${detail}`);
  }

  return (await response.json()) as ClassificationPreview;
}

export async function uploadDocuments(caseId: string, files: File[]): Promise<BulkUploadResponse> {
  const formData = new FormData();
  formData.append('case_id', caseId);
  for (const file of files) {
    formData.append('files', file);
  }

  const response = await fetch(`${apiBaseUrl}/documents/upload-batch`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Upload failed (${response.status}): ${detail}`);
  }

  return (await response.json()) as BulkUploadResponse;
}
