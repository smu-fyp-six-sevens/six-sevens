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
  classification_confidence: number | null;
  routing_status: 'accepted' | 'manual_review' | null;
  storage_path: string | null;
  status: string;
  retry_count: number;
  uploaded_at: string;
  queued_at: string | null;
  processing_started_at: string | null;
  failure_reason: string | null;
  evidence: string[];
  fulfilled_requirements: string[];
  missing_required_fields: string[];
  extracted_fields: Record<string, unknown> | null;
  // Extraction's own confidence in extracted_fields.
  extraction_confidence: number | null;
  escalation_reason: string | null;
  metadata: Record<string, unknown> | null;
  // Only present on documents pushed via the case-status websocket.
  has_detection_report?: boolean;
}

// Mirrors DocumentCaseMatch / CaseMatchResult in backend/app/case_match.py.
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

// A document sitting in APPROVED status is, from an officer's point of view, still doing something.
export type DocumentDetectionStage = 'scanning' | 'detected' | 'skipped' | null;

// Mirrors the subset of CaseSummary in backend/app/models.py this page needs.
export interface CaseRequirementCoverage {
  fulfilled_requirements: string[];
  recommended_additional_documents: string[];
  // Identified individuals on the case with an accepted ID document.
  proof_of_address_coverage: { covered: number; total: number; unmatched_names: string[] };
  // How many of the case's identified individuals (grouped the same way as proof_of_address_coverage.
  id_document_coverage: { covered: number; total: number; missing_side: string | null };
}

// Mirrors UpdateCaseRequest in backend/app/models.py.
export interface UpdatedCase {
  id: string;
  greenlink_case_id: string;
  company_name: string | null;
}

// Mirrors DocumentDownloadResponse in backend/app/models.py.
export interface DocumentDownloadResponse {
  document_id: string;
  signed_url: string;
  expires_in: number;
}

// PDF -> <iframe> (native browser PDF viewer), image -> <img>.
export type PreviewKind = 'pdf' | 'image' | 'unsupported';

// Dev-only window into the langgraph-test branch's pipeline (see backend/app/langgraph/trace.py).
export interface LangGraphTraceStep {
  step: string;
  at: number;
  [key: string]: unknown;
}

export interface LangGraphTrace {
  document_id: string;
  started_at: number;
  steps: LangGraphTraceStep[];
  [key: string]: unknown;
}

export type DetectionSeverity = 'info' | 'low' | 'medium' | 'high';

export interface DetectionFinding {
  id: string;
  tool: string;
  summary: string;
  severity: DetectionSeverity;
  rationale: string;
  specialists: string[];
  via_second_look: boolean;
}

export interface DetectionReport {
  document_id: string;
  document_type: string;
  verdict: 'flagged' | 'clean' | 'inconclusive';
  summary: string;
  findings: DetectionFinding[];
  consulted: {
    specialist: string;
    title: string;
    brief: string;
    tools_called: string[];
    summary: string;
    uncertain: boolean;
    // Absent on reports stored before dismissals existed.
    dismissed?: { id: string; tool: string | null; summary: string; reason: string }[];
    second_look: boolean;
  }[];
  second_look: { from: string; to: string; question: string; reason: string } | null;
  unable_to_assess: { specialist: string; tool: string | null; reason: string | null }[];
  generated_at: string;
}

export type IndividualCheckId =
  | 'font-consistency'
  | 'layout-anomalies'
  | 'spelling-anomaly'
  | 'numerical-validation'
  | 'date-anomalies'
  | 'metadata-anomaly'
  | 'image-integrity';

export interface BulkActionFailure {
  document_id: string;
  reason: string;
}

export interface BulkMoveResponse {
  moved: string[];
  failed: BulkActionFailure[];
}

export interface BulkDeleteResponse {
  deleted: string[];
  failed: BulkActionFailure[];
}

// Lightweight view of a case for the "move document" picker.
export interface CaseOption {
  id: string;
  greenlink_case_id: string;
  company_name: string | null;
}
