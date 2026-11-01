import { useCallback, useEffect, useState } from 'react';
import { getCaseCost, type CaseCost } from '../../services/uploadService';
import { forget, sharedRead } from '../../lib/sharedRead';

const costKey = (caseId: string) => `case-cost:${caseId}`;

// A case's recorded Claude cost and run timings (GET /audit/cases/{id}/cost), read when the caller mounts. The
// overview and the cost section both show it: one read serves both (sharedRead); reload always reads again.
export function useCaseCost(caseId: string) {
  const [cost, setCost] = useState<CaseCost | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const read = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setCost(await sharedRead(costKey(caseId), () => getCaseCost(caseId)));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load the cost');
    } finally {
      setLoading(false);
    }
  }, [caseId]);

  const reload = useCallback(() => {
    forget(costKey(caseId));
    return read();
  }, [caseId, read]);

  useEffect(() => {
    void read();
  }, [read]);

  return { cost, error, loading, reload };
}
