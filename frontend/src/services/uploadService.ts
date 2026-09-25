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
  { value: 'id_document', label: 'Identity document (NRIC/Passport)' },
  { value: 'bank_statement', label: 'Corporate bank statement' },
  { value: 'proof_of_address', label: 'Personal proof of address' },
  { value: 'fatca_crs_declaration', label: 'FATCA/CRS' },
  { value: 'certificate_of_incorporation', label: 'Certificate of incorporation' },
  { value: 'certificate_of_incumbency', label: 'Certificate of incumbency' },
  { value: 'company_constitution', label: 'Company constitution' },
  { value: 'credit_report', label: 'Credit report' },
  { value: 'payslip', label: 'Payslip(s)' },
  { value: 'source_of_wealth', label: 'Source of (corporate) wealth' },
  { value: 'notice_of_assessment', label: 'NOA' },
];

// Mirrors ADDITIONAL_DOCUMENT_TYPES in backend/app/models.py — documents that
// may be required depending on the case. Shown on the checklist (ticked once
// uploaded) but never counted toward it. 'company_proof_of_address' (not
// itself a document_type — see models.COMPANY_PROOF_OF_ADDRESS_REQUIREMENT)
// is ticked by a proof-of-address document showing the company's address.
// 'source_of_personal_wealth' is likewise a requirement, not a document_type:
// a source_of_wealth document in a person's name with no UEN on it.
export const ADDITIONAL_DOCUMENT_TYPES: { value: string; label: string }[] = [
  { value: 'company_proof_of_address', label: 'Company proof of address' },
  { value: 'list_of_loans', label: 'List of loans' },
  { value: 'source_of_personal_wealth', label: 'Source of (personal) wealth' },
];

// Mirrors settings.EXTRACTION_CONFIDENCE_THRESHOLD in backend/app/config.py —
// below this, the UI renders extracted fields as editable so a loan officer
// can correct values the LLM wasn't confident about.
export const EXTRACTION_CONFIDENCE_THRESHOLD = 0.95;

// The single source of truth for "does this document's extraction quality
// need a human to look at it" — the lowest confidence across its own
// extracted fields, unwrapping each field's {value, confidence} shape (see
// DocumentDetailsPage's displayableFieldEntries for the same unwrap done
// there). Deliberately NOT the document-level extraction_confidence the LLM
// used to report for the run as a whole: a single document-wide number
// could still read as confident even when one specific field (e.g. a
// garbled account number) was actually a low-confidence guess — the
// per-field minimum is the more honest signal for a loan officer deciding
// whether to trust what's on screen. Returns null when there are no fields
// with a numeric confidence to compare at all.
export function lowestFieldConfidence(fields: Record<string, unknown> | null): number | null {
  if (!fields) {
    return null;
  }

  let lowest: number | null = null;
  for (const raw of Object.values(fields)) {
    if (!raw || typeof raw !== 'object' || !('confidence' in raw)) {
      continue;
    }
    const confidence = (raw as { confidence?: unknown }).confidence;
    if (typeof confidence === 'number' && (lowest === null || confidence < lowest)) {
      lowest = confidence;
    }
  }
  return lowest;
}

// Mirrors DocumentCaseMatch / CaseMatchResult in backend/app/case_match.py —
// whether a document belongs to the case's company and individuals, checked
// against the certificate of incumbency. Computed live by the backend, never
// stored.
export interface CaseMatchComparison {
  check: 'company_uen' | 'company_name' | 'individual_id' | 'individual_name';
  outcome: 'match' | 'mismatch';
  document_field: string;
  document_value: string;
  case_value: string | null;
  reason: string;
}

export interface DocumentCaseMatch {
  document_id: string;
  filename: string;
  document_type: string | null;
  status: 'match' | 'manual_review' | 'pending';
  can_proceed_to_fraud_detection: boolean;
  // The company or an individual on the document doesn't match the case.
  case_mismatch: boolean;
  reason: string;
  comparisons: CaseMatchComparison[];
}

export interface CaseMatchResult {
  case_id: string;
  incumbency: {
    status: 'missing' | 'insufficient_evidence' | 'verified' | 'manual_review';
    document_id: string | null;
    uen: string | null;
    company_name: string | null;
    reasons: string[];
  };
  documents: DocumentCaseMatch[];
  mismatched_document_ids: string[];
}

export async function getDocumentCaseMatch(documentId: string): Promise<DocumentCaseMatch> {
  const response = await authFetch(`/documents/${documentId}/detection/case-match`);

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Failed to fetch case match (${response.status}): ${detail}`);
  }

  return (await response.json()) as DocumentCaseMatch;
}

// Overall routing outcome shown in the UI (view only). classification_outcome
// alone only records the classifier's decision — documents are always sent on
// to extraction, so an 'accepted' classification can still end up needing
// manual review. This folds in the later stages: a failed document or one
// with a low-confidence field reads as Manual Review (same rule as the
// flagged-documents list), and an accepted one reads as Pending until
// extraction has finished. caseMatch folds in the case-match check the same
// way: a document it sends to manual review (it belongs to another
// company/individual, or the certificate of incumbency itself is in doubt)
// reads as Manual Review — without touching the stored
// classification_outcome, which the checklist still relies on.
export function formatRoutingOutcome(
  document: Pick<
    DocumentRecord,
    'status' | 'classification_outcome' | 'extracted_fields'
  >,
): string {
  if (document.status === 'rejected') {
    return 'Rejected';
  }

  const lowestConfidence = lowestFieldConfidence(document.extracted_fields);

  if (
    document.status === 'failed' ||
    document.classification_outcome === 'manual_review' ||
    (lowestConfidence != null &&
      lowestConfidence < EXTRACTION_CONFIDENCE_THRESHOLD)
  ) {
    return 'Manual Review';
  }

  if (document.classification_outcome === 'accepted') {
    return document.status === 'analyzed' ? 'Accepted' : 'Pending';
  }

  return '—';
}

// Statuses that no longer change on their own — a document sitting in any
// other status is still actively moving through the pipeline (see the "…"
// suffix formatDocumentStatus's callers append for non-terminal statuses).
export const TERMINAL_DOCUMENT_STATUSES = new Set([
  'analyzed',
  'approved',
  'rejected',
  'failed',
]);

// Single source of truth for how a raw backend DocumentStatus reads in the
// UI — shared by the case's document table and the document details page,
// which previously drifted (the details page just showed the raw
// lowercase status string with no label at all).
//
// 'processing' is the classification stage; the backend moves a document to
// 'extracting' once classification is recorded (and back to 'processing'
// during the re-classification an extraction can trigger).
export function formatDocumentStatus(status: string): string {
  switch (status) {
    case 'uploaded':
      return 'Uploaded';
    case 'queued':
      return 'Queued';
    case 'processing':
      return 'Classifying';
    case 'extracting':
      return 'Extracting';
    case 'analyzed':
      return 'Extracted';
    case 'approved':
      return 'Approved';
    case 'rejected':
      return 'Rejected';
    case 'failed':
      return 'Failed';
    default:
      return status;
  }
}

// Mirrors settings.CLASSIFICATION_CONFIDENCE_THRESHOLD in backend/app/config.py —
// below this, the officer can correct the document type (the backend enforces
// the same gate).
export const CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.8;

// Mirrors KNOWN_DOCUMENT_TYPES in backend/app/extraction_prompts.py — the real
// document_type values, so passport/nric stay separate (unlike
// REQUIRED_DOCUMENT_TYPES above, whose 'id_document' is only a checklist
// slot). 'unrecognised' is the backend's "Other" catch-all.
export const DOCUMENT_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: 'bank_statement', label: 'Bank statement' },
  { value: 'certificate_of_incorporation', label: 'Certificate of incorporation' },
  { value: 'payslip', label: 'Payslip' },
  { value: 'passport', label: 'Passport' },
  { value: 'nric', label: 'NRIC' },
  { value: 'fatca_crs_declaration', label: 'FATCA/CRS declaration' },
  { value: 'certificate_of_incumbency', label: 'Certificate of incumbency' },
  { value: 'company_constitution', label: 'Company constitution' },
  { value: 'proof_of_address', label: 'Proof of address' },
  { value: 'source_of_wealth', label: 'Source of wealth' },
  { value: 'credit_report', label: 'Credit report' },
  { value: 'list_of_loans', label: 'List of loans' },
  { value: 'notice_of_assessment', label: 'Notice of assessment' },
  { value: 'unrecognised', label: 'Other' },
];

export const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  bank_statement: 'Bank statement',
  certificate_of_incorporation: 'Certificate of incorporation',
  payslip: 'Payslip',
  passport: 'Passport',
  nric: 'NRIC',
  fatca_crs_declaration: 'FATCA/CRS Declaration',
  certificate_of_incumbency: 'Certificate of incumbency',
  company_constitution: 'Company constitution',
  proof_of_address: 'Proof of address',
  source_of_wealth: 'Source of wealth',
  credit_report: 'Credit report',
  list_of_loans: 'List of loans',
  notice_of_assessment: 'Notice of assessment',
  unrecognised: 'Other',
};

export function formatDocumentType(
  documentType: string | null | undefined,
): string {
  if (!documentType) {
    return '—';
  }

  return (
    DOCUMENT_TYPE_LABELS[documentType] ??
    documentType
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (char) => char.toUpperCase())
  );
}


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

// Mirrors UpdateCaseRequest in backend/app/models.py. Only company_name is
// officer-editable today — status, submission_finalized and
// greenlink_case_id are system-managed and not exposed here.
export interface UpdatedCase {
  id: string;
  greenlink_case_id: string;
  company_name: string | null;
}

export async function updateCase(caseId: string, companyName: string): Promise<UpdatedCase> {
  const response = await authFetch(`/cases/${caseId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ company_name: companyName }),
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Failed to update case (${response.status}): ${detail}`);
  }

  return (await response.json()) as UpdatedCase;
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

export async function addExtractedField(
  documentId: string,
  fieldName: string,
  fieldValue: unknown,
): Promise<DocumentRecord> {
  const response = await authFetch(`/documents/${documentId}/extracted-fields`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      field_name: fieldName,
      field_value: fieldValue,
    }),
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Failed to add extracted field (${response.status}): ${detail}`);
  }

  return (await response.json()) as DocumentRecord;
}

export async function deleteExtractedField(
  documentId: string,
  fieldId: string,
): Promise<DocumentRecord> {
  const response = await authFetch(
    `/documents/${documentId}/extracted-fields/${fieldId}`,
    {
      method: 'DELETE',
    },
  );

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      `Failed to delete extracted field (${response.status}): ${detail}`,
    );
  }

  return (await response.json()) as DocumentRecord;
}

export async function approveDocument(
  documentId: string,
): Promise<DocumentRecord> {
  const response = await authFetch(`/documents/${documentId}/approve`, {
    method: 'POST',
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      `Failed to approve document (${response.status}): ${detail}`,
    );
  }

  return (await response.json()) as DocumentRecord;
}

export async function rejectDocument(
  documentId: string,
): Promise<DocumentRecord> {
  const response = await authFetch(
    `/documents/${documentId}/reject`,
    {
      method: 'POST',
    },
  );

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      `Reject failed (${response.status}): ${detail}`,
    );
  }

  return (await response.json()) as DocumentRecord;
}
// Mirrors UpdateDocumentTypeRequest in backend/app/models.py. Saving also
// kicks off a background extraction rerun against the new type — the
// returned record already reads status 'extracting'.
export async function updateDocumentType(
  documentId: string,
  documentType: string,
): Promise<DocumentRecord> {
  const response = await authFetch(`/documents/${documentId}/document-type`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ document_type: documentType }),
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Failed to update document type (${response.status}): ${detail}`);
  }

  return (await response.json()) as DocumentRecord;
}

export async function uploadDocuments(
  caseId: string,
  files: File[],
): Promise<BulkUploadResponse> {
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