import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { DetectionReport } from '../../services/uploadService';
import { useDetection } from './useDetection';

// The detection API, replaced: each call answers as the test sets it.
const api = vi.hoisted(() => ({
  getDetectionReport: vi.fn(),
  cancelDetection: vi.fn(),
  retryDetection: vi.fn(),
  runIndividualDetectionCheck: vi.fn(),
}));

vi.mock('../../services/uploadService', () => api);

const REPORT = { verdict: 'flagged', summary: 'Net pay restyled.', findings: [] } as unknown as DetectionReport;

function detection(status: string, onRunStarted?: () => void) {
  return renderHook(({ status }) => useDetection('doc-1', status, onRunStarted), { initialProps: { status } });
}

describe('useDetection', () => {
  beforeEach(() => {
    api.getDetectionReport.mockResolvedValue(REPORT);
    api.retryDetection.mockResolvedValue(undefined);
  });

  it('reads the stored report when the document opens', async () => {
    const { result } = detection('detected');

    await waitFor(() => expect(result.current.detectionReport).toEqual(REPORT));
  });

  it('shows the run as cancelling until the worker stops it, then reads why it ended', async () => {
    api.cancelDetection.mockResolvedValue('cancelling');
    const { result, rerender } = detection('detecting');

    await act(() => result.current.handleCancelDetection());
    expect(result.current.isCancellingDetection).toBe(true);
    api.getDetectionReport.mockClear();
    api.getDetectionReport.mockRejectedValue(new Error('Cancelled by loan officer'));

    rerender({ status: 'approved' });

    expect(result.current.isCancellingDetection).toBe(false);
    await waitFor(() => expect(result.current.detectionError).toBe('Cancelled by loan officer'));
    expect(api.getDetectionReport).toHaveBeenCalledOnce();
  });

  it('a run still waiting for a worker is cancelled at once and its error read', async () => {
    api.cancelDetection.mockResolvedValue('cancelled');
    const { result } = detection('detecting');
    api.getDetectionReport.mockClear();

    await act(() => result.current.handleCancelDetection());

    expect(result.current.isCancellingDetection).toBe(false);
    expect(api.getDetectionReport).toHaveBeenCalledOnce();
  });

  it('a cancel that fails says so and leaves the run as it was', async () => {
    api.cancelDetection.mockRejectedValue(new Error('Detection has already finished.'));
    const { result } = detection('detecting');

    await act(() => result.current.handleCancelDetection());

    expect(result.current.cancelDetectionError).toBe('Detection has already finished.');
    expect(result.current.isCancellingDetection).toBe(false);
  });

  it('retrying starts the run, tells the page, and clears the last error once it is running', async () => {
    api.getDetectionReport.mockRejectedValue(new Error('Cancelled by loan officer'));
    const onRunStarted = vi.fn();
    const { result, rerender } = detection('approved', onRunStarted);
    await waitFor(() => expect(result.current.detectionError).toBe('Cancelled by loan officer'));
    api.getDetectionReport.mockResolvedValue(null);

    await act(() => result.current.handleRetryDetection());
    rerender({ status: 'detecting' });

    expect(api.retryDetection).toHaveBeenCalledWith('doc-1');
    expect(onRunStarted).toHaveBeenCalledOnce();
    expect(result.current.detectionError).toBeNull();
  });

  it('keeps each individual check’s result, and a failed check’s error', async () => {
    api.runIndividualDetectionCheck.mockResolvedValueOnce({ findings: 1 }).mockRejectedValueOnce(new Error('Check failed: timeout'));
    const { result } = detection('detected');

    await act(() => result.current.handleRunIndividualCheck('font-consistency'));
    await act(() => result.current.handleRunIndividualCheck('spelling-anomaly'));

    expect(result.current.individualCheckResults).toEqual({ 'font-consistency': { findings: 1 } });
    expect(result.current.individualCheckError).toBe('Check failed: timeout');
    expect(result.current.runningCheckId).toBeNull();
  });

  // REG-014 (Sprint 4 regression report): the panel is left on "Cancelling…" when the worker's snapshot (the run
  // stopped) reaches the page before the cancel request answers. The flag is cleared when the document leaves
  // detecting and set again when the answer lands, and nothing clears it after that, so a retried run shows a
  // disabled "Cancelling…". This test reproduces it; remove `.fails` once useDetection is fixed.
  it.fails('a retried run can be cancelled even when the cancel answered after the run had stopped (REG-014)', async () => {
    let answer!: (outcome: 'cancelling') => void;
    api.cancelDetection.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const { result, rerender } = detection('detecting');

    let cancelling!: Promise<void>;
    act(() => {
      cancelling = result.current.handleCancelDetection();
    });
    rerender({ status: 'approved' }); // the worker stopped the run: its snapshot arrives first
    await act(async () => {
      answer('cancelling'); // then the cancel request answers
      await cancelling;
    });
    rerender({ status: 'detecting' }); // the officer retried

    expect(result.current.isCancellingDetection).toBe(false);
  });
});
