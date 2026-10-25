import { useCallback, useEffect, useState } from 'react';
import type { CaseRecord } from '../App';
import { getCaseByNumber } from '../services/uploadService';
import { toCaseRecord } from '../utils/caseRecord';

type Lookup =
  | { status: 'loading' }
  | { status: 'notFound' }
  | { status: 'error'; message: string }
  | { status: 'ready'; caseRecord: CaseRecord };

// The case named in the URL (/cases/GLDB-137), fetched on its own: the app no longer holds every case.
export function useCaseByNumber(caseNumber: string | undefined) {
  const [lookup, setLookup] = useState<Lookup>({ status: 'loading' });
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (!caseNumber) {
      setLookup({ status: 'notFound' });
      return;
    }
    const controller = new AbortController();
    setLookup({ status: 'loading' });

    getCaseByNumber(caseNumber, controller.signal)
      .then((item) => setLookup(item ? { status: 'ready', caseRecord: toCaseRecord(item) } : { status: 'notFound' }))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setLookup({ status: 'error', message: err instanceof Error ? err.message : 'Failed to load the case.' });
      });

    return () => controller.abort();
  }, [caseNumber, version]);

  const retry = useCallback(() => setVersion((current) => current + 1), []);

  // A rename made on the case page, shown in its header without another request.
  const rename = useCallback((applicant: string) => {
    setLookup((current) => (current.status === 'ready' ? { ...current, caseRecord: { ...current.caseRecord, applicant } } : current));
  }, []);

  return { lookup, retry, rename };
}
