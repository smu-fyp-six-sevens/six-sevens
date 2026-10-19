import { useMemo } from 'react';
import type { CaseRecord } from '../App';
import {
  describeIssue,
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
import { CaseStageCard } from './upload/CaseStageCard';
import AppHeader from '../components/AppHeader';
import ConfirmDialog from '../components/ConfirmDialog';
import DropZone from '../components/DropZone';
import FileRow from '../components/FileRow';
import KebabMenu from '../components/KebabMenu';
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
    risk,
    analysisReady,
    analysisOverview,
    supportingDocumentRecommendations,
    additionalDocuments,
    stage,
    setStage,
    stageHistory,
    setStageHistory,
    wsConnected,
    snapshotLoaded,
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

  const isFinalised = stage === 'finalised';

  // One line for the Case Progress card when something failed and is holding the case up.
  const stageProblem = useMemo(() => {
    const plural = (count: number) => (count === 1 ? '1 document' : `${count} documents`);
    const failedProcessing = documents.filter((document) => document.status === 'failed').length;
    const failedDetection = documents.filter((document) => document.has_detection_error).length;
    if (failedProcessing > 0) return `${plural(failedProcessing)} failed processing. Retry or remove ${failedProcessing === 1 ? 'it' : 'them'}.`;
    if (failedDetection > 0) return `Detection failed on ${plural(failedDetection)}. Retry it from the document page.`;
    if (analysisOverview?.status === 'failed') return 'The last case analysis failed. Run it again.';
    return null;
  }, [documents, analysisOverview]);

  const liveDocumentIds = useMemo(
    () => (snapshotLoaded ? new Set(documents.map((document) => document.id)) : null),
    [snapshotLoaded, documents],
  );

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
  } = useUploadSelection({ caseId: selectedCase.id, caseSocketRef, liveDocumentIds });

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
                      disabled: isFinalised,
                      onSelect: () => setIsEditingCase(true),
                    },
                    {
                      id: 'delete',
                      label: isDeletingCase ? 'Deleting…' : 'Delete case',
                      danger: true,
                      disabled: isFinalised || isDeletingCase || caseHasInFlightDocument,
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

          {isFinalised ? (
            <p className="case-finalised-notice" role="status">
              This case is finalised, so nothing can be uploaded or changed. Reopen it from Case Progress to make changes.
            </p>
          ) : (
          <>
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
          </>
          )}

            <DocumentListPanel
              handleCancelDocument={handleCancelDocument}
              handleRetryDocument={handleRetryDocument}
              allExtractedSelected={allExtractedSelected}
              approveError={approveError}
              approvedDocuments={approvedDocuments}
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
              readOnly={isFinalised}
            />



          <CaseStageCard
            caseId={selectedCase.id}
            stage={stage}
            history={stageHistory}
            problem={stageProblem}
            onChanged={(summary) => {
              setStage(summary.status ?? 'open');
              setStageHistory(summary.status_history ?? []);
            }}
          />

          <UploadSummaryCards
            analysisOverview={analysisOverview}
            analysisReady={analysisReady && !isFinalised}
            caseId={selectedCase.id}
            caseMatchNeedsReview={caseMatchNeedsReview}
            caseMatches={caseMatches}
            flaggedDocuments={flaggedDocuments}
            onOpenDocument={onOpenDocument}
            risk={risk}
            additionalDocuments={additionalDocuments}
            supportingDocumentRecommendations={supportingDocumentRecommendations}
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