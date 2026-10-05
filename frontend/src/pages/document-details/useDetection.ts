import { useEffect, useState } from 'react';
import {
  cancelDetection,
  getDetectionReport,
  retryDetection,
  runIndividualDetectionCheck,
} from '../../services/uploadService';
import type { DetectionReport, IndividualCheckId } from '../../services/uploadService';

// Detection Agent state for one document: the stored report, cancel/retry, and individual checks.
export function useDetection(documentId: string) {
  // The stored report for this document.
  const [detectionReport, setDetectionReport] = useState<DetectionReport | null>(null);
  // Set when a run crashed or was cancelled.
  const [detectionError, setDetectionError] = useState<string | null>(null);
  const [isRefreshingDetection, setIsRefreshingDetection] = useState(false);
  const [isCancellingDetection, setIsCancellingDetection] = useState(false);
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
    try {
      await cancelDetection(documentId);
      await refreshDetectionReport();
    } catch (err) {
      setDetectionError(err instanceof Error ? err.message : 'Failed to cancel detection.');
    } finally {
      setIsCancellingDetection(false);
    }
  };

  const handleRetryDetection = async () => {
    setIsRetryingDetection(true);
    try {
      await retryDetection(documentId);
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
    isCancellingDetection,
    handleCancelDetection,
    isRetryingDetection,
    handleRetryDetection,
    runningCheckId,
    individualCheckError,
    individualCheckResults,
    handleRunIndividualCheck,
  };
}
