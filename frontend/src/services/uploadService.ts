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
