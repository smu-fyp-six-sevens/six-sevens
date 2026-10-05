import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CaseRecord } from '../App';
import {
  acceptedIdentityNames,
  describeIssue,
  fieldsToDraft,
  isEligibleForApproval,
} from './upload/uploadHelpers';
import { DocumentListPanel } from './upload/DocumentListPanel';
import { MoveDocumentsDialog } from './upload/MoveDocumentsDialog';
import { RequirementsSidebar } from './upload/RequirementsSidebar';
import { UploadSummaryCards } from './upload/UploadSummaryCards';
import AppHeader from '../components/AppHeader';
import ConfirmDialog from '../components/ConfirmDialog';
import DropZone from '../components/DropZone';
import FileRow from '../components/FileRow';
import KebabMenu from '../components/KebabMenu';
import { subscribeToCaseStatus } from '../services/caseSocket';
import type { CaseStatusSubscription } from '../services/caseSocket';
import {
  approveDocument,
  cancelDocument,
  deleteCase,
  retryDocument,
  EXTRACTION_CONFIDENCE_THRESHOLD,
  TERMINAL_DOCUMENT_STATUSES,
  isClassificationConfirmed,
  hasExtractedInformation,
  lowestFieldConfidence,
  bulkDeleteDocuments,
  bulkMoveDocuments,
  listCaseOptions,
  updateCase,
  updateExtractedFields,
  uploadDocuments,
} from '../services/uploadService';
import type {
  BulkUploadItemResult,
  CaseOption,
  DocumentCaseMatch,
  DocumentRecord,
} from '../services/uploadService';
import {
  MAX_BULK_FILES,
  checkFileCount,
  recordUploadedHash,
  validateFiles,
} from '../utils/fileValidation';
import type { FileIssue } from '../utils/fileValidation';
import {
  MAX_COMPANY_NAME_LENGTH,
  validateCompanyName,
} from '../utils/caseValidation';


interface UploadPageProps {
  selectedCase: CaseRecord;
  onBack: () => void;
  onOpenDocument: (documentId: string) => void;
  onCaseUpdated: (companyName: string) => void;
  onCaseDeleted: () => void;
}

export default function UploadPage({
  selectedCase,
  onBack,
  onOpenDocument,
  onCaseUpdated,
  onCaseDeleted,
}: UploadPageProps) {
  const [isEditingCase, setIsEditingCase] = useState(false);
  const [companyNameDraft, setCompanyNameDraft] = useState(selectedCase.applicant);
  const [isSavingCase, setIsSavingCase] = useState(false);
  const [saveCaseError, setSaveCaseError] = useState<string | null>(null);

  useEffect(() => {
    setCompanyNameDraft(selectedCase.applicant);
  }, [selectedCase.applicant]);

  async function handleSaveCase() {
    const trimmed = companyNameDraft.trim();
    const validationError = validateCompanyName(companyNameDraft);
    if (validationError) {
      setSaveCaseError(validationError);
      return;
    }

    setIsSavingCase(true);
    setSaveCaseError(null);

    try {
      const updated = await updateCase(selectedCase.id, trimmed);
      onCaseUpdated(updated.company_name ?? trimmed);
      setIsEditingCase(false);
    } catch (err) {
      setSaveCaseError(err instanceof Error ? err.message : 'Failed to save case.');
    } finally {
      setIsSavingCase(false);
    }
  }

  function handleCancelEditCase() {
    setCompanyNameDraft(selectedCase.applicant);
    setSaveCaseError(null);
    setIsEditingCase(false);
  }

  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [countError, setCountError] = useState<string | null>(null);
  const [rejections, setRejections] = useState<{ file: File; issue: FileIssue }[]>([]);
  const [results, setResults] = useState<BulkUploadItemResult[]>([]);
  const [justSubmitted, setJustSubmitted] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [documentDetails, setDocumentDetails] = useState<Map<string, DocumentRecord>>(new Map());
  const [fulfilledRequirements, setFulfilledRequirements] = useState<Set<string>>(new Set());
  const [proofOfAddressCoverage, setProofOfAddressCoverage] = useState<{
    covered: number;
    total: number;
    unmatched_names: string[];
  }>({ covered: 0, total: 0, unmatched_names: [] });
  const [idDocumentCoverage, setIdDocumentCoverage] = useState<{
    covered: number;
    total: number;
    missing_side: string | null;
  }>({ covered: 0, total: 0, missing_side: null });
  const [fieldDrafts, setFieldDrafts] = useState<Map<string, Record<string, string>>>(new Map());
  const [savingFields, setSavingFields] = useState<Set<string>>(new Set());
  const [saveFieldErrors, setSaveFieldErrors] = useState<Map<string, string>>(new Map());
  const [wsConnected, setWsConnected] = useState(false);
  const [approvingDocumentId, setApprovingDocumentId] = useState<string | null>(null);
  const [approveError, setApproveError] = useState<string | null>(null);
  const [cancellingDocumentId, setCancellingDocumentId] = useState<string | null>(null);
  const [cancelActionError, setCancelActionError] = useState<string | null>(null);
  const [retryingDocumentId, setRetryingDocumentId] = useState<string | null>(null);
  const [retryActionError, setRetryActionError] = useState<string | null>(null);
  // Forces a re-render every so often while any document is PROCESSING, so a "Stuck"/Retry-eligible.
  const [, setStuckTick] = useState(0);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [isDeletingCase, setIsDeletingCase] = useState(false);
  const [caseDeleteOpen, setCaseDeleteOpen] = useState(false);
  // Bulk selection.
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<Set<string>>(new Set());
  const [documentsToDelete, setDocumentsToDelete] = useState<DocumentRecord[]>([]);
  const [isBulkDeleting, setIsBulkDeleting] = useState(false);
  const [isApprovingSelected, setIsApprovingSelected] = useState(false);
  const [isMoveDialogOpen, setIsMoveDialogOpen] = useState(false);
  const [caseOptions, setCaseOptions] = useState<CaseOption[] | null>(null);
  const [isLoadingCaseOptions, setIsLoadingCaseOptions] = useState(false);
  const [moveTargetCaseId, setMoveTargetCaseId] = useState('');
  const [isMovingDocuments, setIsMovingDocuments] = useState(false);
  const [moveError, setMoveError] = useState<string | null>(null);
  // Case-match result per document (see backend/app/case_match.py), pushed with every case snapshot.
  const [caseMatches, setCaseMatches] = useState<Map<string, DocumentCaseMatch>>(new Map());

  // A case-match flag needs manual review until an officer approves the document.
  const caseMatchNeedsReview = useCallback(
    (document: DocumentRecord) =>
      document.status !== 'approved' &&
      caseMatches.get(document.id)?.status === 'manual_review',
    [caseMatches],
  );

  const flaggedDocuments = useMemo(() => Array.from(documentDetails.values()).filter((document) => {
    // Rejecting a document is the officer's final decision, so it's no longer waiting on review.
    if (document.status === 'rejected') return false;
    const lowestConfidence = lowestFieldConfidence(document.extracted_fields);
    return (
      document.routing_status === 'manual_review' ||
      (document.status === 'analyzed' && !isClassificationConfirmed(document)) ||
      (document.status === 'analyzed' && !hasExtractedInformation(document.extracted_fields)) ||
      caseMatchNeedsReview(document) ||
      (lowestConfidence != null && lowestConfidence < EXTRACTION_CONFIDENCE_THRESHOLD) ||
      (document.missing_required_fields?.length ?? 0) > 0
    );
  }), [documentDetails, caseMatchNeedsReview]);

  const identityNames = useMemo(
    () => acceptedIdentityNames(Array.from(documentDetails.values())),
    [documentDetails],
  );

  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const hasOutcome = results.length > 0 || rejections.length > 0;

  // One WebSocket per case (see caseSocket.ts / backend routers/ws.py) replaces the old per-document.
  const caseSocketRef = useRef<CaseStatusSubscription | null>(null);
  useEffect(() => {
    setWsConnected(false);

    const subscription = subscribeToCaseStatus(
      selectedCase.id,
      ({ documents, caseSummary, caseMatch }) => {
        if (!isMountedRef.current) return;

        setDocumentDetails(new Map(documents.map((document) => [document.id, document])));
        setCaseMatches(
          new Map((caseMatch?.documents ?? []).map((match) => [match.document_id, match])),
        );
        setFulfilledRequirements(new Set(caseSummary.fulfilled_requirements));
        setProofOfAddressCoverage(caseSummary.proof_of_address_coverage);
        setIdDocumentCoverage(caseSummary.id_document_coverage);
      },
      (connected) => {
        if (isMountedRef.current) setWsConnected(connected);
      },
    );
    caseSocketRef.current = subscription;

    return () => {
      caseSocketRef.current = null;
      subscription.unsubscribe();
    };
  }, [selectedCase.id]);

  const resultByFilename = useMemo(
    () => new Map(results.map((result) => [result.filename, result])),
    [results],
  );

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
      const { ok, rejected } = await validateFiles(
        selectedFiles,
        selectedCase.id,
      );

      setRejections(rejected);

      if (ok.length === 0) return;

      const response = await uploadDocuments(
        selectedCase.id,
        ok.map((validated) => validated.uploadFile),
      );

      setResults(response.results);

      response.results.forEach((result, index) => {
        if (result.success) {
          recordUploadedHash(selectedCase.id, ok[index].hash);
        }
      });

      // Wakes the backend's poll loop immediately instead of waiting out its idle interval (up to 30s once.
      if (response.results.some((result) => result.success)) {
        caseSocketRef.current?.refresh();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed.');
    } finally {
      setIsSubmitting(false);
    }
  }

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

  function getFieldDraft(
    documentId: string,
    extractedFields: Record<string, unknown>,
  ): Record<string, string> {
    return fieldDrafts.get(documentId) ?? fieldsToDraft(extractedFields);
  }

  function setFieldDraftValue(
    documentId: string,
    extractedFields: Record<string, unknown>,
    key: string,
    value: string,
  ) {
    setFieldDrafts((current) => {
      const next = new Map(current);
      const draft = {
        ...getFieldDraft(documentId, extractedFields),
        [key]: value,
      };
      next.set(documentId, draft);
      return next;
    });
  }

  async function saveFieldDraft(
    documentId: string,
    originalFields: Record<string, unknown>,
  ) {
    const draft = fieldDrafts.get(documentId);
    if (!draft) return;

    setSavingFields((current) => new Set(current).add(documentId));

    setSaveFieldErrors((current) => {
      const next = new Map(current);
      next.delete(documentId);
      return next;
    });

    // Start from the original fields so non-displayed ones (e.g.
    const payload: Record<string, unknown> = { ...originalFields };

    for (const [key, value] of Object.entries(draft)) {
      payload[key] = value.trim() === '' ? null : value;
    }

    try {
      const updated = await updateExtractedFields(documentId, payload);

      if (!isMountedRef.current) return;

      setDocumentDetails(
        (current) => new Map(current).set(documentId, updated),
      );

      setFieldDrafts((current) => {
        const next = new Map(current);
        next.delete(documentId);
        return next;
      });
    } catch (err) {
      if (!isMountedRef.current) return;

      setSaveFieldErrors((current) =>
        new Map(current).set(
          documentId,
          err instanceof Error ? err.message : 'Failed to save fields.',
        ),
      );
    } finally {
      if (isMountedRef.current) {
        setSavingFields((current) => {
          const next = new Set(current);
          next.delete(documentId);
          return next;
        });
      }
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

  async function confirmDeleteCase() {
    setIsDeletingCase(true);
    setDeleteError(null);

    try {
      await deleteCase(selectedCase.id);
      onCaseDeleted();
    } catch (err) {
      setCaseDeleteOpen(false);
      setDeleteError(err instanceof Error ? err.message : 'Failed to delete case.');
      setIsDeletingCase(false);
    }
  }

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

  async function openMoveDialog() {
    setMoveError(null);
    setMoveTargetCaseId('');
    setIsMoveDialogOpen(true);
    if (caseOptions !== null) return;

    setIsLoadingCaseOptions(true);
    try {
      const options = await listCaseOptions();
      if (isMountedRef.current) {
        setCaseOptions(options.filter((option) => option.id !== selectedCase.id));
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

  const documents = useMemo(
    () =>
      Array.from(documentDetails.values()).sort((a, b) =>
        a.uploaded_at.localeCompare(b.uploaded_at),
      ),
    [documentDetails],
  );

  const anyDocumentProcessing = useMemo(
    () => documents.some((document) => document.status === 'processing'),
    [documents],
  );
  // Mirrors routers/cases.py's delete_case guard: blocked while any document is still in flight.
  const caseHasInFlightDocument = useMemo(
    () => documents.some((document) => !TERMINAL_DOCUMENT_STATUSES.has(document.status)),
    [documents],
  );
  useEffect(() => {
    if (!anyDocumentProcessing) {
      return;
    }
    const interval = window.setInterval(() => setStuckTick((n) => n + 1), 15000);
    return () => window.clearInterval(interval);
  }, [anyDocumentProcessing]);

  const extractedDocuments = useMemo(
    () => documents.filter((document) => document.status !== 'approved'),
    [documents],
  );

  const approvedDocuments = useMemo(
    () => documents.filter((document) => document.status === 'approved'),
    [documents],
  );

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

  const completedCount = results.length + rejections.length;
  const successfulCount = results.filter((result) => result.success).length;
  const failedCount =
    results.filter((result) => !result.success).length + rejections.length;

  return (
    <main className="shell shell--upload">
      <AppHeader />

      <div className="upload-view">
        <section className="upload-main">
          <button type="button" className="back-link" onClick={onBack}>
            ← Back to case queue
          </button>

          {isEditingCase ? (
            <div className="add-field-form">
              <div className="add-field-form-row">
                <label htmlFor="case-company-name">Company name</label>
                <input
                  id="case-company-name"
                  type="text"
                  value={companyNameDraft}
                  onChange={(event) => setCompanyNameDraft(event.target.value)}
                  maxLength={MAX_COMPANY_NAME_LENGTH}
                  disabled={isSavingCase}
                  aria-invalid={saveCaseError ? true : undefined}
                  aria-describedby={saveCaseError ? 'case-company-name-error' : undefined}
                />
              </div>

              {saveCaseError && (
                <p id="case-company-name-error" className="add-field-error" role="alert">
                  {saveCaseError}
                </p>
              )}

              <div className="add-field-form-actions">
                <button type="button" onClick={handleCancelEditCase} disabled={isSavingCase}>
                  Cancel
                </button>
                <button type="button" onClick={() => void handleSaveCase()} disabled={isSavingCase}>
                  {isSavingCase ? 'Saving…' : 'Save'}
                </button>
              </div>
            </div>
          ) : (
            <div className="case-header-row">
              <p className="eyebrow">
                Case #{selectedCase.caseNumber} · {selectedCase.type} —{' '}
                {selectedCase.applicant}
              </p>
              <div className="case-header-actions">
                <KebabMenu
                  label={isDeletingCase ? 'Deleting case' : 'Case actions'}
                  disabled={isDeletingCase}
                  items={[
                    {
                      id: 'edit',
                      label: 'Edit case details',
                      onSelect: () => setIsEditingCase(true),
                    },
                    {
                      id: 'delete',
                      label: isDeletingCase ? 'Deleting…' : 'Delete case',
                      danger: true,
                      disabled: isDeletingCase || caseHasInFlightDocument,
                      onSelect: () => setCaseDeleteOpen(true),
                    },
                  ]}
                />
              </div>
            </div>
          )}

          {deleteError && (
            <div className="toast show mixed" role="alert">
              ⚠ {deleteError}
            </div>
          )}

          <h1>Upload supporting documents</h1>

          <p className="sub">
            Accepted files are logged and assigned a document ID for the
            investigation record.
          </p>

          <DropZone onFiles={handleFiles} disabled={isSubmitting} />

          {selectedFiles.length > 0 && (
            <>
              <div className="section-label">
                <span>Selected files</span>
                <span>{selectedFiles.length} selected</span>
              </div>

              <div className="file-list">
                {selectedFiles.map((file, index) => {
                  const rejection = rejections.find(
                    (item) => item.file === file,
                  );

                  return (
                    <FileRow
                      key={`${file.name}-${index}`}
                      file={file}
                      result={resultByFilename.get(file.name)}
                      rejected={
                        rejection
                          ? describeIssue(rejection.issue)
                          : undefined
                      }
                      isUploading={isSubmitting}
                      onRemove={() => removeFile(index)}
                    />
                  );
                })}
              </div>

              <div className="action-bar">
                <span className="count">
                  {hasOutcome
                    ? `${completedCount} / ${selectedFiles.length} processed`
                    : `${selectedFiles.length} file${
                        selectedFiles.length > 1 ? 's' : ''
                      } selected`}
                </span>

                <button
                  type="button"
                  className="upload-btn"
                  disabled={isSubmitting || selectedFiles.length === 0}
                  onClick={() => {
                    void handleSubmit();
                  }}
                >
                  {isSubmitting
                    ? 'Uploading…'
                    : hasOutcome
                      ? 'Upload again'
                      : `Upload ${selectedFiles.length} file${
                          selectedFiles.length > 1 ? 's' : ''
                        }`}
                </button>
              </div>
            </>
          )}

          {selectedFiles.length === 0 && !isSubmitting && (
            <div className="empty">
              No files selected yet — drag files above or click browse
            </div>
          )}

          {countError && (
            <div className="toast show mixed">⚠ {countError}</div>
          )}

          {error && (
            <div className="toast show mixed">⚠ {error}</div>
          )}

          {justSubmitted && hasOutcome && !error && !isSubmitting && (
            <div
              className={`toast show ${
                failedCount > 0 ? 'mixed' : 'all-ok'
              }`}
            >
              <span>{failedCount > 0 ? '⚠' : '✓'}</span>

              <span>
                <b>
                  {successfulCount} of {selectedFiles.length} documents
                  uploaded.
                </b>{' '}
                {failedCount > 0
                  ? `${failedCount} need attention above.`
                  : 'All documents uploaded successfully.'}
              </span>
            </div>
          )}

            <DocumentListPanel
              handleCancelDocument={handleCancelDocument}
              handleRetryDocument={handleRetryDocument}
              allExtractedSelected={allExtractedSelected}
              approveError={approveError}
              approvedDocuments={approvedDocuments}
              approvingDocumentId={approvingDocumentId}
              cancelActionError={cancelActionError}
              cancellingDocumentId={cancellingDocumentId}
              caseMatches={caseMatches}
              clearDocumentSelection={clearDocumentSelection}
              documents={documents}
              eligibleForApprovalCount={eligibleForApprovalCount}
              extractedDocuments={extractedDocuments}
              handleApproveSelected={handleApproveSelected}
              handleSelectAllEligible={handleSelectAllEligible}
              isApprovingSelected={isApprovingSelected}
              onOpenDocument={onOpenDocument}
              openMoveDialog={openMoveDialog}
              retryActionError={retryActionError}
              retryingDocumentId={retryingDocumentId}
              selectedApprovableCount={selectedApprovableCount}
              selectedDocumentIds={selectedDocumentIds}
              selectedExtractedDocuments={selectedExtractedDocuments}
              selectedMovableOrDeletableCount={selectedMovableOrDeletableCount}
              setDocumentsToDelete={setDocumentsToDelete}
              setSelectedDocumentIds={setSelectedDocumentIds}
              toggleDocumentSelected={toggleDocumentSelected}
              wsConnected={wsConnected}
            />



          <UploadSummaryCards
            caseMatchNeedsReview={caseMatchNeedsReview}
            caseMatches={caseMatches}
            flaggedDocuments={flaggedDocuments}
            onOpenDocument={onOpenDocument}
          />
        </section>

        <RequirementsSidebar
          fulfilledRequirements={fulfilledRequirements}
          idDocumentCoverage={idDocumentCoverage}
          identityNames={identityNames}
          proofOfAddressCoverage={proofOfAddressCoverage}
        />
      </div>

      {caseDeleteOpen && (
        <ConfirmDialog
          title="Delete this case?"
          message={`Case ${selectedCase.caseNumber || selectedCase.id} (${selectedCase.applicant}) and its documents will be removed and cannot be recovered.`}
          isConfirming={isDeletingCase}
          onCancel={() => {
            if (!isDeletingCase) setCaseDeleteOpen(false);
          }}
          onConfirm={() => void confirmDeleteCase()}
        />
      )}

      {documentsToDelete.length > 0 && (
        <ConfirmDialog
          title={documentsToDelete.length === 1 ? 'Delete this document?' : `Delete ${documentsToDelete.length} documents?`}
          message={
            documentsToDelete.length === 1
              ? `${documentsToDelete[0].filename} will be removed from this case and cannot be recovered.`
              : `${documentsToDelete.length} documents will be removed from this case and cannot be recovered.`
          }
          isConfirming={isBulkDeleting}
          onCancel={() => {
            if (!isBulkDeleting) setDocumentsToDelete([]);
          }}
          onConfirm={() => void confirmDeleteDocuments()}
        />
      )}

      <MoveDocumentsDialog
        caseOptions={caseOptions}
        confirmMoveSelected={confirmMoveSelected}
        isLoadingCaseOptions={isLoadingCaseOptions}
        isMoveDialogOpen={isMoveDialogOpen}
        isMovingDocuments={isMovingDocuments}
        moveError={moveError}
        moveTargetCaseId={moveTargetCaseId}
        selectedMovableOrDeletableCount={selectedMovableOrDeletableCount}
        setIsMoveDialogOpen={setIsMoveDialogOpen}
        setMoveTargetCaseId={setMoveTargetCaseId}
      />
    </main>
  );
}