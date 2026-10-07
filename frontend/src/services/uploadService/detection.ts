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
  await assertOk(response, 'Failed to fetch detection report');

  return (await response.json()) as DetectionReport;
}

// Interrupts an in-flight run (the "Scanning"/"Detecting" stage).
export async function cancelDetection(documentId: string): Promise<void> {
  const response = await authFetch(`/documents/${documentId}/detection/cancel`, { method: 'POST' });

  await assertOk(response, 'Failed to cancel detection');
}

// Re-launches detection after a cancelled or crashed run.
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
  { id: 'metadata-anomaly', label: 'Metadata anomaly' },
  // Runs both image checks (SCRUM-64 resolution/compression, SCRUM-65 tampering) and returns the result.
  { id: 'image-integrity', label: 'Image integrity' },
  // SCRUM-67: AI-generation likelihood score with every indicator's value and contribution.
  { id: 'ai-generation', label: 'AI generation' },
];

// Each check has its own result shape (FontConsistencyResult, LayoutAnomalyResult, ...).
export async function runIndividualDetectionCheck(
  documentId: string,
  checkId: IndividualCheckId,
): Promise<unknown> {
  const response = await authFetch(`/documents/${documentId}/detection/${checkId}`);

  if (response.status === 409) {
    const detail = await response.text();
    throw new Error(detail || 'Run full detection first before running an individual check.');
  }
  await assertOk(response, 'Check failed');

  return await response.json();
}
