import type { CaseFailure, CaseStage } from '../services/uploadService';

// In the order a case moves through them (see CaseStage in backend/app/models.py).
export const CASE_STAGES: CaseStage[] = [
  'open',
  'processing_documents',
  'pending_review',
  'documents_incomplete',
  'fraud_detection',
  'ready_for_analysis',
  'pending_evaluation',
  'case_analysed',
  'finalised',
  'failed',
];

// Detours rather than steps every case passes through: shown in the progress steps only while the case is there.
export const BRANCH_STAGES = new Set<CaseStage>(['documents_incomplete', 'failed']);

const CASE_STAGE_LABELS: Record<CaseStage, string> = {
  open: 'Open',
  processing_documents: 'Processing Documents',
  pending_review: 'Pending Review',
  documents_incomplete: 'Documents Incomplete',
  fraud_detection: 'Fraud Detection',
  ready_for_analysis: 'Ready for Analysis',
  case_analysed: 'Case Analysed',
  pending_evaluation: 'Pending Evaluation',
  finalised: 'Finalised',
  failed: 'Failed',
};

// Stages from before these ones, which a case can still hold until the backend brings it up to date.
const LEGACY_STAGE_LABELS: Record<string, string> = {
  documents_uploaded: 'Processing Documents',
  documents_classified: 'Processing Documents',
  documents_extracted: 'Pending Review',
  manual_review: 'Pending Review',
  documents_approved: 'Fraud Detection',
  documents_rejected: 'Documents Incomplete',
  detection_triggered: 'Fraud Detection',
  documents_failed: 'Failed',
  detection_failed: 'Failed',
  analysing: 'Ready for Analysis',
  completed: 'Case Analysed',
  additional_documents_required: 'Pending Evaluation',
};

// One colour per group of stages, by who acts next, shared by the case queue's dots and filter and the case
// page's Case Status badge: grey nothing yet, blue the system is working, amber the officer has something to do,
// green analysed, slate closed, red something failed. Within a group the shade deepens as the case moves on.
export type StatusTone =
  | 'grey' | 'blue-1' | 'blue-2' | 'amber-1' | 'amber-2' | 'amber-3' | 'amber-4' | 'green' | 'slate' | 'red';

const CASE_STAGE_TONES: Record<CaseStage, StatusTone> = {
  open: 'grey',
  processing_documents: 'blue-1',
  fraud_detection: 'blue-2',
  pending_review: 'amber-1',
  documents_incomplete: 'amber-2',
  ready_for_analysis: 'amber-3',
  pending_evaluation: 'amber-4',
  case_analysed: 'green',
  finalised: 'slate',
  failed: 'red',
};

// What a failed case needs fixed, and how (see CaseFailure in backend/app/models.py).
const FAILURE_MESSAGES: Record<CaseFailure, string> = {
  document_processing: 'Document processing failed. Retry or remove the failed document.',
  fraud_detection: 'Fraud detection failed on a document. Retry it from the document page.',
  case_analysis: 'The case analysis failed. Run it again.',
};

export function caseFailureMessage(failure: CaseFailure | null | undefined): string | null {
  return failure ? FAILURE_MESSAGES[failure] : null;
}

export function isCaseStage(value: string): value is CaseStage {
  return (CASE_STAGES as string[]).includes(value);
}

export function caseStageTone(stage: string | null | undefined): StatusTone {
  return CASE_STAGE_TONES[stage as CaseStage] ?? 'grey';
}

// A value from before these stages reads as the stage it became; anything else as itself ("some_stage" -> "Some stage").
export function caseStageLabel(stage: string | null | undefined): string {
  if (!stage) return 'Open';
  return (
    CASE_STAGE_LABELS[stage as CaseStage] ??
    LEGACY_STAGE_LABELS[stage] ??
    stage.charAt(0).toUpperCase() + stage.slice(1).replace(/_/g, ' ')
  );
}
