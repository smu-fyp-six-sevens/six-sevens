import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
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
import { AdditionalDocumentsCard } from './upload/AdditionalDocumentsCard';
import { AuditTrailCard } from './upload/AuditTrailCard';
import { CaseCostCard } from './upload/CaseCostCard';
import { CaseAnalysisCard } from './upload/CaseAnalysisCard';
import { CaseRiskCard } from './upload/CaseRiskCard';
import { CaseOverview } from './upload/CaseOverview';
import { CaseSectionNav } from './upload/CaseSectionNav';
import { caseSectionStatuses, isCaseSection, type CaseSection } from './upload/caseSections';
import { FlaggedItemsCard } from './upload/FlaggedItemsCard';
import { NotesCard } from '../components/NotesCard';
import AppHeader from '../components/AppHeader';
import CaseHeader, { CaseTitle, caseCrumb, caseQueueCrumb } from '../components/CaseHeader';
import { useCases } from '../casesContext';
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
  onOpenDocument: (documentId: string) => void;
  onCaseUpdated: (companyName: string) => void;
  onCaseDeleted: () => void;
}

export default function UploadPage({
  selectedCase,
  onOpenDocument,
  onCaseUpdated,
  onCaseDeleted,
}: UploadPageProps) {
  const cases = useCases();
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

  // Kept in the URL so a refresh or a trip to a document and back lands on the same section.
  const [searchParams, setSearchParams] = useSearchParams();
  const sectionParam = searchParams.get('section');
  const activeSection: CaseSection = isCaseSection(sectionParam) ? sectionParam : 'documents';
  const selectSection = (section: CaseSection) => {
    setSearchParams(section === 'documents' ? {} : { section }, { replace: true });
  };

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

      <CaseHeader crumbs={[caseQueueCrumb, caseCrumb(cases, selectedCase, { current: true })]}>
        {isEditingCase ? (
          <div className="case-title-editing">
            <label htmlFor="case-company-name" className="sr-only">Company name</label>
            <div className="case-title">
              <input
                id="case-company-name"
                type="text"
                className="case-title-input"
                value={companyNameDraft}
                onChange={(event) => setCompanyNameDraft(event.target.value)}
                maxLength={MAX_COMPANY_NAME_LENGTH}
                disabled={isSavingCase}
                aria-invalid={saveCaseError ? true : undefined}
                aria-describedby={saveCaseError ? 'case-company-name-error' : undefined}
                autoFocus
              />
              <span className="add-field-form-actions">
                <button type="button" onClick={handleCancelEditCase} disabled={isSavingCase}>
                  Cancel
                </button>
                <button type="button" onClick={() => void handleSaveCase()} disabled={isSavingCase}>
                  {isSavingCase ? 'Saving…' : 'Save'}
                </button>
              </span>
            </div>
            {saveCaseError && (
              <p id="case-company-name-error" className="add-field-error" role="alert">
                {saveCaseError}
              </p>
            )}
          </div>
        ) : (
          <>
            <CaseTitle caseRecord={selectedCase} />
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
          </>
        )}
      </CaseHeader>

      {deleteError && (
        <div className="toast show mixed" role="alert">
          ⚠ {deleteError}
        </div>
      )}

      <div className="case-layout">
        <CaseSectionNav
          activeSection={activeSection}
          statuses={caseSectionStatuses({
            documentCount: documents.length,
            approvedCount: approvedDocuments.length,
            hasInFlightDocument: caseHasInFlightDocument,
            flaggedCount: flaggedDocuments.length,
            analysisReady,
            analysisOverview,
            risk,
            additionalDocumentCount: additionalDocuments
              ? additionalDocuments.items.length
              : supportingDocumentRecommendations.length,
          })}
          onSelect={selectSection}
        />

        <div className="case-content">
          {activeSection === 'summary' && (
            <CaseOverview
              documents={documents}
              flaggedDocuments={flaggedDocuments}
              approvedDocuments={approvedDocuments}
              caseMatches={caseMatches}
              risk={risk}
              analysisReady={analysisReady}
              analysisOverview={analysisOverview}
              snapshotLoaded={snapshotLoaded}
              onSelectSection={selectSection}
            />
          )}

          {activeSection === 'documents' && (
            <div className="upload-view">
              <section className="upload-main">
                <h2 className="section-title">Upload supporting documents</h2>

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
              </section>

              <RequirementsSidebar
                fulfilledRequirements={fulfilledRequirements}
                idDocumentCoverage={idDocumentCoverage}
                identityNames={identityNames}
                proofOfAddressCoverage={proofOfAddressCoverage}
              />
            </div>
          )}

          {activeSection === 'flagged' && (
            <FlaggedItemsCard
              caseMatchNeedsReview={caseMatchNeedsReview}
              caseMatches={caseMatches}
              flaggedDocuments={flaggedDocuments}
              onOpenDocument={onOpenDocument}
            />
          )}

          {activeSection === 'risk' && <CaseRiskCard risk={risk} caseId={selectedCase.id} caseDocuments={documents} />}

          {activeSection === 'analysis' && (
            <CaseAnalysisCard caseId={selectedCase.id} analysisReady={analysisReady} overview={analysisOverview} />
          )}

          {/* Both come from the latest complete case analysis, so risk is set exactly when one exists. */}
          {activeSection === 'additional' && (
            <AdditionalDocumentsCard
              additionalDocuments={additionalDocuments}
              legacyRecommendations={supportingDocumentRecommendations}
              analysed={risk !== null}
            />
          )}

          {activeSection === 'notes' && <NotesCard caseId={selectedCase.id} caseNumber={selectedCase.caseNumber} documents={documents} />}

          {activeSection === 'audit' && <AuditTrailCard />}

          {activeSection === 'cost' && <CaseCostCard caseId={selectedCase.id} documents={documents} />}
        </div>
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
