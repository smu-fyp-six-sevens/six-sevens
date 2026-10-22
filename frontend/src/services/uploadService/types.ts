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
  // When an officer saved the fields of an Other document; approving one needs it.
  fields_reviewed_at?: string | null;
  escalation_reason: string | null;
  metadata: Record<string, unknown> | null;
  // Only present on documents pushed via the case-status websocket.
  has_detection_report?: boolean;
  has_detection_error?: boolean;
}

// Mirrors DocumentCaseMatch / CaseMatchResult in backend/app/case_match.py.
export interface CaseMatchComparison {
  check: 'company_uen' | 'company_name' | 'individual_id' | 'individual_name';
  outcome: 'match' | 'mismatch';
  document_field: string;
  document_value: string;
  reference_document_id?: string | null;
  reference_filename?: string | null;
  other_document_id?: string | null;
  other_filename?: string | null;
  case_value: string | null;
  reason: string;
}

export interface DocumentCaseMatch {
  document_id: string;
  filename: string;
  document_type: string | null;
  status: 'match' | 'mismatch' | 'manual_review' | 'pending';
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
export type DocumentDetectionStage = 'scanning' | 'detected' | 'skipped' | 'failed' | null;

// Mirrors CaseRisk in backend/app/models.py (scored in backend/app/case_risk.py).
export type CaseRiskLevel = 'Low' | 'Medium' | 'High' | 'Critical';

export interface CaseRiskFinding {
  document_id: string;
  filename: string;
  document_type: string | null;
  finding_id: string;
  tool: string | null;
  severity: DetectionSeverity;
  summary: string;
  rationale: string;
  points: number;
  // The specialist kept the tool's finding without assessing it.
  unreviewed: boolean;
  category?: string | null;
  // "cross_document" for a finding from the case analysis rather than one document's scan.
  source?: 'document' | 'cross_document';
  // The same finding on other documents is scored once. On the copy that scores: how many other documents carry it.
  also_on?: number;
  // On a copy that scores 0: the finding_id of the one that scored.
  repeat_of?: string | null;
}

export interface CaseRisk {
  // Only 'scored' carries a level; the others say why there isn't one yet.
  state: 'scored' | 'pending' | 'failed' | 'no_documents';
  level: CaseRiskLevel | null;
  // 0-100, anchored to level (see SCORE_BANDS in backend/app/case_risk.py).
  score: number | null;
  // The raw points total the level is banded from.
  points: number | null;
  contributing_findings: CaseRiskFinding[];
  unassessed_checks: { document_id: string; filename: string; tool: string | null; reason: string | null }[];
  reasons: string[];
  documents_scanned: number;
  documents_total: number;
}

// Mirrors SupportingDocumentRecommendation in backend/app/models.py (see backend/app/supporting_documents.py).
export interface SupportingDocumentRecommendation {
  document_type:
    | 'purchase_order'
    | 'sales_invoice'
    | 'related_party_repayment_evidence'
    | 'bank_loan_evidence';
  // What to ask for, e.g. "Purchase Order".
  title: string;
  // Why: what the source document shows.
  reason: string;
  // The ask, introducing items, e.g. "Request purchase orders for these transactions:".
  request: string;
  source_document_id: string;
  source_filename: string;
  // Bank statement transactions, or financial statement lines.
  items: RecommendationItem[];
}

// Mirrors RecommendationItem in backend/app/models.py.
export interface RecommendationItem {
  date: string | null;
  // The transaction description or line label, as printed.
  description: string;
  // Negative for a payment out, positive for a receipt.
  amount: number;
  // A financial statement line's prior-year figure, where it has one.
  prior_amount: number | null;
  // The supplier paid or customer paying, when the statement names one.
  counterparty: string | null;
  reason: string;
  // The lines this one totals, e.g. a total's portions — shown as a table of these
  // lines plus this one as its total row.
  breakdown: RecommendationItem[];
}

// Mirrors the subset of CaseSummary in backend/app/models.py this page needs.
export interface CaseRequirementCoverage {
  fulfilled_requirements: string[];
  // The checklist requirements no accepted document has fulfilled yet.
  missing_requirements: string[];
  // Identified individuals on the case with an accepted ID document.
  proof_of_address_coverage: { covered: number; total: number; unmatched_names: string[] };
  // How many of the case's identified individuals (grouped the same way as proof_of_address_coverage.
  id_document_coverage: { covered: number; total: number; missing_side: string | null };
  // null when the backend couldn't load the case's detection reports.
  risk: CaseRisk | null;
  // Empty when nothing in the case needs further evidence.
  supporting_document_recommendations: SupportingDocumentRecommendation[];
  // The same requests merged with the agent's; null for a run from before it existed.
  additional_documents?: AdditionalDocuments | null;
  // Every approved document has been scanned, so the case analysis can be run.
  analysis_ready?: boolean;
  // The latest per-case analysis in brief; null when the case was never analysed.
  analysis?: CaseAnalysisOverview | null;
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
  // Checks the document had nothing for (no images, no metadata, no dates): a pass, shown for the
  // record. Absent on reports stored before it existed.
  nothing_to_check?: { specialist: string; tool: string | null; reason: string | null }[];
  generated_at: string;
}

export type IndividualCheckId =
  | 'font-consistency'
  | 'layout-anomalies'
  | 'spelling-anomaly'
  | 'numerical-validation'
  | 'date-anomalies'
  | 'company-name-consistency'
  | 'external-validation'
  | 'metadata-anomaly'
  | 'image-integrity'
  | 'ai-generation';

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

// Mirrors AdditionalDocument in backend/app/models.py.
export interface AdditionalDocument {
  title: string;
  why: string;
  source: 'checklist' | 'finding';
  addresses: string[];
  // False when more evidence cannot change the score.
  affects_score: boolean;
  score_if_supports: number | null;
  level_if_supports: string | null;
  // The checklist rule's detail, for checklist items.
  recommendation: SupportingDocumentRecommendation | null;
}

// Mirrors AdditionalDocuments in backend/app/models.py.
export interface AdditionalDocuments {
  items: AdditionalDocument[];
  reason: 'none_needed' | 'no_reducible_findings' | 'model_failed' | 'budget' | null;
  current_score: number | null;
  current_level: string | null;
  best_case_score: number | null;
  best_case_level: string | null;
}

// Mirrors CaseAnalysisOverview in backend/app/models.py.
export interface CaseAnalysisOverview {
  run_id: string;
  status: 'running' | 'complete' | 'failed' | 'superseded';
  // The case's documents changed since this run read them.
  stale: boolean;
  finished_at: string | null;
  finding_count: number;
}

// Mirrors case_pipeline/models.py.
export interface CrossDocFinding {
  id: string;
  hypothesis_id: string;
  summary: string;
  rationale: string;
  severity: 'low' | 'medium' | 'high';
  category: string;
  citations: string[];
  document_ids: string[];
  probe_summary: string;
  breach_flags: string[];
}

export interface OutlookRequest {
  title: string;
  what_it_shows: string;
  addresses: string[];
  expected_effect: 'full' | 'partial';
  source: 'agent' | 'rule';
  score_if_supports: number | null;
  level_if_supports: string | null;
  score_if_contradicts: number | null;
}

export interface RiskOutlook {
  current_score: number | null;
  current_level: string | null;
  requests: OutlookRequest[];
  best_case_score: number | null;
  best_case_level: string | null;
  unreducible_finding_ids: string[];
}

export interface CaseAnalysis {
  run_id: string;
  case_id: string;
  status: 'running' | 'complete' | 'failed' | 'superseded';
  started_at: string;
  finished_at: string | null;
  error: string | null;
  findings: CrossDocFinding[];
  outlook: RiskOutlook | null;
  summary: string | null;
  // Comparisons that found the documents agree, e.g. "uen is the same on a.pdf, b.pdf".
  consistent_checks?: string[];
  // Tool calls, models and counts of the run.
  audit?: { tool_calls?: unknown[]; [key: string]: unknown };
  // Step-by-step log of the run (debug); written as the run goes.
  trace: CaseAnalysisTraceStep[];
  stale: boolean;
}

export interface CaseAnalysisTraceStep {
  step: string;
  at: number;
  [key: string]: unknown;
}

// GET /audit/cases/{id}/cost: Claude spend at list price, from the audit trail's end-of-run events.
export interface CaseCostRun {
  occurred_at: string;
  type: string;
  run: 'document' | 'extraction_rerun' | 'detection' | 'case_analysis' | null;
  document_id: string | null;
  llm_cost_usd: number;
  llm_calls: number | null;
  llm_cache_read_tokens: number | null;
}

export interface CaseCost {
  case_id: string;
  total_usd: number;
  documents_usd: Record<string, number>;
  case_analysis_usd: number;
  runs: CaseCostRun[];
}
