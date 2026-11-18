import { assertOk } from './request';
import { authFetch } from '../../lib/api';
import type {
  DetectionReport,
  IndividualCheckId,
  LangGraphTrace,
} from './types';

export async function getLangGraphTrace(documentId: string): Promise<LangGraphTrace | null> {
  const response = await authFetch(`/documents/${documentId}/langgraph-trace`);

  if (response.status === 404) {
    return null;
  }
  await assertOk(response, 'Failed to fetch langgraph trace');

  return (await response.json()) as LangGraphTrace;
}

export async function getDetectionReport(documentId: string): Promise<DetectionReport | null> {
  const response = await authFetch(`/documents/${documentId}/detection/report`);

  if (response.status === 404) {
    return null;
  }
  // A 502 is a run that crashed or was cancelled; its error stays in the console.
  await assertOk(response, 'Failed to fetch detection report', {
    502: 'The last detection run did not finish. Run detection again.',
  });

  return (await response.json()) as DetectionReport;
}

// Interrupts a run. "cancelling": one in flight, which stops at its next step; "cancelled": one that was still
// waiting for a worker, stopped at once.
export async function cancelDetection(documentId: string): Promise<'cancelling' | 'cancelled'> {
  const response = await authFetch(`/documents/${documentId}/detection/cancel`, { method: 'POST' });

  await assertOk(response, 'Failed to cancel detection');

  return ((await response.json()) as { status: 'cancelling' | 'cancelled' }).status;
}

// Runs detection again: after a cancelled or crashed run, or over a finished report (replaced once the new run
// completes).
export async function retryDetection(documentId: string): Promise<void> {
  const response = await authFetch(`/documents/${documentId}/detection/retry`, { method: 'POST' });

  await assertOk(response, 'Failed to retry detection');
}

export const INDIVIDUAL_DETECTION_CHECKS: { id: IndividualCheckId; label: string }[] = [
  { id: 'font-consistency', label: 'Font consistency' },
  { id: 'layout-anomalies', label: 'Layout anomalies' },
  { id: 'spelling-anomaly', label: 'Spelling anomaly' },
  { id: 'numerical-validation', label: 'Numerical validation' },
  { id: 'date-anomalies', label: 'Date anomalies' },
  { id: 'company-name-consistency', label: 'Company name consistency' },
  { id: 'external-validation', label: 'External validation' },
  { id: 'metadata-anomaly', label: 'Metadata anomaly' },
  { id: 'image-integrity', label: 'Image integrity' },
  { id: 'ai-generation', label: 'AI Generation' },
  { id: 'page-consistency', label: 'Page consistency' },
];

// Each check has its own result shape (FontConsistencyResult, LayoutAnomalyResult, ...).
export async function runIndividualDetectionCheck(
  documentId: string,
  checkId: IndividualCheckId,
): Promise<unknown> {
  const response = await authFetch(`/documents/${documentId}/detection/${checkId}`);

  // A 409's detail says why (no full report yet); assertOk shows it.
  await assertOk(response, 'Check failed');

  return await response.json();
}
