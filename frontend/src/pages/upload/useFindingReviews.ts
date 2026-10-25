import { useCallback, useEffect, useState } from 'react';
import type { CaseFindingReviews, CaseRiskFinding, FindingReview, ReviewScope, ReviewVerdict } from '../../services/uploadService';
import {
  deleteFindingReview,
  getCaseFindingReviews,
  reportMissedIssue,
  setFindingVerdicts,
} from '../../services/uploadService';

export function findingScope(finding: CaseRiskFinding): ReviewScope {
  return finding.source === 'cross_document' ? 'case' : 'document';
}

// A cross-document finding is filed under its category; a document finding under its tool.
export function findingCheck(finding: CaseRiskFinding): string {
  return findingScope(finding) === 'case' ? finding.category ?? 'cross_document_variance' : finding.tool ?? 'unknown';
}

// The officer's verdict on this finding. A verdict only counts against the same finding it was given on (same
// id and summary): a rerun renumbers findings, so an old verdict never lands on a different one.
export function verdictFor(reviews: FindingReview[], finding: CaseRiskFinding): FindingReview | undefined {
  return reviews.find(
    (review) => review.kind === 'verdict' && review.finding_id === finding.finding_id && review.summary === finding.summary,
  );
}

// The case's reviews and metrics, and the actions that change them. Every change returns them afresh.
export function useFindingReviews(caseId: string, enabled: boolean) {
  const [data, setData] = useState<CaseFindingReviews | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savingKey, setSavingKey] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    getCaseFindingReviews(caseId)
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

  const run = useCallback(async (key: string, action: () => Promise<CaseFindingReviews>) => {
    setSavingKey(key);
    setError(null);
    try {
      setData(await action());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save the review');
    } finally {
      setSavingKey(null);
    }
  }, []);

  // One group's review: each row is correct (ticked) or wrong (not), and a row can stand for several identical
  // findings (same check and summary), so its verdict goes on each of them. One request for the whole group.
  const saveGroup = useCallback(
    (key: string, rows: { members: CaseRiskFinding[]; verdict: ReviewVerdict }[]) =>
      run(key, () =>
        setFindingVerdicts(
          caseId,
          rows.flatMap(({ members, verdict }) =>
            members.map((finding) => {
              const scope = findingScope(finding);
              return {
                scope,
                documentId: scope === 'document' ? finding.document_id : null,
                findingId: finding.finding_id,
                checkName: findingCheck(finding),
                checkCode: finding.check_code ?? null,
                summary: finding.summary,
                verdict,
              };
            }),
          ),
        ),
      ),
    [caseId, run],
  );

  const addMissed = useCallback(
    (key: string, scope: ReviewScope, documentId: string | null, checkName: string, note: string) =>
      run(key, () => reportMissedIssue(caseId, { scope, documentId, checkName, note })),
    [caseId, run],
  );

  const removeReview = useCallback(
    (reviewId: string) => run(reviewId, () => deleteFindingReview(caseId, reviewId)),
    [caseId, run],
  );

  return { data, error, savingKey, saveGroup, addMissed, removeReview };
}
