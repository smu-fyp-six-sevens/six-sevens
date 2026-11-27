import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { DetectionReport } from '../../services/uploadService';
import { useDetectionCounts } from './useDetectionCounts';

const api = vi.hoisted(() => ({ getDetectionReport: vi.fn() }));

vi.mock('../../services/uploadService', () => api);

const SNAPSHOT = { high: 1, medium: 0, low: 2, info: 0 };

describe('useDetectionCounts', () => {
  beforeEach(() => {
    api.getDetectionReport.mockReset();
  });

  it('shows the snapshot counts at once, without reading a report', () => {
    const { result } = renderHook(() => useDetectionCounts([{ id: 'a', status: 'detected', detection_counts: SNAPSHOT }]));

    expect(result.current.get('a')).toEqual(SNAPSHOT);
    expect(api.getDetectionReport).not.toHaveBeenCalled();
  });

  it('reads a report only for a detected document that has no counts, and counts its findings', async () => {
    api.getDetectionReport.mockResolvedValue({
      findings: [{ severity: 'high' }, { severity: 'low' }, { severity: 'low' }],
    } as unknown as DetectionReport);
    const { result } = renderHook(() =>
      useDetectionCounts([
        { id: 'old', status: 'detected' },
        { id: 'counted', status: 'detected', detection_counts: SNAPSHOT },
        { id: 'pending', status: 'detecting' },
        { id: 'approved', status: 'approved', detection_counts: null },
      ]),
    );

    await waitFor(() => expect(result.current.get('old')).toEqual({ high: 1, medium: 0, low: 2, info: 0 }));
    expect(api.getDetectionReport).toHaveBeenCalledTimes(1);
    expect(api.getDetectionReport).toHaveBeenCalledWith('old');
    expect(result.current.has('pending')).toBe(false);
    expect(result.current.has('approved')).toBe(false);
  });

  it('leaves a document out when its report cannot be read', async () => {
    api.getDetectionReport.mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useDetectionCounts([{ id: 'bad', status: 'detected' }]));

    await waitFor(() => expect(api.getDetectionReport).toHaveBeenCalled());
    expect(result.current.has('bad')).toBe(false);
  });
});
