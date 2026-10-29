import { useEffect, useState } from 'react';
import type { CaseRecord } from '../App';
import { listCasesPage } from '../services/uploadService';
import { toCaseRecord } from '../utils/caseRecord';

// The case list's page size limit (routers/cases.py).
const PAGE_SIZE = 100;

// Every case as the case queue lists it (status, risk, timings, flags, document types), newest first, a page at a time.
export function useDashboardCases() {
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();

    (async () => {
      const loaded: CaseRecord[] = [];
      for (;;) {
        const page = await listCasesPage(
          { limit: PAGE_SIZE, offset: loaded.length, sort: 'newest', statuses: [], risks: [], q: '' },
          controller.signal,
        );
        loaded.push(...page.items.map(toCaseRecord));
        if (!page.items.length || loaded.length >= page.total) break;
      }
      setCases(loaded);
    })()
      .catch(() => undefined)
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });

    return () => controller.abort();
  }, []);

  return { cases, isLoading };
}
