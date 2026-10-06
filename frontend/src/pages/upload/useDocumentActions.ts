import { useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import {
  approveDocument,
  cancelDocument,
  retryDocument,
} from '../../services/uploadService';
import type { DocumentCaseMatch, DocumentRecord } from '../../services/uploadService';
import { isEligibleForApproval } from './uploadHelpers';

interface DocumentActionsParams {
  setDocumentDetails: Dispatch<SetStateAction<Map<string, DocumentRecord>>>;
  isMountedRef: { current: boolean };
  selectedDocumentIds: Set<string>;
  setSelectedDocumentIds: Dispatch<SetStateAction<Set<string>>>;
  extractedDocuments: DocumentRecord[];
  caseMatches: Map<string, DocumentCaseMatch>;
}

// Per-row approve, cancel and retry, plus approve-selected. approveError is shared by single and bulk approve.
export function useDocumentActions({
  setDocumentDetails,
  isMountedRef,
  selectedDocumentIds,
  setSelectedDocumentIds,
  extractedDocuments,
  caseMatches,
}: DocumentActionsParams) {
  const [approvingDocumentId, setApprovingDocumentId] = useState<string | null>(null);
  const [approveError, setApproveError] = useState<string | null>(null);
  const [cancellingDocumentId, setCancellingDocumentId] = useState<string | null>(null);
  const [cancelActionError, setCancelActionError] = useState<string | null>(null);
  const [retryingDocumentId, setRetryingDocumentId] = useState<string | null>(null);
  const [retryActionError, setRetryActionError] = useState<string | null>(null);
  const [isApprovingSelected, setIsApprovingSelected] = useState(false);

  async function handleApproveDocument(documentId: string) {
    setApprovingDocumentId(documentId);
    setApproveError(null);

    try {
      const updated = await approveDocument(documentId);

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

      setApproveError(
        err instanceof Error
          ? err.message
          : 'Failed to approve document.',
      );
    } finally {
      if (isMountedRef.current) {
        setApprovingDocumentId(null);
      }
    }
  }

  async function handleCancelDocument(documentId: string) {
    setCancellingDocumentId(documentId);
    setCancelActionError(null);

    try {
      const updated = await cancelDocument(documentId);

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

      setCancelActionError(
        err instanceof Error ? err.message : 'Failed to cancel document.',
      );
    } finally {
      if (isMountedRef.current) {
        setCancellingDocumentId(null);
      }
    }
  }

  async function handleRetryDocument(documentId: string) {
    setRetryingDocumentId(documentId);
    setRetryActionError(null);

    try {
      const updated = await retryDocument(documentId);

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

      setRetryActionError(
        err instanceof Error ? err.message : 'Failed to retry document.',
      );
    } finally {
      if (isMountedRef.current) {
        setRetryingDocumentId(null);
      }
    }
  }

  async function handleApproveSelected() {
    const eligible = extractedDocuments.filter(
      (document) => selectedDocumentIds.has(document.id) && isEligibleForApproval(document, caseMatches),
    );
    if (eligible.length === 0) return;

    setIsApprovingSelected(true);
    setApproveError(null);

    try {
      const updatedDocuments = await Promise.all(
        eligible.map((document) => approveDocument(document.id)),
      );
      if (!isMountedRef.current) return;

      setDocumentDetails((current) => {
        const next = new Map(current);
        for (const updated of updatedDocuments) next.set(updated.id, updated);
        return next;
      });
      setSelectedDocumentIds((current) => {
        const next = new Set(current);
        for (const updated of updatedDocuments) next.delete(updated.id);
        return next;
      });
    } catch (err) {
      if (!isMountedRef.current) return;
      setApproveError(err instanceof Error ? err.message : 'Failed to approve selected documents.');
    } finally {
      if (isMountedRef.current) setIsApprovingSelected(false);
    }
  }

  return {
    approvingDocumentId,
    approveError,
    setApproveError,
    cancellingDocumentId,
    cancelActionError,
    retryingDocumentId,
    retryActionError,
    isApprovingSelected,
    handleApproveDocument,
    handleCancelDocument,
    handleRetryDocument,
    handleApproveSelected,
  };
}
