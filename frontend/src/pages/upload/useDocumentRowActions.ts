import { useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import {
  bulkApproveDocuments,
  cancelDocument,
  retryDocument,
} from '../../services/uploadService';
import type { DocumentCaseMatch, DocumentRecord } from '../../services/uploadService';
import { isEligibleForApproval } from './uploadHelpers';

interface DocumentRowActionsParams {
  setDocumentDetails: Dispatch<SetStateAction<Map<string, DocumentRecord>>>;
  isMountedRef: { current: boolean };
  selectedDocumentIds: Set<string>;
  setSelectedDocumentIds: Dispatch<SetStateAction<Set<string>>>;
  extractedDocuments: DocumentRecord[];
  caseMatches: Map<string, DocumentCaseMatch>;
}

// Per-row approve, cancel and retry, plus approve-selected. approveError is shared by single and bulk approve.
export function useDocumentRowActions({
  setDocumentDetails,
  isMountedRef,
  selectedDocumentIds,
  setSelectedDocumentIds,
  extractedDocuments,
  caseMatches,
}: DocumentRowActionsParams) {
  const [approveError, setApproveError] = useState<string | null>(null);
  const [cancellingDocumentId, setCancellingDocumentId] = useState<string | null>(null);
  const [cancelActionError, setCancelActionError] = useState<string | null>(null);
  const [retryingDocumentId, setRetryingDocumentId] = useState<string | null>(null);
  const [retryActionError, setRetryActionError] = useState<string | null>(null);
  const [isApprovingSelected, setIsApprovingSelected] = useState(false);

  // One shape for the per-row actions: run the call, store the updated document or the error.
  async function runRowAction(
    documentId: string,
    action: (documentId: string) => Promise<DocumentRecord>,
    setBusyId: (documentId: string | null) => void,
    setError: (message: string | null) => void,
    failureMessage: string,
  ) {
    setBusyId(documentId);
    setError(null);

    try {
      const updated = await action(documentId);

      if (!isMountedRef.current) {
        return;
      }

      setDocumentDetails((current) => {
        const next = new Map(current);
        next.set(documentId, updated);
        return next;
      });
    } catch (err) {
      if (!isMountedRef.current) {
        return;
      }

      setError(err instanceof Error ? err.message : failureMessage);
    } finally {
      if (isMountedRef.current) {
        setBusyId(null);
      }
    }
  }

  const handleCancelDocument = (documentId: string) =>
    runRowAction(documentId, cancelDocument, setCancellingDocumentId, setCancelActionError, 'Failed to cancel document.');
  const handleRetryDocument = (documentId: string) =>
    runRowAction(documentId, retryDocument, setRetryingDocumentId, setRetryActionError, 'Failed to retry document.');

  async function handleApproveSelected() {
    const eligible = extractedDocuments.filter(
      (document) => selectedDocumentIds.has(document.id) && isEligibleForApproval(document, caseMatches),
    );
    if (eligible.length === 0) return;

    setIsApprovingSelected(true);
    setApproveError(null);

    try {
      const result = await bulkApproveDocuments(eligible.map((document) => document.id));
      if (!isMountedRef.current) return;

      setDocumentDetails((current) => {
        const next = new Map(current);
        for (const updated of result.approved) next.set(updated.id, updated);
        return next;
      });
      setSelectedDocumentIds((current) => {
        const next = new Set(current);
        for (const updated of result.approved) next.delete(updated.id);
        return next;
      });
      if (result.failed.length > 0) {
        // One refusal is shown as the backend worded it; several are counted.
        setApproveError(
          result.failed.length === 1
            ? result.failed[0].reason
            : `${result.failed.length} of ${eligible.length} documents could not be approved.`,
        );
      }
    } catch (err) {
      if (!isMountedRef.current) return;
      setApproveError(err instanceof Error ? err.message : 'Failed to approve selected documents.');
    } finally {
      if (isMountedRef.current) setIsApprovingSelected(false);
    }
  }

  return {
    approveError,
    setApproveError,
    cancellingDocumentId,
    cancelActionError,
    retryingDocumentId,
    retryActionError,
    isApprovingSelected,
    handleCancelDocument,
    handleRetryDocument,
    handleApproveSelected,
  };
}
