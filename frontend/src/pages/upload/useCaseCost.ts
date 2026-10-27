import { useCallback, useEffect, useState } from 'react';
import { getCaseCost, type CaseCost } from '../../services/uploadService';

// A case's recorded Claude cost and run timings (GET /audit/cases/{id}/cost), read when the caller mounts.
export function useCaseCost(caseId: string) {
  const [cost, setCost] = useState<CaseCost | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setCost(await getCaseCost(caseId));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load the cost');
    } finally {
      setLoading(false);
    }
  }, [caseId]);

  useEffect(() => {
    void load();
  }, [load]);

  return { cost, error, loading, reload: load };
}
