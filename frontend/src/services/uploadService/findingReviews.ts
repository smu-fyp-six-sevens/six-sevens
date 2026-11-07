import { assertOk } from './request';
import { authFetch } from '../../lib/api';
import { addDays, parseDay } from '../../utils/dateRange';
import type {
  CaseFindingReviews,
  CaseStage,
  DetectionMetrics,
  ReviewReason,
  FeedbackDetail,
  ReviewScope,
  ReviewVerdict,
} from './types';

// Every review in the case, with precision and recall per document, across documents and for the case.
export async function getCaseFindingReviews(caseId: string): Promise<CaseFindingReviews> {
  const response = await authFetch(`/cases/${caseId}/finding-reviews`);

  await assertOk(response, 'Failed to load the finding reviews');

  return (await response.json()) as CaseFindingReviews;
}

// One finding, named by its fingerprint (and its document, for a document finding); verdict null clears the mark.
export interface FindingVerdictInput {
  scope: ReviewScope;
  documentId: string | null;
  fingerprint: string;
  verdict: ReviewVerdict | null;
  // Why the agents got it wrong: required with a false alarm (wrong) or a set-aside finding found real (correct), and for
  // 'other' a note too.
  reason?: ReviewReason | null;
  note?: string | null;
}

// One group's review in one request, signed by the logged-in officer: the findings the officer ticked are the ones the
// agents got wrong, the rest right. Returns the case's reviews and metrics afresh.
export async function setFindingVerdicts(caseId: string, verdicts: FindingVerdictInput[]): Promise<CaseFindingReviews> {
  const response = await authFetch(`/cases/${caseId}/finding-reviews/verdicts`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      verdicts: verdicts.map((review) => ({
        scope: review.scope,
        document_id: review.documentId,
        fingerprint: review.fingerprint,
        verdict: review.verdict,
        // The reason the officer gave for the agents getting it wrong: a false alarm (wrong), or a set-aside finding that is real.
        reason: review.verdict ? (review.reason ?? null) : null,
        note: review.verdict && review.reason === 'other' ? (review.note?.trim() ?? null) : null,
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

// The review of a group with no findings to mark, signed by the logged-in officer.
export async function signOffGroup(
  caseId: string,
  scope: ReviewScope,
  documentId: string | null,
): Promise<CaseFindingReviews> {
  const response = await authFetch(`/cases/${caseId}/finding-reviews/sign-offs`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scope, document_id: documentId }),
  });

  await assertOk(response, 'Failed to save the review');

  return (await response.json()) as CaseFindingReviews;
}

export async function deleteFindingReview(caseId: string, reviewId: string): Promise<CaseFindingReviews> {
  const response = await authFetch(`/cases/${caseId}/finding-reviews/${reviewId}`, { method: 'DELETE' });

  await assertOk(response, 'Failed to remove the review');

  return (await response.json()) as CaseFindingReviews;
}

// The dashboard's filters for the metrics: cases created from `since` to `until` (local YYYY-MM-DD days,
// inclusive), in these statuses.
export interface DetectionMetricsFilters {
  since?: string | null;
  until?: string | null;
  statuses?: CaseStage[];
}

// The start of a local day as a UTC instant, so the backend counts a case on the officer's day, as the dashboard's
// case counts do, not on UTC's.
const startOfDay = (day: string) => parseDay(day).toISOString();

// Precision and recall per check across every reviewed case matching the filters (the dashboard).
export async function getDetectionMetrics(filters: DetectionMetricsFilters = {}): Promise<DetectionMetrics> {
  const params = new URLSearchParams();
  if (filters.since) params.set('since', startOfDay(filters.since));
  // Up to the start of the next day, so all of the last one counts.
  if (filters.until) params.set('before', startOfDay(addDays(filters.until, 1)));
  for (const status of filters.statuses ?? []) params.append('status', status);
  const query = params.toString();
  const response = await authFetch(`/detection-metrics${query ? `?${query}` : ''}`);

  await assertOk(response, 'Failed to load the detection metrics');

  return (await response.json()) as DetectionMetrics;
}

// The feedback items an agent applied to a finding, opened: the officer's reason and note (confidential details already
// removed) and the case each came from. One a later officer withdrew is no longer listed.
export async function getFindingFeedback(ids: string[]): Promise<FeedbackDetail[]> {
  if (ids.length === 0) return [];
  const query = new URLSearchParams();
  for (const id of ids) query.append('ids', id);
  const response = await authFetch(`/finding-feedback?${query.toString()}`);

  await assertOk(response, 'Failed to load the feedback');

  return (await response.json()) as FeedbackDetail[];
}
