import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { addDays, formatPeriod, inPeriod, presetPeriod } from './dateRange';

// The dashboard's date filter: which cases a period keeps.
describe('dashboard periods', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 10, 5, 22, 30)); // 5 Nov 2026, late evening local time
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('counts a preset back from today, including today', () => {
    expect(presetPeriod('Last 7 days')).toEqual({ from: '2026-10-30', to: '2026-11-05' });
    expect(presetPeriod('Last 30 days')).toEqual({ from: '2026-10-07', to: '2026-11-05' });
    expect(presetPeriod('All time')).toEqual({ from: null, to: '2026-11-05' });
  });

  it('keeps a day on either end of the period and drops the days outside it', () => {
    const period = { from: '2026-10-30', to: '2026-11-05' };

    expect(['2026-10-29', '2026-10-30', '2026-11-05', '2026-11-06'].map((day) => inPeriod(day, period))).toEqual([
      false, true, true, false,
    ]);
    expect(inPeriod('2001-01-01', { from: null, to: '2026-11-05' })).toBe(true);
  });

  it('steps across a month end', () => {
    expect(addDays('2026-10-30', 3)).toBe('2026-11-02');
  });

  it('names a period without the year when it is this year, and with it otherwise', () => {
    expect(formatPeriod({ from: '2026-10-30', to: '2026-11-05' })).toBe('30 Oct – 5 Nov');
    expect(formatPeriod({ from: '2025-12-30', to: '2026-01-05' })).toBe('30 Dec 2025 – 5 Jan 2026');
    expect(formatPeriod({ from: null, to: '2026-11-05' })).toBe('Until 5 Nov');
    expect(formatPeriod({ from: '2026-11-05', to: '2026-11-05' })).toBe('5 Nov');
  });
});
