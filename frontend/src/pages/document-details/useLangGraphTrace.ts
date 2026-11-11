import { useEffect, useState } from 'react';
import { getLangGraphTrace } from '../../services/uploadService';
import type { LangGraphTrace } from '../../services/uploadService';

// The agent trace, and the low-confidence reasons shown under each field (fieldReasons.ts). See backend/app/langgraph/trace.py.
export function useLangGraphTrace(documentId: string, documentStatus?: string) {
  const [langGraphTrace, setLangGraphTrace] = useState<LangGraphTrace | null>(null);
  const [isRefreshingTrace, setIsRefreshingTrace] = useState(false);

  const refreshLangGraphTrace = async () => {
    setIsRefreshingTrace(true);
    try {
      const result = await getLangGraphTrace(documentId);
      setLangGraphTrace(result);
    } catch {
      // Best-effort: a fetch failure here shouldn't disturb the page.
    } finally {
      setIsRefreshingTrace(false);
    }
  };

  // On load and when the status changes, so a run that finishes while the page is open brings its reasons.
  useEffect(() => {
    let cancelled = false;
    getLangGraphTrace(documentId)
      .then((result) => {
        if (!cancelled) setLangGraphTrace(result);
      })
      .catch(() => {
        // Best-effort: a fetch failure here shouldn't disturb the page.
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, documentStatus]);

  return { langGraphTrace, isRefreshingTrace, refreshLangGraphTrace };
}
