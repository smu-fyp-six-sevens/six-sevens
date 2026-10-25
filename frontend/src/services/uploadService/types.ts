// API types. Those with a backend model are generated: backend/scripts/export_openapi.py writes openapi.json,
// `npm run gen:api` turns it into ../api.gen.ts, and CI fails when either is out of date. Never edit those by
// hand. The types written out below come from endpoints that return plain dicts, or exist only here.
import type { components } from '../api.gen';

type Schemas = components['schemas'];

export type BulkUploadItemResult = Schemas['BulkUploadItemResult'];
export type BulkUploadResponse = Schemas['BulkUploadResponse'];

// The case-status WebSocket adds the two detection flags (case_service._build_case_snapshot).
export type DocumentRecord = Schemas['DocumentRecord'] & {
  has_detection_report?: boolean;
  has_detection_error?: boolean;
};

export type CaseMatchComparison = Schemas['FieldComparison'];
export type DocumentCaseMatch = Schemas['DocumentCaseMatch'];
export type CaseMatchResult = Schemas['CaseMatchResult'];

// A document sitting in APPROVED status is, from an officer's point of view, still doing something.
export type DocumentDetectionStage = 'scanning' | 'detected' | 'skipped' | 'failed' | null;

export type CaseRiskLevel = NonNullable<Schemas['CaseRisk']['level']>;

// The backend types severity as a plain string; it is always a detection severity.
export type CaseRiskFinding = Omit<Schemas['ContributingFinding'], 'severity'> & { severity: DetectionSeverity };

export type CaseRisk = Omit<Schemas['CaseRisk'], 'contributing_findings'> & {
  contributing_findings: CaseRiskFinding[];
};

export type SupportingDocumentRecommendation = Schemas['SupportingDocumentRecommendation'];
export type RecommendationItem = Schemas['RecommendationItem'];

// The part of CaseSummary the case page reads (it also arrives in the WebSocket snapshot).
export type CaseRequirementCoverage = Pick<
  Schemas['CaseSummary'],
  | 'fulfilled_requirements'
  | 'missing_requirements'
  | 'proof_of_address_coverage'
  | 'id_document_coverage'
  | 'supporting_document_recommendations'
  | 'additional_documents'
  | 'analysis_ready'
  | 'analysis'
> & { risk: CaseRisk | null };

export type UpdatedCase = Pick<Schemas['CaseSummary'], 'id' | 'greenlink_case_id' | 'company_name'>;

export type DocumentDownloadResponse = Schemas['DocumentDownloadResponse'];

// PDF -> <iframe> (native browser PDF viewer), image -> <img>.
export type PreviewKind = 'pdf' | 'image' | 'unsupported';

// GET /documents/{id}/langgraph-trace returns the stored trace as a dict (backend/app/langgraph/trace.py).
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

// GET /documents/{id}/detection/report returns a dict (backend/app/langgraph/detection_report.py).
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

export type BulkActionFailure = Schemas['BulkActionFailure'];

// One audit trail entry (backend/app/events/envelope.py). An agent's run names its officer in detail.requested_by.
export type AuditEvent = Schemas['Event'];
export type BulkMoveResponse = Schemas['BulkMoveResponse'];
export type BulkDeleteResponse = Schemas['BulkDeleteResponse'];

// Lightweight view of a case for the "move document" picker.
export type CaseOption = Pick<Schemas['CaseListItem'], 'id' | 'greenlink_case_id' | 'company_name'>;

export type AdditionalDocument = Schemas['AdditionalDocument'];
export type AdditionalDocuments = Schemas['AdditionalDocuments'];
export type CaseAnalysisOverview = Schemas['CaseAnalysisOverview'];
export type CrossDocFinding = Schemas['CrossDocFinding'];
export type OutlookRequest = Schemas['OutlookRequest'];
export type RiskOutlook = Schemas['RiskOutlook'];

// The backend types the trace as a list of dicts; every step carries its name and time.
export type CaseAnalysis = Omit<Schemas['CaseAnalysis'], 'trace'> & { trace: CaseAnalysisTraceStep[] };

export interface CaseAnalysisTraceStep {
  step: string;
  at: number;
  [key: string]: unknown;
}

// GET /audit/cases/{id}/cost returns a dict: Claude spend at list price, from the audit trail's end-of-run events.
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

// parent_id marks a reply; edited_at is set once its author edits it; deleted_at marks a deleted note kept
// (text erased) as a placeholder above its replies.
export type CaseNote = Schemas['CaseNote'];
