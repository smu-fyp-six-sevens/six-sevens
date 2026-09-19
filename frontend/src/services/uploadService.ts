import { authFetch } from '../lib/api';

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
// 'id_document' (not itself a document_type — see
// models.IDENTITY_DOCUMENT_REQUIREMENT) is satisfied by uploading EITHER a
// passport or an NRIC, not both.
export const REQUIRED_DOCUMENT_TYPES: { value: string; label: string }[] = [
  { value: 'bank_statement', label: 'Bank statement' },
  { value: 'certificate_of_incorporation', label: 'Certificate of incorporation' },
  { value: 'payslip', label: 'Payslip' },
  { value: 'id_document', label: 'Identity document (passport or NRIC)' },
  { value: 'fatca_crs_declaration', label: 'FATCA/CRS declaration' },
  { value: 'certificate_of_incumbency', label: 'Certificate of incumbency' },
  { value: 'company_constitution', label: 'Company constitution' },
  { value: 'proof_of_address', label: 'Proof of address' },
  { value: 'source_of_wealth', label: 'Source of wealth' },
  { value: 'credit_report', label: 'Credit report' },
  { value: 'list_of_loans', label: 'List of loans' },
];

// Mirrors OPTIONAL_DOCUMENT_TYPES in backend/app/models.py — shown on the
// checklist (ticked once uploaded) but never counted toward it.
export const OPTIONAL_DOCUMENT_TYPES: { value: string; label: string }[] = [
  { value: 'notice_of_assessment', label: 'Notice of assessment' },
];

// Mirrors settings.EXTRACTION_CONFIDENCE_THRESHOLD in backend/app/config.py —
// below this, the UI renders extracted fields as editable so a loan officer
// can correct values the LLM wasn't confident about.
export const EXTRACTION_CONFIDENCE_THRESHOLD = 0.95;


// Mirrors the subset of CaseSummary in backend/app/models.py this page
// needs. fulfilled_requirements here is NOT just a union of each document's
// own fulfilled_requirements — proof_of_address specifically requires every
// identified individual (every id_document) to have an address on file, not
// just one anywhere in the case — see case_status.py's
// compute_identity_proof_of_address_coverage. That's why the sidebar
// checklist fetches this from the backend rather than recomputing it from
// documentDetails client-side.
export interface CaseRequirementCoverage {
  fulfilled_requirements: string[];
  recommended_additional_documents: string[];
  // How many of the case's identified individuals (one per accepted
  // id_document) currently have an address on file, out of how many there
  // are. total=0 means no ID document has been accepted yet.
  // unmatched_names: names with an address on file (proof_of_address,
  // bank_statement, ...) that don't match any identified id_document —
  // not counted in covered/total, so surfaced separately.
  proof_of_address_coverage: { covered: number; total: number; unmatched_names: string[] };
  // How many of the case's identified individuals (grouped the same way as
  // proof_of_address_coverage — by id_number, falling back to name) have a
  // complete id_document (front+back matched by id_number, or a
  // self-contained single capture) on file, out of how many identities
  // have been identified at all. total=0 means no id_document has been
  // accepted yet. missing_side ("front"/"back") is only populated when
  // there's exactly one identity in the case — see
  // mapper.compute_id_document_identity_progress /
  // mapper.compute_id_document_missing_side.
  id_document_coverage: { covered: number; total: number; missing_side: string | null };
}

export async function getCaseRequirementCoverage(caseId: string): Promise<CaseRequirementCoverage> {
  const response = await authFetch(`/cases/${caseId}`);

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Failed to fetch case (${response.status}): ${detail}`);
  }

  return (await response.json()) as CaseRequirementCoverage;
}

export async function getDocument(documentId: string): Promise<DocumentRecord> {
  const response = await authFetch(`/documents/${documentId}`);

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Failed to fetch document (${response.status}): ${detail}`);
  }

  return (await response.json()) as DocumentRecord;
}

export async function listDocuments(caseId: string): Promise<DocumentRecord[]> {
  const response = await authFetch(`/documents?case_id=${encodeURIComponent(caseId)}`);

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
  const response = await authFetch(`/documents/${documentId}/extracted-fields`, {
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

  const response = await authFetch('/documents/upload-batch', {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Upload failed (${response.status}): ${detail}`);
  }

  return (await response.json()) as BulkUploadResponse;
}
