import { assertOk } from './request';
import { authFetch } from '../../lib/api';
import type { CaseFindingReviews, CaseStage, DetectionMetrics, ReviewScope, ReviewVerdict } from './types';

// Every review in the case, with precision and recall per document, across documents and for the case.
export async function getCaseFindingReviews(caseId: string): Promise<CaseFindingReviews> {
  const response = await authFetch(`/cases/${caseId}/finding-reviews`);

  await assertOk(response, 'Failed to load the finding reviews');

  return (await response.json()) as CaseFindingReviews;
}

// Marks one finding correct or wrong (signed by the logged-in officer); verdict null clears the mark.
// Returns the case's reviews and metrics afresh.
export async function setFindingVerdict(
  caseId: string,
  review: {
    scope: ReviewScope;
    documentId: string | null;
    findingId: string;
    checkName: string;
    checkCode?: string | null;
    summary: string;
    verdict: ReviewVerdict | null;
  },
): Promise<CaseFindingReviews> {
  const response = await authFetch(`/cases/${caseId}/finding-reviews/verdict`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      scope: review.scope,
      document_id: review.documentId,
      finding_id: review.findingId,
      check_name: review.checkName,
      check_code: review.checkCode ?? null,
      summary: review.summary,
      verdict: review.verdict,
    }),
  });

  await assertOk(response, 'Failed to save the review');

  return (await response.json()) as CaseFindingReviews;
}

export interface FindingVerdictInput {
  scope: ReviewScope;
  documentId: string | null;
  findingId: string;
  checkName: string;
  checkCode?: string | null;
  summary: string;
  verdict: ReviewVerdict | null;
}

// One group's review in one request: the findings the officer ticked are correct, the rest wrong.
export async function setFindingVerdicts(caseId: string, verdicts: FindingVerdictInput[]): Promise<CaseFindingReviews> {
  const response = await authFetch(`/cases/${caseId}/finding-reviews/verdicts`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      verdicts: verdicts.map((review) => ({
        scope: review.scope,
        document_id: review.documentId,
        finding_id: review.findingId,
        check_name: review.checkName,
        check_code: review.checkCode ?? null,
        summary: review.summary,
        verdict: review.verdict,
      })),
    }),
  });

  await assertOk(response, 'Failed to save the review');

  return (await response.json()) as CaseFindingReviews;
}

// An issue no finding raised, against the check that should have caught it (a false negative).
export async function reportMissedIssue(
  caseId: string,
  issue: { scope: ReviewScope; documentId: string | null; checkName: string; note?: string },
): Promise<CaseFindingReviews> {
  const response = await authFetch(`/cases/${caseId}/finding-reviews/missed`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      scope: issue.scope,
      document_id: issue.documentId,
      check_name: issue.checkName,
      note: issue.note ?? null,
    }),
  });

  await assertOk(response, 'Failed to save the missed issue');

  return (await response.json()) as CaseFindingReviews;
}

// Corrects a reported missed issue: the check it's filed against, and its note.
export async function updateMissedIssue(
  caseId: string,
  reviewId: string,
  issue: { checkName: string; note?: string },
): Promise<CaseFindingReviews> {
  const response = await authFetch(`/cases/${caseId}/finding-reviews/${reviewId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ check_name: issue.checkName, note: issue.note ?? null }),
  });

  await assertOk(response, 'Failed to save the missed issue');

  return (await response.json()) as CaseFindingReviews;
}

export async function deleteFindingReview(caseId: string, reviewId: string): Promise<CaseFindingReviews> {
  const response = await authFetch(`/cases/${caseId}/finding-reviews/${reviewId}`, { method: 'DELETE' });

  await assertOk(response, 'Failed to remove the review');

  return (await response.json()) as CaseFindingReviews;
}

// The dashboard's filters for the metrics: cases created on or after `since` (YYYY-MM-DD), in these statuses.
export interface DetectionMetricsFilters {
  since?: string | null;
  statuses?: CaseStage[];
}

// Precision and recall per check across every reviewed case matching the filters (the dashboard).
export async function getDetectionMetrics(filters: DetectionMetricsFilters = {}): Promise<DetectionMetrics> {
  const params = new URLSearchParams();
  if (filters.since) params.set('since', filters.since);
  for (const status of filters.statuses ?? []) params.append('status', status);
  const query = params.toString();
  const response = await authFetch(`/detection-metrics${query ? `?${query}` : ''}`);

  await assertOk(response, 'Failed to load the detection metrics');

  return (await response.json()) as DetectionMetrics;
}
