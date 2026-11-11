import { useEffect, useState } from 'react';
import { getDetectionReport } from '../../services/uploadService';
import { sharedRead } from '../../lib/sharedRead';

// What a finished scan found, for a document's status pill: 'clean' with no findings, 'findings' with at least one,
// null while it loads, when it can't be read, or before the scan has finished.
export type DetectionOutcome = 'clean' | 'findings' | null;

// Read only once the scan has finished ('detected'): the report doesn't change after that until a re-run, which moves
// the document back to scanning first. One read per document, shared with any other reader of it.
export function useDetectionOutcome(documentId: string, status: string): DetectionOutcome {
  const [outcome, setOutcome] = useState<DetectionOutcome>(null);

  useEffect(() => {
    if (status !== 'detected') {
      setOutcome(null);
      return;
    }
    let cancelled = false;
    sharedRead(`detection-report:${documentId}`, () => getDetectionReport(documentId))
      .then((report) => {
        if (!cancelled) setOutcome(report ? (report.findings.length > 0 ? 'findings' : 'clean') : null);
      })
      .catch(() => {
        if (!cancelled) setOutcome(null);
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, status]);

  return outcome;
}
