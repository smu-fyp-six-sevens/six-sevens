import type { CaseFailure, CaseStage } from '../services/uploadService';

// In the order a case moves through them (see CaseStage in backend/app/models.py).
export const CASE_STAGES: CaseStage[] = [
  'open',
  'processing_documents',
  'pending_review',
  'documents_incomplete',
  'fraud_detection',
  'pending_evaluation',
  'case_analysed',
  'finalised',
  'failed',
];

// Detours rather than steps every case passes through: shown in the progress steps only while the case is there.
export const BRANCH_STAGES = new Set<CaseStage>(['documents_incomplete', 'failed']);

// Each name says what is happening to the case, or what it waits for, as a step in its flow.
const CASE_STAGE_LABELS: Record<CaseStage, string> = {
  open: 'Awaiting Documents',
  processing_documents: 'Processing Documents',
  pending_review: 'Pending Review',
  documents_incomplete: 'Missing Documents',
  fraud_detection: 'Detecting Fraud',
  pending_evaluation: 'Pending Evaluation',
  case_analysed: 'Ready to Finalise',
  finalised: 'Finalised',
  failed: 'Failed',
};

// One colour per group of stages, by who acts next, shared by the case queue's marks and filter, the badges, the
// Case Status card and the dashboard's pipeline: idle nothing yet, system the system is working, officer the
// officer has something to do (blocked: and can't go on without more documents), done analysed, closed
// finalised, failed something failed. Each group also has its own shape (components/StageMark.tsx), so the
// stages don't rest on colour alone.
export type StatusTone = 'idle' | 'system' | 'officer' | 'blocked' | 'done' | 'closed' | 'failed';

const CASE_STAGE_TONES: Record<CaseStage, StatusTone> = {
  open: 'idle',
  processing_documents: 'system',
  fraud_detection: 'system',
  pending_review: 'officer',
  documents_incomplete: 'blocked',
  pending_evaluation: 'officer',
  case_analysed: 'done',
  finalised: 'closed',
  failed: 'failed',
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
  return CASE_STAGE_TONES[stage as CaseStage] ?? 'idle';
}

export function caseStageLabel(stage: string | null | undefined): string {
  if (!stage) return CASE_STAGE_LABELS.open;
  return CASE_STAGE_LABELS[stage as CaseStage] ?? stage;
}
