import { useEffect, useState } from 'react';
import type { CaseRecord } from '../App';
import { searchCases } from '../services/uploadService';
import { toCaseRecord } from '../utils/caseRecord';

// The newest cases, for the dashboard's links (it is still mostly sample data).
export function useRecentCases(limit: number) {
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();

    searchCases('', limit, controller.signal)
      .then((result) => setCases(result.items.map(toCaseRecord)))
      .catch(() => undefined)
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });

    return () => controller.abort();
  }, [limit]);

  return { cases, isLoading };
}
