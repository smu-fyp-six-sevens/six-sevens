import { useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import {
  approveDocument,
  cancelDocument,
  deleteDocument,
  rejectDocument,
  retryDocument,
} from '../../services/uploadService';
import type { DocumentRecord } from '../../services/uploadService';

interface DocumentActionsParams {
  document: DocumentRecord | null;
  setDocument: Dispatch<SetStateAction<DocumentRecord | null>>;
  onDocumentDeleted: () => void;
}

// Approve, reject, cancel, retry and delete for the open document.
export function useDocumentActions({ document, setDocument, onDocumentDeleted }: DocumentActionsParams) {
  const [isApproving, setIsApproving] = useState(false);
  const [approveError, setApproveError] = useState<string | null>(null);

  const [isRejecting, setIsRejecting] = useState(false);
  const [rejectError, setRejectError] = useState<string | null>(null);

  const [isCancelling, setIsCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  const [isRetrying, setIsRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);

  const [isDeletingDocument, setIsDeletingDocument] = useState(false);
  const [deleteDocumentError, setDeleteDocumentError] = useState<string | null>(null);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);

  // One shape for approve, reject, cancel and retry: run the call, store the updated document or the error.
  async function runDocumentAction(
    action: (documentId: string) => Promise<DocumentRecord>,
    setBusy: (busy: boolean) => void,
    setError: (message: string | null) => void,
    failureMessage: string,
  ) {
    if (!document) {
      return;
    }

    setBusy(true);
    setError(null);

    try {
      setDocument(await action(document.id));
    } catch (error) {
      setError(error instanceof Error ? error.message : failureMessage);
    } finally {
      setBusy(false);
    }
  }

  const handleApproveDocument = () =>
    runDocumentAction(approveDocument, setIsApproving, setApproveError, 'Failed to approve document.');
  const handleRejectDocument = () =>
    runDocumentAction(rejectDocument, setIsRejecting, setRejectError, 'Failed to reject document.');
  const handleCancelDocument = () =>
    runDocumentAction(cancelDocument, setIsCancelling, setCancelError, 'Failed to cancel document.');
  const handleRetryDocument = () =>
    runDocumentAction(retryDocument, setIsRetrying, setRetryError, 'Failed to retry document.');

  async function confirmDeleteDocument() {
    if (!document) return;

    setIsDeletingDocument(true);
    setDeleteDocumentError(null);

    try {
      await deleteDocument(document.id);
      onDocumentDeleted();
    } catch (error) {
      setDeleteConfirmOpen(false);
      setDeleteDocumentError(
        error instanceof Error ? error.message : 'Failed to delete document.',
      );
      setIsDeletingDocument(false);
    }
  }

  return {
    isApproving,
    approveError,
    handleApproveDocument,
    isRejecting,
    rejectError,
    handleRejectDocument,
    isCancelling,
    cancelError,
    handleCancelDocument,
    isRetrying,
    retryError,
    handleRetryDocument,
    isDeletingDocument,
    deleteDocumentError,
    deleteConfirmOpen,
    setDeleteConfirmOpen,
    confirmDeleteDocument,
  };
}
