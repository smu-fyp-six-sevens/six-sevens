import type { CaseStage } from '../services/uploadService';

// In the order a case moves through them (see CaseStage in backend/app/models.py).
export const CASE_STAGES: CaseStage[] = [
  'open',
  'documents_uploaded',
  'documents_classified',
  'documents_extracted',
  'documents_failed',
  'manual_review',
  'documents_incomplete',
  'documents_approved',
  'documents_rejected',
  'detection_triggered',
  'detection_failed',
  'ready_for_analysis',
  'additional_documents_required',
  'case_analysed',
  'finalised',
];

// Detours rather than steps every case passes through: shown in the progress steps only while the case is there.
export const BRANCH_STAGES = new Set<CaseStage>([
  'documents_failed',
  'manual_review',
  'documents_incomplete',
  'documents_rejected',
  'detection_failed',
  'additional_documents_required',
]);

const CASE_STAGE_LABELS: Record<CaseStage, string> = {
  open: 'Open',
  documents_uploaded: 'Documents Uploaded',
  documents_classified: 'Documents Classified',
  documents_extracted: 'Documents Extracted',
  documents_failed: 'Documents Failed',
  manual_review: 'Manual Review',
  documents_incomplete: 'Incomplete Documents',
  documents_approved: 'Documents Approved',
  documents_rejected: 'Documents Rejected',
  detection_triggered: 'Detection Triggered',
  detection_failed: 'Detection Failed',
  ready_for_analysis: 'Ready for Analysis',
  additional_documents_required: 'Additional Documents Required',
  case_analysed: 'Case Analysed',
  finalised: 'Finalised',
};

// One colour per stage, shared by the case queue's filter and cards and the case page's Case Progress badge.
export type StatusTone =
  | 'grey' | 'sky' | 'blue' | 'indigo' | 'red' | 'orange' | 'amber' | 'gold'
  | 'teal' | 'rose' | 'purple' | 'maroon' | 'lime' | 'cyan' | 'green' | 'slate';

const CASE_STAGE_TONES: Record<CaseStage, StatusTone> = {
  open: 'grey',
  documents_uploaded: 'sky',
  documents_classified: 'blue',
  documents_extracted: 'indigo',
  documents_failed: 'red',
  manual_review: 'orange',
  documents_incomplete: 'gold',
  documents_approved: 'teal',
  documents_rejected: 'rose',
  detection_triggered: 'purple',
  detection_failed: 'maroon',
  ready_for_analysis: 'lime',
  additional_documents_required: 'cyan',
  case_analysed: 'green',
  finalised: 'slate',
};

export function caseStageTone(stage: string | null | undefined): StatusTone {
  return CASE_STAGE_TONES[stage as CaseStage] ?? 'grey';
}

export interface QueueStatus {
  label: string;
  tone: StatusTone;
  // Where it sits among the queue's filter chips: the order a case moves through them.
  rank: number;
}

// A case's status in the queue: its stage, except while a document is stuck in processing (computed
// live from how long it has been there, see backend monitoring.is_stuck), which needs attention first.
export function queueStatus(stage: string | null | undefined, documentsStatus: string | null | undefined): QueueStatus {
  if (documentsStatus === 'stuck') {
    return { label: 'Processing Stuck', tone: 'amber', rank: CASE_STAGES.indexOf('documents_classified') + 0.5 };
  }
  const rank = CASE_STAGES.indexOf(stage as CaseStage);
  return { label: caseStageLabel(stage), tone: caseStageTone(stage), rank: rank >= 0 ? rank : CASE_STAGES.length };
}

// A value from before these stages existed reads as itself ("some_stage" -> "Some stage").
export function caseStageLabel(stage: string | null | undefined): string {
  if (!stage) return 'Open';
  return CASE_STAGE_LABELS[stage as CaseStage] ?? stage.charAt(0).toUpperCase() + stage.slice(1).replace(/_/g, ' ');
}

// Whether a change took the case back to an earlier stage (e.g. a document added to an analysed case).
// Reopening a finalised case isn't a step back, and an unknown stage can't be placed, so neither counts.
export function isMovedBack(fromStage: string | null, toStage: string): boolean {
  if (!fromStage || fromStage === 'finalised') return false;
  const from = CASE_STAGES.indexOf(fromStage as CaseStage);
  const to = CASE_STAGES.indexOf(toStage as CaseStage);
  return from >= 0 && to >= 0 && to < from;
}
