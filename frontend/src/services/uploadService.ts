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
  fulfilled_requirements: string[];
  extracted_fields: Record<string, unknown> | null;
  // Extraction's own confidence in extracted_fields — separate from
  // `confidence` above (the classifier's), never overwrites it.
  extraction_confidence: number | null;
  escalation_reason: string | null;
  metadata: Record<string, unknown> | null;
}

// Mirrors REQUIRED_DOCUMENT_TYPES in backend/app/models.py — the fixed
// checklist every case must satisfy. A single document can fulfil more
// than one of these (see DocumentRecord.fulfilled_requirements).
export const REQUIRED_DOCUMENT_TYPES: { value: string; label: string }[] = [
  { value: 'bank_statement', label: 'Bank statement' },
  { value: 'certificate_of_incorporation', label: 'Certificate of incorporation' },
  { value: 'payslip', label: 'Payslip' },
  { value: 'id_document', label: 'ID document' },
  { value: 'fatca_crs_declaration', label: 'FATCA/CRS declaration' },
  { value: 'certificate_of_incumbency', label: 'Certificate of incumbency' },
  { value: 'company_constitution', label: 'Company constitution' },
  { value: 'proof_of_address', label: 'Proof of address' },
  { value: 'source_of_wealth', label: 'Source of wealth' },
  { value: 'credit_report', label: 'Credit report' },
  { value: 'list_of_loans', label: 'List of loans' },
];

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

export async function listDocuments(caseId: string): Promise<DocumentRecord[]> {
  const response = await fetch(`${apiBaseUrl}/documents?case_id=${encodeURIComponent(caseId)}`);

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Failed to fetch documents (${response.status}): ${detail}`);
  }

  return (await response.json()) as DocumentRecord[];
}

// Mirrors UpdateExtractedFieldsRequest in backend/app/models.py — always sends
// the full extracted_fields dict (not a per-field patch), matching the PATCH
// /documents/{document_id}/extracted-fields endpoint in routers/documents.py.
export async function updateExtractedFields(
  documentId: string,
  extractedFields: Record<string, unknown>,
): Promise<DocumentRecord> {
  const response = await fetch(`${apiBaseUrl}/documents/${documentId}/extracted-fields`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ extracted_fields: extractedFields }),
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Failed to update extracted fields (${response.status}): ${detail}`);
  }

  return (await response.json()) as DocumentRecord;
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
