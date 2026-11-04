import { useCallback, useEffect, useState } from 'react';
import type { CaseFindingReviews, FalseAlarmReason, FindingReview, ReviewScope, ReviewVerdict } from '../../services/uploadService';
import {
  deleteFindingReview,
  getCaseFindingReviews,
  reportMissedIssue,
  setFindingVerdicts,
  signOffGroup,
  updateMissedIssue,
} from '../../services/uploadService';
import { remember, sharedRead } from '../../lib/sharedRead';

const reviewsKey = (caseId: string) => `finding-reviews:${caseId}`;

// Anything an officer gives a verdict on: a scored finding, or one the specialists dismissed (no source: a
// document's own).
export interface ReviewTarget {
  document_id: string;
  fingerprint: string;
  source?: string | null;
}

export function findingScope(finding: ReviewTarget): ReviewScope {
  return finding.source === 'cross_document' ? 'case' : 'document';
}

// The document a finding's review sits on: none for a cross-document finding.
function reviewDocument(finding: ReviewTarget): string | null {
  return findingScope(finding) === 'case' ? null : finding.document_id;
}

// The officer's verdict on this finding: the same document and fingerprint. A rerun replaces the findings and
// their verdicts, so a verdict only ever matches a finding of the run it was given on.
export function verdictFor(reviews: FindingReview[], finding: ReviewTarget): FindingReview | undefined {
  const documentId = reviewDocument(finding);
  return reviews.find(
    (review) =>
      review.kind === 'verdict' && review.fingerprint === finding.fingerprint && (review.document_id ?? null) === documentId,
  );
}

// The case's reviews and metrics, and the actions that change them. Every change returns them afresh. The overview
// and the risk summary both show them: one read serves both (sharedRead).
export function useFindingReviews(caseId: string, enabled: boolean) {
  const [data, setData] = useState<CaseFindingReviews | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savingKey, setSavingKey] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    sharedRead(reviewsKey(caseId), () => getCaseFindingReviews(caseId))
      .then((loaded) => {
        if (!cancelled) setData(loaded);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load the finding reviews');
      });
    return () => {
      cancelled = true;
    };
  }, [caseId, enabled]);

  // Resolves true once saved; a failure is shown through `error` instead.
  const run = useCallback(async (key: string, action: () => Promise<CaseFindingReviews>) => {
    setSavingKey(key);
    setError(null);
    try {
      const saved = await action();
      remember(reviewsKey(caseId), saved);
      setData(saved);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save the review');
      return false;
    } finally {
      setSavingKey(null);
    }
  }, [caseId]);

  // One group's review: each row has the verdict the officer's tick gives it, and a row can stand for several identical
  // findings (same check and summary), so its verdict goes on each of them. One request for the whole group.
  const saveGroup = useCallback(
    (key: string, rows: { members: ReviewTarget[]; verdict: ReviewVerdict; reason?: FalseAlarmReason | null; note?: string | null }[]) =>
      run(key, () =>
        setFindingVerdicts(
          caseId,
          rows.flatMap(({ members, verdict, reason, note }) =>
            members.map((finding) => ({
              scope: findingScope(finding),
              documentId: reviewDocument(finding),
              fingerprint: finding.fingerprint,
              verdict,
              reason,
              note,
            })),
          ),
        ),
      ),
    [caseId, run],
  );

  const signOff = useCallback(
    (key: string, scope: ReviewScope, documentId: string | null) => run(key, () => signOffGroup(caseId, scope, documentId)),
    [caseId, run],
  );

  const addMissed = useCallback(
    (key: string, scope: ReviewScope, documentId: string | null, checkName: string, note: string) =>
      run(key, () => reportMissedIssue(caseId, { scope, documentId, checkName, note })),
    [caseId, run],
  );

  const editMissed = useCallback(
    (reviewId: string, checkName: string, note: string) =>
      run(reviewId, () => updateMissedIssue(caseId, reviewId, { checkName, note })),
    [caseId, run],
  );

  const removeReview = useCallback(
    (reviewId: string) => run(reviewId, () => deleteFindingReview(caseId, reviewId)),
    [caseId, run],
  );

  return { data, error, savingKey, saveGroup, signOff, addMissed, editMissed, removeReview };
}
