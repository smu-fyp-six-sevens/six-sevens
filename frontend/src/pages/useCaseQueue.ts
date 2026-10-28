import { useCallback, useEffect, useState } from 'react';
import type { CaseRecord } from '../App';
import { getCaseCounts, listCasesPage } from '../services/uploadService';
import type { CaseCounts, CaseStage, CaseRiskFilter, CaseSort } from '../services/uploadService';
import { toCaseRecord } from '../utils/caseRecord';

export interface CaseQueueQuery {
  q: string;
  sort: CaseSort;
  statuses: CaseStage[];
  risks: CaseRiskFilter[];
  page: number;
  pageSize: number;
}

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError';
}

// The queue's data, a page at a time from the server. While the next page loads the current rows stay on
// screen (isFetching), so the table doesn't flash empty. The sidebar's counts are their own, cheaper request.
export function useCaseQueue({ q, sort, statuses, risks, page, pageSize }: CaseQueueQuery) {
  const [items, setItems] = useState<CaseRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [isFetching, setIsFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [counts, setCounts] = useState<CaseCounts | null>(null);
  const [version, setVersion] = useState(0);
  const [countsVersion, setCountsVersion] = useState(0);
  const statusKey = statuses.join(',');
  const riskKey = risks.join(',');

  useEffect(() => {
    const controller = new AbortController();
    setIsFetching(true);
    setError(null);

    listCasesPage(
      {
        limit: pageSize,
        offset: (page - 1) * pageSize,
        sort,
        statuses: statusKey ? (statusKey.split(',') as CaseStage[]) : [],
        risks: riskKey ? (riskKey.split(',') as CaseRiskFilter[]) : [],
        q,
      },
      controller.signal,
    )
      .then((result) => {
        setItems(result.items.map(toCaseRecord));
        setTotal(result.total);
        setHasLoaded(true);
        setIsFetching(false);
      })
      .catch((err: unknown) => {
        if (isAbort(err)) return;
        setError(err instanceof Error ? err.message : 'Failed to load cases.');
        setIsFetching(false);
      });

    return () => controller.abort();
  }, [q, sort, statusKey, riskKey, page, pageSize, version]);

  useEffect(() => {
    const controller = new AbortController();

    getCaseCounts(controller.signal)
      .then(setCounts)
      // The sidebar just goes without its counts; the list has its own error.
      .catch(() => undefined);

    return () => controller.abort();
  }, [countsVersion]);

  const reload = useCallback(() => {
    setVersion((current) => current + 1);
    setCountsVersion((current) => current + 1);
  }, []);

  // A rename made in a row, shown at once without asking the server for the page again.
  const patchItem = useCallback((caseId: string, patch: Partial<CaseRecord>) => {
    setItems((current) => current.map((item) => (item.id === caseId ? { ...item, ...patch } : item)));
  }, []);

  return { items, total, hasLoaded, isFetching, error, counts, reload, patchItem };
}
