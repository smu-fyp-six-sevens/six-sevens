// Records as the API sends them, for unit tests: every required field set, then the test's own values on top.
import type { CaseRequirementCoverage, DocumentRecord } from '../services/uploadService';

export function makeDocument(overrides: Partial<DocumentRecord> = {}): DocumentRecord {
  return {
    id: 'doc-1',
    case_id: 'case-1',
    filename: 'Payslip_Oct2026.pdf',
    storage_path: 'case-1/doc-1_Payslip_Oct2026.pdf',
    status: 'analyzed',
    document_type: 'payslip',
    classification_confidence: 0.97,
    routing_status: 'accepted',
    extraction_confidence: 0.95,
    extracted_fields: {
      employer_name: { value: 'Brightwave Analytics Pte Ltd', confidence: 0.98 },
      net_pay: { value: '4160.00', confidence: 0.97 },
    },
    missing_required_fields: [],
    fulfilled_requirements: ['payslip'],
    fields_reviewed_at: null,
    evidence: [],
    escalation_reason: null,
    failure_reason: null,
    metadata: null,
    retry_count: 0,
    queued_at: '2026-11-01T02:00:00Z',
    processing_started_at: '2026-11-01T02:00:05Z',
    uploaded_at: '2026-11-01T02:00:00Z',
    ...overrides,
  };
}

export function makeCaseSummary(overrides: Partial<CaseRequirementCoverage> = {}): CaseRequirementCoverage {
  return {
    fulfilled_requirements: [],
    missing_requirements: [],
    unapproved_requirements: [],
    reviews_required: true,
    proof_of_address_coverage: { covered: 0, total: 0, unmatched_names: [] },
    id_document_coverage: { covered: 0, total: 0, missing_side: null },
    additional_documents: null,
    analysis_ready: false,
    analysis: null,
    status: 'open',
    failure: null,
    findings_to_review: null,
    risk: null,
    ...overrides,
  };
}
