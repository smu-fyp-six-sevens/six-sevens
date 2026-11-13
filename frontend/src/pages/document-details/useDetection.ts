import { useEffect, useRef, useState } from 'react';
import {
  cancelDetection,
  getDetectionReport,
  retryDetection,
  runIndividualDetectionCheck,
} from '../../services/uploadService';
import type { DetectionReport, IndividualCheckId } from '../../services/uploadService';

// Detection Agent state for one document: the stored report, cancel/retry, and individual checks.
// `status` is the document's live status: a cancelled or crashed run is only visible in the stored error,
// so the report is read again whenever the document leaves 'detecting'.
// `onRunStarted` runs once a rerun is accepted: a detected document's page isn't listening for updates until then.
export function useDetection(documentId: string, status: string | undefined, onRunStarted?: () => void) {
  // The stored report for this document.
  const [detectionReport, setDetectionReport] = useState<DetectionReport | null>(null);
  // Set when a run crashed or was cancelled.
  const [detectionError, setDetectionError] = useState<string | null>(null);
  const [isRefreshingDetection, setIsRefreshingDetection] = useState(false);
  const [isCancellingDetection, setIsCancellingDetection] = useState(false);
  // The cancel was accepted; the worker stops at its next step, and the document returns to approved a moment later.
  const [isCancelPending, setIsCancelPending] = useState(false);
  // The document's status as of now, for an answer that lands after the run it cancels has already stopped.
  const latestStatus = useRef(status);
  latestStatus.current = status;
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [isRetryingDetection, setIsRetryingDetection] = useState(false);
  // Individual checks: re-run one check, only once a full detection report exists.
  const [runningCheckId, setRunningCheckId] = useState<IndividualCheckId | null>(null);
  const [individualCheckError, setIndividualCheckError] = useState<string | null>(null);
  const [individualCheckResults, setIndividualCheckResults] = useState<
    Partial<Record<IndividualCheckId, unknown>>
  >({});

  const refreshDetectionReport = async () => {
    setIsRefreshingDetection(true);
    try {
      const report = await getDetectionReport(documentId);
      setDetectionReport(report);
      setDetectionError(null);
    } catch (err) {
      setDetectionError(err instanceof Error ? err.message : 'Detection failed.');
    } finally {
      setIsRefreshingDetection(false);
    }
  };

  const handleCancelDetection = async () => {
    setIsCancellingDetection(true);
    setCancelError(null);
    try {
      // A run still waiting for a worker stops at once, and the document stays approved: read its error now.
      if ((await cancelDetection(documentId)) === 'cancelled') await refreshDetectionReport();
      // Only while that run is still the current one: a late answer would otherwise outlive it and block the next.
      else if (latestStatus.current === 'detecting') setIsCancelPending(true);
    } catch (err) {
      // Not a failed run: the cancel itself didn't go through, so the panel keeps showing what it showed.
      setCancelError(err instanceof Error ? err.message : 'Failed to cancel detection.');
      await refreshDetectionReport();
    } finally {
      setIsCancellingDetection(false);
    }
  };

  const handleRetryDetection = async () => {
    setIsRetryingDetection(true);
    try {
      await retryDetection(documentId);
      onRunStarted?.();
      await refreshDetectionReport();
    } catch (err) {
      setDetectionError(err instanceof Error ? err.message : 'Failed to retry detection.');
    } finally {
      setIsRetryingDetection(false);
    }
  };

  const handleRunIndividualCheck = async (checkId: IndividualCheckId) => {
    setRunningCheckId(checkId);
    setIndividualCheckError(null);
    try {
      const result = await runIndividualDetectionCheck(documentId, checkId);
      setIndividualCheckResults((prev) => ({ ...prev, [checkId]: result }));
    } catch (err) {
      setIndividualCheckError(err instanceof Error ? err.message : 'Check failed.');
    } finally {
      setRunningCheckId(null);
    }
  };

  // A run ended (cancelled or crashed): its error, or its report, is only in storage until it is read again.
  const previousStatus = useRef(status);
  useEffect(() => {
    const before = previousStatus.current;
    previousStatus.current = status;

    if (before === status) return;
    if (status !== 'detecting') setIsCancelPending(false);
    // A new run started: the last one's error no longer describes it.
    if (status === 'detecting') setDetectionError(null);
    // From approved too: a quick run can go from approved to detected between two updates, never showing detecting.
    if ((before === 'detecting' && (status === 'approved' || status === 'detected')) || (before === 'approved' && status === 'detected')) {
      void refreshDetectionReport();
    }
    // refreshDetectionReport only reads the document id.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  useEffect(() => {
    let cancelled = false;
    getDetectionReport(documentId)
      .then((report) => {
        if (!cancelled) setDetectionReport(report);
      })
      .catch((err) => {
        if (!cancelled) {
          setDetectionError(err instanceof Error ? err.message : 'Detection failed.');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  return {
    detectionReport,
    detectionError,
    isRefreshingDetection,
    refreshDetectionReport,
    isCancellingDetection: isCancellingDetection || isCancelPending,
    cancelDetectionError: cancelError,
    handleCancelDetection,
    isRetryingDetection,
    handleRetryDetection,
    runningCheckId,
    individualCheckError,
    individualCheckResults,
    handleRunIndividualCheck,
  };
}
