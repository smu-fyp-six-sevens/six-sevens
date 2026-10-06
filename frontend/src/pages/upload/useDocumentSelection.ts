import { useMemo, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { CaseStatusSubscription } from '../../services/caseSocket';
import {
  TERMINAL_DOCUMENT_STATUSES,
  bulkDeleteDocuments,
  bulkMoveDocuments,
  listCaseOptions,
} from '../../services/uploadService';
import type { CaseOption, DocumentCaseMatch, DocumentRecord } from '../../services/uploadService';
import { isEligibleForApproval } from './uploadHelpers';

interface DocumentSelectionParams {
  caseId: string;
  extractedDocuments: DocumentRecord[];
  caseMatches: Map<string, DocumentCaseMatch>;
  setDocumentDetails: Dispatch<SetStateAction<Map<string, DocumentRecord>>>;
  setCaseMatches: Dispatch<SetStateAction<Map<string, DocumentCaseMatch>>>;
  setDeleteError: Dispatch<SetStateAction<string | null>>;
  isMountedRef: { current: boolean };
  caseSocketRef: { current: CaseStatusSubscription | null };
}

// Bulk selection of extracted documents, bulk delete, and bulk move to another case.
export function useDocumentSelection({
  caseId,
  extractedDocuments,
  caseMatches,
  setDocumentDetails,
  setCaseMatches,
  setDeleteError,
  isMountedRef,
  caseSocketRef,
}: DocumentSelectionParams) {
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<Set<string>>(new Set());
  const [documentsToDelete, setDocumentsToDelete] = useState<DocumentRecord[]>([]);
  const [isBulkDeleting, setIsBulkDeleting] = useState(false);
  const [isMoveDialogOpen, setIsMoveDialogOpen] = useState(false);
  const [caseOptions, setCaseOptions] = useState<CaseOption[] | null>(null);
  const [isLoadingCaseOptions, setIsLoadingCaseOptions] = useState(false);
  const [moveTargetCaseId, setMoveTargetCaseId] = useState('');
  const [isMovingDocuments, setIsMovingDocuments] = useState(false);
  const [moveError, setMoveError] = useState<string | null>(null);

  const selectedExtractedDocuments = useMemo(
    () => extractedDocuments.filter((document) => selectedDocumentIds.has(document.id)),
    [extractedDocuments, selectedDocumentIds],
  );
  const allExtractedSelected =
    extractedDocuments.length > 0 && selectedExtractedDocuments.length === extractedDocuments.length;
  const eligibleForApprovalCount = useMemo(
    () => extractedDocuments.filter((document) => isEligibleForApproval(document, caseMatches)).length,
    [extractedDocuments, caseMatches],
  );
  const selectedApprovableCount = useMemo(
    () => selectedExtractedDocuments.filter((document) => isEligibleForApproval(document, caseMatches)).length,
    [selectedExtractedDocuments, caseMatches],
  );
  const selectedMovableOrDeletableCount = useMemo(
    () => selectedExtractedDocuments.filter((document) => TERMINAL_DOCUMENT_STATUSES.has(document.status)).length,
    [selectedExtractedDocuments],
  );

  function clearDocumentSelection() {
    setSelectedDocumentIds(new Set());
  }

  function toggleDocumentSelected(documentId: string) {
    setSelectedDocumentIds((current) => {
      const next = new Set(current);
      if (next.has(documentId)) {
        next.delete(documentId);
      } else {
        next.add(documentId);
      }
      return next;
    });
  }

  async function confirmDeleteDocuments() {
    if (documentsToDelete.length === 0) return;

    const targets = documentsToDelete;
    setIsBulkDeleting(true);
    setDeleteError(null);

    try {
      const result = await bulkDeleteDocuments(targets.map((document) => document.id));
      const deletedIds = new Set(result.deleted);

      if (deletedIds.size > 0) {
        setDocumentDetails((current) => {
          const next = new Map(current);
          for (const id of deletedIds) next.delete(id);
          return next;
        });
        setCaseMatches((current) => {
          const next = new Map(current);
          for (const id of deletedIds) next.delete(id);
          return next;
        });
        setSelectedDocumentIds((current) => {
          const next = new Set(current);
          for (const id of deletedIds) next.delete(id);
          return next;
        });
        caseSocketRef.current?.refresh();
      }

      if (result.failed.length > 0) {
        setDeleteError(
          result.failed.length === targets.length
            ? 'Failed to delete the selected document(s).'
            : `${result.failed.length} of ${targets.length} documents could not be deleted.`,
        );
      }
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Failed to delete the selected document(s).');
    }

    setDocumentsToDelete([]);
    setIsBulkDeleting(false);
  }

  function handleSelectAllEligible() {
    setSelectedDocumentIds(
      new Set(
        extractedDocuments
          .filter((document) => isEligibleForApproval(document, caseMatches))
          .map((document) => document.id),
      ),
    );
  }

  async function openMoveDialog() {
    setMoveError(null);
    setMoveTargetCaseId('');
    setIsMoveDialogOpen(true);
    if (caseOptions !== null) return;

    setIsLoadingCaseOptions(true);
    try {
      const options = await listCaseOptions();
      if (isMountedRef.current) {
        setCaseOptions(options.filter((option) => option.id !== caseId));
      }
    } catch (err) {
      if (isMountedRef.current) {
        setMoveError(err instanceof Error ? err.message : 'Failed to load cases.');
      }
    } finally {
      if (isMountedRef.current) setIsLoadingCaseOptions(false);
    }
  }

  async function confirmMoveSelected() {
    if (!moveTargetCaseId) return;

    const eligible = extractedDocuments.filter(
      (document) =>
        selectedDocumentIds.has(document.id) &&
        TERMINAL_DOCUMENT_STATUSES.has(document.status) &&
        // Locked once rejected — the backend refuses the move too.
        document.status !== 'rejected',
    );
    if (eligible.length === 0) {
      setIsMoveDialogOpen(false);
      return;
    }

    setIsMovingDocuments(true);
    setMoveError(null);

    try {
      const result = await bulkMoveDocuments(eligible.map((document) => document.id), moveTargetCaseId);
      const movedIds = new Set(result.moved);

      if (movedIds.size > 0) {
        setDocumentDetails((current) => {
          const next = new Map(current);
          for (const id of movedIds) next.delete(id);
          return next;
        });
        setCaseMatches((current) => {
          const next = new Map(current);
          for (const id of movedIds) next.delete(id);
          return next;
        });
        setSelectedDocumentIds((current) => {
          const next = new Set(current);
          for (const id of movedIds) next.delete(id);
          return next;
        });
        caseSocketRef.current?.refresh();
      }

      if (result.failed.length > 0) {
        setMoveError(
          result.failed.length === eligible.length
            ? 'Failed to move the selected document(s).'
            : `${result.failed.length} of ${eligible.length} documents could not be moved.`,
        );
      } else {
        setIsMoveDialogOpen(false);
      }
    } catch (err) {
      setMoveError(err instanceof Error ? err.message : 'Failed to move the selected document(s).');
    }

    setIsMovingDocuments(false);
  }

  return {
    selectedDocumentIds,
    setSelectedDocumentIds,
    documentsToDelete,
    setDocumentsToDelete,
    isBulkDeleting,
    isMoveDialogOpen,
    setIsMoveDialogOpen,
    caseOptions,
    isLoadingCaseOptions,
    moveTargetCaseId,
    setMoveTargetCaseId,
    isMovingDocuments,
    moveError,
    selectedExtractedDocuments,
    allExtractedSelected,
    eligibleForApprovalCount,
    selectedApprovableCount,
    selectedMovableOrDeletableCount,
    clearDocumentSelection,
    toggleDocumentSelected,
    confirmDeleteDocuments,
    handleSelectAllEligible,
    openMoveDialog,
    confirmMoveSelected,
  };
}
