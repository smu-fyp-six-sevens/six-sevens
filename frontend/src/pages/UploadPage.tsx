import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { CaseRecord } from '../App';
import {
  describeIssue,
} from './upload/uploadHelpers';
import { useCaseSettings } from './upload/useCaseSettings';
import { useCaseSnapshot } from './upload/useCaseSnapshot';
import { useDocumentRowActions } from './upload/useDocumentRowActions';
import { useDocumentSelection } from './upload/useDocumentSelection';
import { useUploadSelection } from './upload/useUploadSelection';
import { DocumentListPanel } from './upload/DocumentListPanel';
import { MoveDocumentsDialog } from './upload/MoveDocumentsDialog';
import { AdditionalDocumentsCard } from './upload/AdditionalDocumentsCard';
import { AuditTrailCard } from './upload/AuditTrailCard';
import { CaseCostCard } from './upload/CaseCostCard';
import { CaseAnalysisCard } from './upload/CaseAnalysisCard';
import { CaseRiskCard } from './upload/CaseRiskCard';
import { CaseOverview } from './upload/CaseOverview';
import { SeverityCard } from './upload/SeverityGauge';
import { SeverityCardSkeleton } from '../components/Skeletons';
import { CaseSectionNav } from './upload/CaseSectionNav';
import { useCaseReportDownload } from './upload/useCaseReportDownload';
import { CharacterCount } from '../components/CharacterCount';
import { CaseStageCard } from './upload/CaseStageCard';
import { caseSectionStatuses, isCaseSection, type CaseSection } from './upload/caseSections';
import { reviewReason } from './upload/reviewReason';
import { missingRequirements as missingChecklist } from './upload/documentChecklist';
import { NotesCard } from '../components/NotesCard';
import AppHeader from '../components/AppHeader';
import CaseHeader, { CaseTitle } from '../components/CaseHeader';
import CaseFrame from '../components/CaseFrame';
import { caseCrumb, caseQueueCrumb } from '../components/caseCrumbs';
import ConfirmDialog from '../components/ConfirmDialog';
import FinalisedBanner from '../components/FinalisedBanner';
import DropZone from '../components/DropZone';
import FileRow from '../components/FileRow';
import KebabMenu from '../components/KebabMenu';
import {
  MAX_COMPANY_NAME_LENGTH,
} from '../utils/caseValidation';
import { TERMINAL_DOCUMENT_STATUSES } from '../services/uploadService';
import { ErrorMessage } from '../components/ErrorMessage';
import { Icon } from '../components/Icon';


const NAV_COLLAPSED_KEY = 'caseNav.collapsed';

// Per-browser, like the queue's filters; storage can throw (private mode), so it is only a convenience.
function loadNavCollapsed(): boolean {
  try {
    return localStorage.getItem(NAV_COLLAPSED_KEY) === 'true';
  } catch {
    return false;
  }
}

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
  const {
    setDocumentDetails,
    fulfilledRequirements,
    unapprovedRequirements,
    reviewsRequired,
    proofOfAddressCoverage,
    idDocumentCoverage,
    risk,
    analysisReady,
    analysisOverview,
    additionalDocuments,
    stage,
    setStage,
    stageFailure,
    setStageFailure,
    findingsToReview,
    setFindingsToReview,
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
    rejectedDocuments,
    flaggedDocuments,
    caseMatchNeedsReview,
  } = useCaseSnapshot(selectedCase.id);

  // Kept in the URL so a refresh or a trip to a document and back lands on the same section.
  const [searchParams, setSearchParams] = useSearchParams();
  // Approved documents live in the Documents tab now; an old link to the Approved tab lands there.
  const rawSection = searchParams.get('section');
  const sectionParam = rawSection === 'approved' ? 'documents' : rawSection;
  const activeSection: CaseSection = isCaseSection(sectionParam) ? sectionParam : 'summary';
  const selectSection = (section: CaseSection) => {
    setSearchParams(section === 'summary' ? {} : { section }, { replace: true });
  };

  const [navCollapsed, setNavCollapsed] = useState(loadNavCollapsed);
  const [reportError, setReportError] = useState<string | null>(null);
  const report = useCaseReportDownload(selectedCase.id, selectedCase.caseNumber, setReportError);
  useEffect(() => {
    try {
      localStorage.setItem(NAV_COLLAPSED_KEY, String(navCollapsed));
    } catch {
      // Not remembering the menu is fine.
    }
  }, [navCollapsed]);

  // Until the first snapshot arrives, the stage the queue row showed.
  const currentStage = snapshotLoaded ? stage : selectedCase.caseStatus;
  const isFinalised = currentStage === 'finalised';

  // For a case that enforces the checklist, the stage and the analysis wait for these to be approved.
  const missingRequirements = unapprovedRequirements.length;
  // The required documents the Documents tab lists as missing, for its menu subtitle.
  const missingRequirementsList = missingChecklist(fulfilledRequirements, { idDocument: idDocumentCoverage, proofOfAddress: proofOfAddressCoverage });

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

  const selection = useDocumentSelection({
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
    selectedMovableOrDeletableCount,
    confirmDeleteDocuments,
    confirmMoveSelected,
  } = selection;

  // What each flagged document waits on, shown under its name in the document list.
  const reviewReasons = useMemo(
    () => new Map(flaggedDocuments.map((document) => [document.id, reviewReason(document, caseMatches.get(document.id), caseMatchNeedsReview)])),
    [flaggedDocuments, caseMatches, caseMatchNeedsReview],
  );

  const rowActions = useDocumentRowActions({
    setDocumentDetails,
    isMountedRef,
    selectedDocumentIds: selection.selectedDocumentIds,
    setSelectedDocumentIds: selection.setSelectedDocumentIds,
    extractedDocuments,
    caseMatches,
  });

  return (
    <main className="shell shell--upload">
      <AppHeader />

      <CaseFrame
        collapsed={navCollapsed}
        nav={
          <CaseSectionNav
            collapsed={navCollapsed}
            statusesLoading={!snapshotLoaded}
            onToggleCollapsed={() => setNavCollapsed((collapsed) => !collapsed)}
            activeSection={activeSection}
            statuses={caseSectionStatuses({
              documentCount: documents.length,
              approvedCount: approvedDocuments.length,
              detectedCount: approvedDocuments.filter((document) => document.status === 'detected').length,
            missingCount: missingRequirementsList.length,
              // Not yet approved or rejected, so still uploading or extracting (detection comes after approval).
              processingCount: extractedDocuments.filter((document) => !TERMINAL_DOCUMENT_STATUSES.has(document.status)).length,
              flaggedCount: flaggedDocuments.length,
              toApproveCount: extractedDocuments.filter(
                (document) => document.status === 'analyzed' && !flaggedDocuments.includes(document),
              ).length,
              analysisReady,
              analysisOverview,
              risk,
              additionalDocumentCount: additionalDocuments?.items.length ?? 0,
            })}
            onSelect={selectSection}
          />
        }
      >
      <CaseHeader
        crumbs={[caseQueueCrumb, caseCrumb(selectedCase, { current: true })]}
        notice={isFinalised ? <FinalisedBanner /> : undefined}
      >
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
            <CharacterCount length={companyNameDraft.length} />
            {saveCaseError && (
              <ErrorMessage inline id="case-company-name-error" className="add-field-error" message={saveCaseError} />
            )}
          </div>
        ) : (
          <>
            <CaseTitle caseRecord={selectedCase} />
            <span className="case-title-actions">
              <KebabMenu
                label={isDeletingCase ? 'Deleting case' : 'Case actions'}
                disabled={isDeletingCase}
                items={[
                  {
                    id: 'report',
                    label: report.isDownloading ? 'Preparing report…' : 'Download report',
                    disabled: report.isDownloading,
                    onSelect: () => void report.download(),
                  },
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
            </span>
          </>
        )}
      </CaseHeader>

      {deleteError && <ErrorMessage className="toast-error" message={deleteError} />}
      {reportError && <ErrorMessage className="toast-error" message={reportError} />}


        <div className="case-content">
          {/* The case's severity and where it stands, side by side; the overview under them. */}
          {activeSection === 'summary' && (
            <div className="case-summary-top">
              {snapshotLoaded ? <SeverityCard risk={risk} /> : <SeverityCardSkeleton />}

              <CaseStageCard
                caseId={selectedCase.id}
                stage={currentStage}
                failure={stageFailure}
                onChanged={(summary) => {
                  setStage(summary.status);
                  setStageFailure(summary.failure ?? null);
                  setFindingsToReview(summary.findings_to_review ?? null);
                }}
                onSelectSection={selectSection}
                analysisRunning={analysisOverview?.status === 'running'}
                caseLabel={selectedCase.caseNumber ?? undefined}
                findingsToReview={findingsToReview}
                missingRequirements={missingRequirements}
              />
            </div>
          )}

          {activeSection === 'summary' && (
            <CaseOverview
              reviewsRequired={reviewsRequired}
              documents={documents}
              flaggedDocuments={flaggedDocuments}
              approvedDocuments={approvedDocuments}
              caseMatches={caseMatches}
              risk={risk}
              analysisOverview={analysisOverview}
              snapshotLoaded={snapshotLoaded}
              caseId={selectedCase.id}
              onSelectSection={selectSection}
            />
          )}

          {activeSection === 'documents' && (
            <div className="upload-view">
              <section className="upload-main">
                {!isFinalised && (
                <>
                <h2 className="section-title">Upload supporting documents</h2>

                <p className="sub">
                  Accepted files are logged and assigned a document ID for the
                  investigation record.
                </p>

                {/* The drop target and what was chosen share one box: the prompt on top, the files under it, and
                    the upload (or its outcome) at the foot. */}
                <div className={`upload-box${selectedFiles.length > 0 ? ' upload-box--filled' : ''}`}>
                  <DropZone onFiles={handleFiles} disabled={isSubmitting} />

                  {selectedFiles.length > 0 && (
                    <>
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
                        {justSubmitted && hasOutcome && !error && !isSubmitting ? (
                          <span className={`toast show ${failedCount > 0 ? 'mixed' : 'all-ok'}`}>
                            <Icon name={failedCount > 0 ? 'alert' : 'check'} size={16} />
                            <span>
                              <b>
                                {successfulCount} of {selectedFiles.length} documents uploaded.
                              </b>{' '}
                              {failedCount > 0
                                ? `${failedCount} need attention.`
                                : 'All documents uploaded successfully.'}
                            </span>
                          </span>
                        ) : (
                          <span className="count">
                            {hasOutcome
                              ? `${completedCount} / ${selectedFiles.length} processed`
                              : `${selectedFiles.length} file${selectedFiles.length > 1 ? 's' : ''} selected`}
                          </span>
                        )}

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
                              : `Upload ${selectedFiles.length} file${selectedFiles.length > 1 ? 's' : ''}`}
                        </button>
                      </div>
                    </>
                  )}
                </div>

                {countError && <ErrorMessage className="toast-error" message={countError} />}

                {error && <ErrorMessage className="toast-error" message={error} />}
                </>
                )}

                <DocumentListPanel
                  extractedDocuments={extractedDocuments}
                  reviewReasons={reviewReasons}
                  fulfilledRequirements={fulfilledRequirements}
                  coverage={{ idDocument: idDocumentCoverage, proofOfAddress: proofOfAddressCoverage }}
                  approvedDocuments={approvedDocuments}
                  rejectedDocuments={rejectedDocuments}
                  wsConnected={wsConnected}
                  onOpenDocument={onOpenDocument}
                  selection={selection}
                  rowActions={rowActions}
                  readOnly={isFinalised}
                />
              </section>

            </div>
          )}

          {activeSection === 'risk' && (
            <CaseRiskCard
              risk={risk}
              caseId={selectedCase.id}
              caseDocuments={documents}
              readOnly={isFinalised}
              reviewsRequired={reviewsRequired}
            />
          )}

          {activeSection === 'analysis' && (
            <CaseAnalysisCard
              caseId={selectedCase.id}
              analysisReady={analysisReady && !isFinalised}
              overview={analysisOverview}
              missingRequirements={missingRequirements}
            />
          )}

          {/* Both come from the latest complete case analysis, so risk is set exactly when one exists. */}
          {activeSection === 'additional' && (
            <AdditionalDocumentsCard
              additionalDocuments={additionalDocuments}
              analysed={risk !== null}
            />
          )}

          {activeSection === 'notes' && <NotesCard finalised={isFinalised} caseId={selectedCase.id} caseNumber={selectedCase.caseNumber} documents={documents} />}

          {activeSection === 'audit' && <AuditTrailCard caseId={selectedCase.id} caseNumber={selectedCase.caseNumber} documents={documents} />}

          {activeSection === 'cost' && <CaseCostCard caseId={selectedCase.id} documents={documents} />}
        </div>
      </CaseFrame>

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
