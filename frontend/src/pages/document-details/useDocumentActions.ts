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

  async function handleApproveDocument() {
    if (!document) {
      return;
    }

    setIsApproving(true);
    setApproveError(null);

    try {
      const updated = await approveDocument(document.id);
      setDocument(updated);
    } catch (error) {
      setApproveError(
        error instanceof Error
          ? error.message
          : 'Failed to approve document.',
      );
    } finally {
      setIsApproving(false);
    }
  }

  async function handleRejectDocument() {
    if (!document) {
      return;
    }

    setIsRejecting(true);
    setRejectError(null);

    try {
      const updated = await rejectDocument(document.id);
      setDocument(updated);
    } catch (error) {
      setRejectError(
        error instanceof Error
          ? error.message
          : 'Failed to reject document.',
      );
    } finally {
      setIsRejecting(false);
    }
  }

  async function handleCancelDocument() {
    if (!document) {
      return;
    }

    setIsCancelling(true);
    setCancelError(null);

    try {
      const updated = await cancelDocument(document.id);
      setDocument(updated);
    } catch (error) {
      setCancelError(
        error instanceof Error
          ? error.message
          : 'Failed to cancel document.',
      );
    } finally {
      setIsCancelling(false);
    }
  }

  async function handleRetryDocument() {
    if (!document) {
      return;
    }

    setIsRetrying(true);
    setRetryError(null);

    try {
      const updated = await retryDocument(document.id);
      setDocument(updated);
    } catch (error) {
      setRetryError(
        error instanceof Error
          ? error.message
          : 'Failed to retry document.',
      );
    } finally {
      setIsRetrying(false);
    }
  }

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
