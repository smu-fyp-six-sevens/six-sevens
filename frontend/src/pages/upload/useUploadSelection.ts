import { useMemo, useState } from 'react';
import type { CaseStatusSubscription } from '../../services/caseSocket';
import { uploadDocuments } from '../../services/uploadService';
import type { BulkUploadItemResult } from '../../services/uploadService';
import { MAX_BULK_FILES, checkFileCount, recordUploadedHash, validateFiles } from '../../utils/fileValidation';
import type { FileIssue } from '../../utils/fileValidation';

interface UploadSelectionParams {
  caseId: string;
  caseSocketRef: { current: CaseStatusSubscription | null };
}

// Files picked for upload, the submit, and the per-file results.
export function useUploadSelection({ caseId, caseSocketRef }: UploadSelectionParams) {
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [countError, setCountError] = useState<string | null>(null);
  const [rejections, setRejections] = useState<{ file: File; issue: FileIssue }[]>([]);
  const [results, setResults] = useState<BulkUploadItemResult[]>([]);
  const [justSubmitted, setJustSubmitted] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const hasOutcome = results.length > 0 || rejections.length > 0;

  const resultByFilename = useMemo(
    () => new Map(results.map((result) => [result.filename, result])),
    [results],
  );

  const completedCount = results.length + rejections.length;
  const successfulCount = results.filter((result) => result.success).length;
  const failedCount =
    results.filter((result) => !result.success).length + rejections.length;

  function handleFiles(files: File[]) {
    setSelectedFiles((current) => {
      const combined = [...current, ...files];
      setCountError(checkFileCount(combined));
      return combined.slice(0, MAX_BULK_FILES);
    });

    setResults([]);
    setRejections([]);
    setError(null);
    setJustSubmitted(false);
  }

  async function handleSubmit() {
    if (!selectedFiles.length || countError) return;

    setIsSubmitting(true);
    setError(null);
    setResults([]);
    setRejections([]);
    setJustSubmitted(true);

    try {
      const { ok, rejected } = await validateFiles(selectedFiles, caseId);

      setRejections(rejected);

      if (ok.length === 0) return;

      const response = await uploadDocuments(
        caseId,
        ok.map((validated) => validated.uploadFile),
      );

      setResults(response.results);

      response.results.forEach((result, index) => {
        if (result.success) {
          recordUploadedHash(caseId, ok[index].hash);
        }
      });

      // Wakes the backend's poll loop now, instead of waiting out its idle interval.
      if (response.results.some((result) => result.success)) {
        caseSocketRef.current?.refresh();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed.');
    } finally {
      setIsSubmitting(false);
    }
  }

  function removeFile(index: number) {
    setSelectedFiles((files) => files.filter((_, i) => i !== index));
    setCountError(null);
    setResults([]);
    setRejections([]);
    setError(null);
    setJustSubmitted(false);
  }

  return {
    selectedFiles,
    countError,
    rejections,
    results,
    justSubmitted,
    isSubmitting,
    error,
    hasOutcome,
    resultByFilename,
    completedCount,
    successfulCount,
    failedCount,
    handleFiles,
    handleSubmit,
    removeFile,
  };
}
