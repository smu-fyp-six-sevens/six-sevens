import type { CaseCostTiming } from '../../services/uploadService';

// How long each stage of a case took, from the finished runs in its audit trail.
export interface ProcessingTime {
  label: string;
  detail: string;
  value: string;
}

export const formatUsd = (value: number) => `$${value.toFixed(4)}`;

export function formatDuration(milliseconds: number): string {
  if (milliseconds < 1000) return `${Math.round(milliseconds)}ms`;

  const seconds = milliseconds / 1000;
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  if (seconds < 60) return `${Math.round(seconds)}s`;

  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${String(Math.round(seconds % 60)).padStart(2, '0')}s`;
}

const average = (values: number[]) => values.reduce((sum, value) => sum + value, 0) / values.length;

const STAGES: { label: string; type: string; plural: string }[] = [
  { label: 'Classify + extract', type: 'document.analyzed', plural: 'documents' },
  { label: 'Anomaly detection', type: 'document.detected', plural: 'documents' },
  { label: 'Case analysis', type: 'case.analysis.completed', plural: 'runs' },
];

// The case's automated time end to end: how long any of its runs had it in work (the union of their intervals, each
// ending when its event was recorded). Runs side by side count once; waits for an officer (approval, a later upload)
// don't count. The same rule as the dashboard's (backend audit.busy_ms). Null until a run has finished.
export function caseProcessingMs(timings: CaseCostTiming[] | undefined): number | null {
  const spans = (timings ?? [])
    .filter((timing) => STAGES.some((stage) => stage.type === timing.type))
    .map((timing) => {
      const end = new Date(timing.occurred_at).getTime();
      return [end - timing.duration_ms, end] as const;
    })
    .filter(([start, end]) => Number.isFinite(start) && Number.isFinite(end))
    .sort((a, b) => a[0] - b[0]);
  if (spans.length === 0) return null;
  let total = 0;
  let [start, stop] = spans[0];
  for (const [begin, end] of spans.slice(1)) {
    if (begin > stop) {
      total += stop - start;
      [start, stop] = [begin, end];
    } else {
      stop = Math.max(stop, end);
    }
  }
  return total + (stop - start);
}

// Per stage, the average of its runs (the newest run for the case analysis, which is one per case).
export function processingTimes(timings: CaseCostTiming[] | undefined): ProcessingTime[] {
  return STAGES.map(({ label, type, plural }) => {
    const durations = (timings ?? []).filter((timing) => timing.type === type).map((timing) => timing.duration_ms);

    if (durations.length === 0) return { label, detail: 'not run yet', value: '—' };
    if (type === 'case.analysis.completed') {
      return { label, detail: 'latest run', value: formatDuration(durations[durations.length - 1]) };
    }

    return { label, detail: `average of ${durations.length} ${durations.length === 1 ? plural.replace(/s$/, '') : plural}`, value: formatDuration(average(durations)) };
  });
}
