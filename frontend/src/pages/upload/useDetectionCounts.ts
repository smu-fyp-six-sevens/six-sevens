import { useEffect, useState } from 'react';
import { getDetectionReport, type DetectionSeverity } from '../../services/uploadService';
import { sharedRead } from '../../lib/sharedRead';

// A finished scan's findings by severity (all zero: nothing found).
export type DetectionCounts = Record<DetectionSeverity, number>;

// Each scanned document's counts, read once its scan has finished ('detected'): the report doesn't change after that
// until a re-run, which moves the document back to scanning first. One read per document, shared with any other
// reader of it. A document missing from the map is still loading, unreadable, or not scanned.
export function useDetectionCounts(documents: { id: string; status: string }[]): Map<string, DetectionCounts> {
  const [counts, setCounts] = useState<Map<string, DetectionCounts>>(new Map());
  const scanned = documents.filter((document) => document.status === 'detected').map((document) => document.id);
  const key = scanned.join(',');

  useEffect(() => {
    let cancelled = false;
    const ids = key ? key.split(',') : [];
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
      setCounts(next);
    });
    return () => {
      cancelled = true;
    };
  }, [key]);

  return counts;
}

function countBySeverity(findings: { severity: DetectionSeverity }[]): DetectionCounts {
  const counts: DetectionCounts = { high: 0, medium: 0, low: 0, info: 0 };
  findings.forEach((finding) => {
    if (finding.severity in counts) counts[finding.severity] += 1;
  });
  return counts;
}
