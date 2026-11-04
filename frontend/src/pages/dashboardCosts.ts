import { useEffect, useState } from 'react';
import { getCaseCosts } from '../services/uploadService';

// Claude cost helpers shared by the dashboard's Overview and Cases panels.

export const usd = (value: number) => `$${value.toFixed(value < 1 ? 3 : 2)}`;

export function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

// Claude spend per live case (case id to USD), from the audit trail; null until loaded. A case with no costed run is
// absent. Read again whenever the set of cases changes.
export function useCaseCosts(caseIds: string[]) {
  const [costs, setCosts] = useState<Record<string, number> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = caseIds.join(',');

  useEffect(() => {
    if (!key) return;
    let cancelled = false;
    setError(null);
    getCaseCosts(key.split(','))
      .then((loaded) => {
        if (!cancelled) setCosts(loaded);
      })
      .catch((err: unknown) => {
        // The panels say only that the costs didn't load (a missing route reads as "no longer exists" otherwise).
        console.error('Failed to load the case costs:', err);
        if (!cancelled) setError("Couldn't load the costs. Try refreshing.");
      });
    return () => {
      cancelled = true;
    };
  }, [key]);

  return { costs, error };
}
