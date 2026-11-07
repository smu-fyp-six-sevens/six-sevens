import { useCallback, useEffect, useRef, useState } from 'react';
import { listCasesPage } from '../services/uploadService';
import { toCaseRecord, type CaseRecord } from '../utils/caseRecord';

// The case list's page size limit (routers/cases.py).
const PAGE_SIZE = 100;

// Every case as the case queue lists it (status, risk, timings, flags, document types), newest first, a page at a time.
// No socket covers the whole case list (only one case's), so the dashboard reads it on opening and when the officer
// refreshes. `isLoading` is only the first load: a refresh swaps the figures in place. `error` is the last load's
// failure (a failed refresh keeps the figures already shown, with the error beside them).
export function useDashboardCases() {
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The load in progress, if any: one at a time, and the one to cancel when the page closes.
  const running = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    if (running.current) return;
    const controller = new AbortController();
    running.current = controller;
    setIsRefreshing(true);
    setError(null);
    try {
      const loaded: CaseRecord[] = [];
      for (;;) {
        const page = await listCasesPage(
          { limit: PAGE_SIZE, offset: loaded.length, sort: 'newest', statuses: [], risks: [], q: '' },
          controller.signal,
        );
        loaded.push(...page.items.map(toCaseRecord));
        if (!page.items.length || loaded.length >= page.total) break;
      }
      if (!controller.signal.aborted) setCases(loaded);
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Failed to load the cases.');
    } finally {
      if (running.current === controller) running.current = null;
      if (!controller.signal.aborted) {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    void load();
    // Cancelled loads are forgotten at once, so the next load isn't skipped as "already running" (React opens the
    // page twice in development, cancelling the first load).
    return () => {
      running.current?.abort();
      running.current = null;
    };
  }, [load]);

  return { cases, isLoading, isRefreshing, error, refresh: load };
}
