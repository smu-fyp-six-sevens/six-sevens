import type { CaseDocumentsStatus } from '../services/uploadService';

// What the backend calls a case's documents status, and what the queue calls it.
const LABELS: Record<CaseDocumentsStatus, string> = {
  in_progress: 'In progress',
  analyzed: 'Ready for review',
  failed: 'Documents failed',
  stuck: 'Processing stuck',
  no_documents: 'Documents pending',
};

// The order the queue's sidebar lists them in.
export const STATUS_ORDER = Object.keys(LABELS) as CaseDocumentsStatus[];

export function statusLabel(status: string): string {
  return LABELS[status as CaseDocumentsStatus] ?? (status || 'Open');
}

// The backend value behind a label, or undefined when the label is not one of ours.
export function statusValue(label: string): CaseDocumentsStatus | undefined {
  return STATUS_ORDER.find((value) => LABELS[value] === label);
}
