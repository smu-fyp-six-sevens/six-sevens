// API types. Those with a backend model are generated: backend/scripts/export_openapi.py writes openapi.json,
// `npm run gen:api` turns it into ../api.gen.ts, and CI fails when either is out of date. Never edit those by
// hand. The types written out below come from endpoints that return plain dicts, or exist only here.
import type { components, operations } from '../api.gen';

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

// A tool's finding a specialist dismissed as a false positive: never scored, listed for the officer's review.
export type DismissedFinding = Schemas['DismissedFinding'];

export type SupportingDocumentRecommendation = Schemas['SupportingDocumentRecommendation'];
export type RecommendationItem = Schemas['RecommendationItem'];

// The part of CaseSummary the case page reads (it also arrives in the WebSocket snapshot).
export type CaseRequirementCoverage = Pick<
  Schemas['CaseSummary'],
  | 'fulfilled_requirements'
  | 'missing_requirements'
  | 'unapproved_requirements'
  | 'reviews_required'
  | 'proof_of_address_coverage'
  | 'id_document_coverage'
  | 'additional_documents'
  | 'analysis_ready'
  | 'analysis'
  | 'status'
  | 'failure'
  | 'findings_to_review'
> & { risk: CaseRisk | null };

// Where a case stands (see backend/app/case_stage.py), and what a failed case needs fixed.
export type CaseStage = Schemas['CaseStage'];
export type CaseFailure = Schemas['CaseFailure'];

export type UpdatedCase = Pick<Schemas['CaseSummary'], 'id' | 'greenlink_case_id' | 'company_name'>;

export type DocumentDownloadResponse = Schemas['DocumentDownloadResponse'];

// What the content guard found at ingest (backend/app/guardrails/content_guard.py).
export type ContentGuardResponse = Schemas['ContentGuardResponse'];
export type ContentGuardFinding = Schemas['ContentGuardFinding'];

// The case queue: one page of rows, the counts for its sidebar, and jump-to-case suggestions.
export type CaseDocumentsStatus = Schemas['CaseDocumentsStatus'];
export type CaseListPage = Schemas['CaseListPage'];
export type CaseCounts = Schemas['CaseCounts'];
export type CaseSearchResult = Schemas['CaseSearchResult'];
export type CaseListItem = Schemas['CaseListItem'];
// GET /cases's query parameters (the backend's Literals).
type ListCasesQuery = NonNullable<operations['list_cases_cases_get']['parameters']['query']>;
export type CaseSort = NonNullable<ListCasesQuery['sort']>;
// "unscored" is a case no analysis run has scored yet.
export type CaseRiskFilter = NonNullable<ListCasesQuery['risk']>[number];

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
  confidence?: number;
  agent_confidences?: { specialist: string; confidence: number }[];
  // Where on the document it was found, for the checks that measure an area (image and layout checks).
  // `page` is 1-based (null for an image upload); `bbox` is [x0, y0, x1, y1] from the top-left, in
  // `bbox_units`, within a page of `page_size` when the check reports it.
  page?: number | null;
  bbox?: [number, number, number, number] | null;
  bbox_units?: 'pt' | 'px';
  page_size?: [number, number] | null;
  // The font and spelling checks list each place instead (page 1-based, bbox in `bbox_units`).
  locations?: { page: number; bbox?: [number, number, number, number] }[];
  // The check's own label for it (image checks: indicator; layout check: type).
  indicator?: string;
  type?: string;
}

export interface DetectionReport {
  document_id: string;
  document_type: string;
  verdict: 'flagged' | 'clean' | 'inconclusive';
  summary: string;
  confidence: number;
  agent_decisions: {
    agent: string;
    decision_id: string;
    decision: string;
    confidence: number;
  }[];
  findings: DetectionFinding[];
  consulted: {
    specialist: string;
    title: string;
    brief: string;
    tools_called: string[];
    summary: string;
    uncertain: boolean;
    dismissed: { id: string; tool: string | null; summary: string; reason: string }[];
    second_look: boolean;
  }[];
  second_look: { from: string; to: string; question: string; reason: string } | null;
  unable_to_assess: { specialist: string; tool: string | null; reason: string | null }[];
  // Checks the document had nothing for (no images, no metadata, no dates): a pass, shown for the record.
  nothing_to_check: { specialist: string; tool: string | null; reason: string | null }[];
  generated_at: string;
  // Set when a later run crashed or was cancelled: this report, from the run before, still stands.
  last_run_error?: string;
}

export type IndividualCheckId =
  | 'font-consistency'
  | 'layout-anomalies'
  | 'spelling-anomaly'
  | 'numerical-validation'
  | 'date-anomalies'
  | 'page-consistency'
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
// A document the risk score counts that isn't scanned yet, and what it's waiting for.
export type WaitingDocument = Schemas['WaitingDocument'];
// A run's headline, cited reasons and "would help" line (backend case_pipeline/risk/summary.py).
export type AnalysisSummary = Schemas['AnalysisSummary'];
// One row of GET /cases/{id}/analysis/runs, newest first.
export type CaseAnalysisRunItem = Schemas['CaseAnalysisRunItem'];
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

// GET /audit/cases/{id}/cost: Claude spend at list price, from the audit trail's end-of-run events.
export type CaseCost = Schemas['CaseCost'];
export type CaseCostRun = Schemas['CaseCostRun'];
// A finished run and how long it took (success only; document.analyzed, document.detected, case.analysis.completed).
export type CaseCostTiming = Schemas['CaseCostTiming'];

// parent_id marks a reply; edited_at is set once its author edits it; deleted_at marks a deleted note kept
// (text erased) as a placeholder above its replies.
export type CaseNote = Schemas['CaseNote'];

// Finding reviews (backend/app/detection_metrics.py). "document": a finding on one document (check_name is its
// tool). "case": a cross-document finding from the case analysis (check_name is its category).
// precision = correct / (correct + wrong); recall = correct / (correct + missed); null: nothing to measure yet.
export type FindingReview = Schemas['FindingReview'];
export type ReviewScope = FindingReview['scope'];
export type ReviewVerdict = NonNullable<FindingReview['verdict']>;
export type CheckMetrics = Schemas['CheckMetrics'];
export type MetricsSummary = Schemas['MetricsSummary'];
// The backend keys checks by scope as a plain dict; it always has both scopes.
export type CaseFindingReviews = Omit<Schemas['CaseFindingReviews'], 'checks'> & {
  checks: Record<ReviewScope, string[]>;
};
export type DetectionMetrics = Schemas['DetectionMetrics'];
