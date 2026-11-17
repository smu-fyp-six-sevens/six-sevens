import { useEffect, useState } from 'react';
import { getDetectionReport, type DetectionSeverity } from '../../services/uploadService';
import { sharedRead } from '../../lib/sharedRead';

// A finished scan's findings by severity (all zero: nothing found).
export type DetectionCounts = Record<DetectionSeverity, number>;

type CountedDocument = { id: string; status: string; detection_counts?: DetectionCounts | null };

// Each scanned document's counts. The case snapshot carries them, so they show with the list; only a report stored
// before the counts were (no detection_counts) is read here, once, shared with any other reader of it. A document
// missing from the map is still loading, unreadable, or not scanned.
export function useDetectionCounts(documents: CountedDocument[]): Map<string, DetectionCounts> {
  const scanned = documents.filter((document) => document.status === 'detected');
  const fromSnapshot = new Map(
    scanned.flatMap((document) => (document.detection_counts ? [[document.id, document.detection_counts] as const] : [])),
  );
  const missingKey = scanned.filter((document) => !document.detection_counts).map((document) => document.id).join(',');
  const [fetched, setFetched] = useState<Map<string, DetectionCounts>>(new Map());

  useEffect(() => {
    let cancelled = false;
    const ids = missingKey ? missingKey.split(',') : [];
    if (ids.length === 0) {
      setFetched(new Map());
      return;
    }
    void Promise.all(
      ids.map((id) =>
        sharedRead(`detection-report:${id}`, () => getDetectionReport(id))
          .then((report) => [id, report ? countBySeverity(report.findings) : null] as const)
          .catch(() => [id, null] as const),
      ),
    ).then((entries) => {
      if (cancelled) return;
      const next = new Map<string, DetectionCounts>();
      entries.forEach(([id, value]) => value && next.set(id, value));
      setFetched(next);
    });
    return () => {
      cancelled = true;
    };
  }, [missingKey]);

  return new Map([...fetched, ...fromSnapshot]);
}

function countBySeverity(findings: { severity: DetectionSeverity }[]): DetectionCounts {
  const counts: DetectionCounts = { high: 0, medium: 0, low: 0, info: 0 };
  findings.forEach((finding) => {
    if (finding.severity in counts) counts[finding.severity] += 1;
  });
  return counts;
}
