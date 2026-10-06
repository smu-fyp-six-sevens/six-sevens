import { useState } from 'react';
import type { CaseRecord } from '../App';
import {
  describeIssue,
  fieldsToDraft,
} from './upload/uploadHelpers';
import { useCaseSettings } from './upload/useCaseSettings';
import { useCaseSnapshot } from './upload/useCaseSnapshot';
import { useDocumentActions } from './upload/useDocumentActions';
import { useDocumentSelection } from './upload/useDocumentSelection';
import { useUploadSelection } from './upload/useUploadSelection';
import { DocumentListPanel } from './upload/DocumentListPanel';
import { MoveDocumentsDialog } from './upload/MoveDocumentsDialog';
import { RequirementsSidebar } from './upload/RequirementsSidebar';
import { UploadSummaryCards } from './upload/UploadSummaryCards';
import AppHeader from '../components/AppHeader';
import ConfirmDialog from '../components/ConfirmDialog';
import DropZone from '../components/DropZone';
import FileRow from '../components/FileRow';
import KebabMenu from '../components/KebabMenu';
import {
  updateExtractedFields,
} from '../services/uploadService';
import type {
} from '../services/uploadService';
import {
} from '../utils/fileValidation';
import {
  MAX_COMPANY_NAME_LENGTH,
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
  const {
    setDocumentDetails,
    fulfilledRequirements,
    proofOfAddressCoverage,
    idDocumentCoverage,
    wsConnected,
    caseMatches,
    setCaseMatches,
    isMountedRef,
    caseSocketRef,
    documents,
    caseHasInFlightDocument,
    extractedDocuments,
    approvedDocuments,
    flaggedDocuments,
    identityNames,
    caseMatchNeedsReview,
  } = useCaseSnapshot(selectedCase.id);

  const {
    isEditingCase,
    setIsEditingCase,
    companyNameDraft,
    setCompanyNameDraft,
    isSavingCase,
    saveCaseError,
    handleSaveCase,
    handleCancelEditCase,
    deleteError,
    setDeleteError,
    isDeletingCase,
    caseDeleteOpen,
    setCaseDeleteOpen,
    confirmDeleteCase,
  } = useCaseSettings({ selectedCase, onCaseUpdated, onCaseDeleted });

  const {
    selectedFiles,
    countError,
    rejections,
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
  } = useUploadSelection({ caseId: selectedCase.id, caseSocketRef });

  const {
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
  } = useDocumentSelection({
    caseId: selectedCase.id,
    extractedDocuments,
    caseMatches,
    setDocumentDetails,
    setCaseMatches,
    setDeleteError,
    isMountedRef,
    caseSocketRef,
  });

  const {
    approvingDocumentId,
    approveError,
    cancellingDocumentId,
    cancelActionError,
    retryingDocumentId,
    retryActionError,
    isApprovingSelected,
    handleCancelDocument,
    handleRetryDocument,
    handleApproveSelected,
  } = useDocumentActions({
    setDocumentDetails,
    isMountedRef,
    selectedDocumentIds,
    setSelectedDocumentIds,
    extractedDocuments,
    caseMatches,
  });

  // Unused field-draft state, kept until the dead code is removed (see PENDING_WORK.md).
  const [fieldDrafts, setFieldDrafts] = useState<Map<string, Record<string, string>>>(new Map());
  const [savingFields, setSavingFields] = useState<Set<string>>(new Set());
  const [saveFieldErrors, setSaveFieldErrors] = useState<Map<string, string>>(new Map());

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