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
}

const apiBaseUrl = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export async function getDocument(documentId: string): Promise<DocumentRecord> {
  const response = await fetch(`${apiBaseUrl}/documents/${documentId}`);

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Failed to fetch document (${response.status}): ${detail}`);
  }

  return (await response.json()) as DocumentRecord;
}

export async function listDocuments(caseId: string): Promise<DocumentRecord[]> {
  const response = await fetch(`${apiBaseUrl}/documents?case_id=${encodeURIComponent(caseId)}`);

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Failed to fetch documents (${response.status}): ${detail}`);
  }

  return (await response.json()) as DocumentRecord[];
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
