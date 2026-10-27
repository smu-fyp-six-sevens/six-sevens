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
  { label: 'Fraud detection', type: 'document.detected', plural: 'documents' },
  { label: 'Case analysis', type: 'case.analysis.completed', plural: 'runs' },
];

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
