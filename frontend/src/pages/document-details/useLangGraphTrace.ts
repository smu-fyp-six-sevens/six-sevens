import { useEffect, useState } from 'react';
import { getLangGraphTrace } from '../../services/uploadService';
import type { LangGraphTrace } from '../../services/uploadService';

// Debug aid, see backend/app/langgraph/trace.py.
export function useLangGraphTrace(documentId: string) {
  const [langGraphTrace, setLangGraphTrace] = useState<LangGraphTrace | null>(null);
  const [isRefreshingTrace, setIsRefreshingTrace] = useState(false);

  const refreshLangGraphTrace = async () => {
    setIsRefreshingTrace(true);
    try {
      const result = await getLangGraphTrace(documentId);
      setLangGraphTrace(result);
    } catch {
      // Dev-only aid — a fetch failure here shouldn't disturb the page.
    } finally {
      setIsRefreshingTrace(false);
    }
  };

  // Fetched once on load.
  useEffect(() => {
    let cancelled = false;
    getLangGraphTrace(documentId)
      .then((result) => {
        if (!cancelled) setLangGraphTrace(result);
      })
      .catch(() => {
        // Dev-only aid — a fetch failure here shouldn't disturb the page.
      });
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  return { langGraphTrace, isRefreshingTrace, refreshLangGraphTrace };
}
