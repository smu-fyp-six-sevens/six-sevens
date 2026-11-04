import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { getCaseByNumber } from '../services/uploadService';
import { toCaseRecord, type CaseRecord } from '../utils/caseRecord';

type Lookup =
  | { status: 'loading' }
  | { status: 'notFound' }
  | { status: 'error'; message: string }
  | { status: 'ready'; caseRecord: CaseRecord };

// What a page that already holds the case (the queue, the header search) sends along when it navigates here.
export interface CaseHandover {
  caseRecord?: CaseRecord;
}

// The handed-over case, if it is the one the URL names.
function handedOver(state: unknown, caseNumber: string | undefined): CaseRecord | undefined {
  const record = (state as CaseHandover | null)?.caseRecord;

  return record && (record.caseNumber ?? record.id) === caseNumber ? record : undefined;
}

// The case named in the URL (/cases/GLDB-137). When the navigation brought the case along it is ready at once and
// the server is only asked to confirm it; otherwise (a pasted link, a refresh) the page waits for the lookup.
export function useCaseByNumber(caseNumber: string | undefined) {
  const location = useLocation();
  const brought = handedOver(location.state, caseNumber);
  const broughtRef = useRef(brought);
  broughtRef.current = brought;

  const [lookup, setLookup] = useState<Lookup>(brought ? { status: 'ready', caseRecord: brought } : { status: 'loading' });
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (!caseNumber) {
      setLookup({ status: 'notFound' });
      return;
    }
    const controller = new AbortController();
    const early = broughtRef.current;
    setLookup(early ? { status: 'ready', caseRecord: early } : { status: 'loading' });

    getCaseByNumber(caseNumber, controller.signal)
      .then((item) => setLookup(item ? { status: 'ready', caseRecord: toCaseRecord(item) } : { status: 'notFound' }))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        // The handed-over case stays on screen if confirming it failed; the page's own requests report problems.
        if (early) return;
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
