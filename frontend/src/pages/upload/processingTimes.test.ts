import { describe, expect, it } from 'vitest';
import type { CaseCostTiming } from '../../services/uploadService';
import { caseProcessingMs } from './processingTimes';

const T0 = Date.parse('2026-11-01T00:00:00Z');

// A run that ended `endSec` seconds after T0 and lasted `durationSec`.
const run = (endSec: number, durationSec: number, type = 'document.analyzed'): CaseCostTiming => ({
  document_id: 'doc-1',
  duration_ms: durationSec * 1000,
  occurred_at: new Date(T0 + endSec * 1000).toISOString(),
  type,
});

describe('caseProcessingMs', () => {
  it('is null with no timings', () => {
    expect(caseProcessingMs(undefined)).toBeNull();
    expect(caseProcessingMs([])).toBeNull();
  });

  it('one run is its own duration', () => {
    expect(caseProcessingMs([run(100, 30)])).toBe(30_000);
  });

  it('overlapping runs count once', () => {
    expect(caseProcessingMs([run(100, 40), run(120, 40)])).toBe(60_000);
  });

  it('touching runs join', () => {
    expect(caseProcessingMs([run(100, 40), run(140, 40)])).toBe(80_000);
  });

  it('a gap between runs is not counted', () => {
    expect(caseProcessingMs([run(100, 40), run(300, 40)])).toBe(80_000);
  });

  it('ignores timings of other event types', () => {
    expect(caseProcessingMs([run(100, 30), run(500, 200, 'document.uploaded')])).toBe(30_000);
    expect(caseProcessingMs([run(100, 30, 'document.uploaded')])).toBeNull();
  });
});
