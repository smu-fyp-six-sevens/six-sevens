import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import type { CaseRecord } from '../App';
import AppHeader from '../components/AppHeader';
import CaseHeader, { CaseTitle } from '../components/CaseHeader';
import { caseCrumb, caseQueueCrumb, documentCrumb } from '../components/caseCrumbs';
import ConfirmDialog from '../components/ConfirmDialog';
import DocumentPreviewModal from '../components/DocumentPreviewModal';
import DocumentPreviewPane, { ShowDocumentButton } from '../components/DocumentPreviewPane';
import KebabMenu from '../components/KebabMenu';
import { DocumentBodySkeleton } from '../components/Skeletons';
import {
  canCancelDocument,
  canRetryDocument,
  CLASSIFICATION_CONFIDENCE_THRESHOLD,
  EXTRACTION_CONFIDENCE_THRESHOLD,
  documentDetectionStage,
  formatDocumentType,
  hasBeenApproved,
  hasExtractedInformation,
  isBlockedByContentGuard,
  isClassificationConfirmed,
  skipsDetection,
} from '../services/uploadService';
import { displayableFieldEntries } from './document-details/fieldDisplay';
import type { ExtractedField } from './document-details/fieldDisplay';
import { describeFailureReason } from './document-details/formatters';
import { DocumentSummaryCard } from './document-details/DocumentSummaryCard';
import { ExtractedInformationCard } from './document-details/ExtractedInformationCard';
import { ManualReviewCard } from './document-details/ManualReviewCard';
import { NotesCard } from '../components/NotesCard';
import { useDetection } from './document-details/useDetection';
import { useDocumentActions } from './document-details/useDocumentActions';
import { useDocumentCaseMatch } from './document-details/useDocumentCaseMatch';
import { useDocumentRecord } from './document-details/useDocumentRecord';
import { useDocumentTypeEditing } from './document-details/useDocumentTypeEditing';
import { useFieldEditing } from './document-details/useFieldEditing';
import { useLangGraphTrace } from './document-details/useLangGraphTrace';
import { useContentGuard } from './document-details/useContentGuard';
import { ContentGuardWarning } from './document-details/ContentGuardWarning';
import { humanizeFieldKey } from '../utils/fieldLabels';

// Developer-only panels, so they load on demand.
const DetectionPanel = lazy(() =>
  import('./document-details/DetectionPanel').then((m) => ({ default: m.DetectionPanel })),
);
const LangGraphTracePanel = lazy(() =>
  import('./document-details/LangGraphTracePanel').then((m) => ({ default: m.LangGraphTracePanel })),
);

// Whether the side-by-side preview is shown, remembered per browser. Storage can throw (private mode,
// blocked site data), so it falls back to shown.
const PREVIEW_PANE_STORAGE_KEY = 'documentDetails.previewPaneOpen';

function loadPreviewPaneOpen(): boolean {
  try {
    return localStorage.getItem(PREVIEW_PANE_STORAGE_KEY) !== 'false';
  } catch {
    return true;
  }
}

function savePreviewPaneOpen(open: boolean) {
  try {
    localStorage.setItem(PREVIEW_PANE_STORAGE_KEY, String(open));
  } catch {
    // Not remembering is fine: the toggle still works for this visit.
  }
}

interface DocumentDetailsPageProps {
  selectedCase: CaseRecord;
  documentId: string;
  onDocumentDeleted: () => void;
}

export default function DocumentDetailsPage({
  selectedCase,
  documentId,
  onDocumentDeleted,
}: DocumentDetailsPageProps) {
  const {
    document,
    setDocument,
    isLoading,
    loadError,
    fieldDraft,
    setFieldDraft,
    extractedFieldsJsonRef,
    isInFlight,
  } = useDocumentRecord(documentId);
  const caseMatch = useDocumentCaseMatch(documentId, document);
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  const [isPreviewPaneOpen, setIsPreviewPaneOpen] = useState(loadPreviewPaneOpen);
  const togglePreviewPane = (open: boolean) => {
    setIsPreviewPaneOpen(open);
    savePreviewPaneOpen(open);
  };

  const isApproved = hasBeenApproved(document?.status);
  // Approve and Reject are both final: the document is locked against field edits and type corrections.
  const isLocked = isApproved || document?.status === 'rejected';
  // Fields still under the threshold haven't been reviewed: "Save fields" marks every reviewed field.
  const lowConfidenceFields = useMemo(
    () =>
      Object.entries(document?.extracted_fields ?? {})
        .filter(([, raw]) => {
          const confidence =
            raw && typeof raw === 'object' ? (raw as { confidence?: unknown }).confidence : null;
          return typeof confidence === 'number' && confidence < EXTRACTION_CONFIDENCE_THRESHOLD;
        })
        .map(([fieldName]) => fieldName)
        .sort(),
    [document?.extracted_fields],
  );

  const isTypeConfirmed = document ? isClassificationConfirmed(document) : false;

  const hasExtractedInfo = hasExtractedInformation(document?.extracted_fields ?? null);

  // An Other document's fields come from no schema, so an officer has to save them before
  // approval, however confident Extraction was. Every other type only needs its flagged fields saved.
  const isOtherDocument = document?.document_type === 'unrecognised';
  const otherFieldsUnsaved = isOtherDocument && !document?.fields_reviewed_at;

  const canApprove =
    document?.status === 'analyzed' &&
    isTypeConfirmed &&
    hasExtractedInfo &&
    (document.missing_required_fields?.length ?? 0) === 0 &&
    lowConfidenceFields.length === 0 &&
    !otherFieldsUnsaved;

  const missingRequiredFields = useMemo(
    () => document?.missing_required_fields ?? [],
    [document?.missing_required_fields],
  );

  const caseMatchMismatches = useMemo(
    () => caseMatch?.comparisons.filter((comparison) => comparison.outcome === 'mismatch') ?? [],
    [caseMatch],
  );

  const extractedFields = useMemo(
    () => displayableFieldEntries(document?.extracted_fields ?? null),
    [document?.extracted_fields],
  );

  const isUnrecognisedDocument =
    !document?.document_type ||
    document.document_type.toLowerCase() === 'unrecognised';

  // A field needs the officer's attention when the backend says it's a required field that's missing.
  const needsAttention = (key: string, field: ExtractedField) =>
    isUnrecognisedDocument ||
    missingRequiredFields.includes(key) ||
    (field.confidence != null && field.confidence < EXTRACTION_CONFIDENCE_THRESHOLD);

  // Required fields Extraction didn't return at all, shown as empty rows so the officer can fill them in.
  const absentRequiredFields = useMemo(
    () => missingRequiredFields.filter((name) => !extractedFields.some(([key]) => key === name)),
    [missingRequiredFields, extractedFields],
  );
  const reviewFields = useMemo<[string, ExtractedField][]>(
    () => [
      ...extractedFields,
      ...absentRequiredFields.map((name): [string, ExtractedField] => [
        name,
        { value: null, confidence: null },
      ]),
    ],
    [extractedFields, absentRequiredFields],
  );

  // No manual review or added fields on a document the content guard stopped: no model read it.
  const isBlocked = document ? isBlockedByContentGuard(document) : false;

  // A blocked document's reason is the content guard warning at the top of the page, so it isn't repeated here.
  const failureReason =
    document?.status === 'failed' && document.failure_reason && !isBlocked
      ? describeFailureReason(document.failure_reason)
      : null;

  // Distinct from failureReason (a hard processing failure) and from needsManualReview below.
  const lowClassificationConfidence =
    document?.classification_confidence != null &&
    document.classification_confidence < CLASSIFICATION_CONFIDENCE_THRESHOLD;

  // Manual review is driven by the lowest confidence among this document's own extracted fields.
  const lowestConfidence = useMemo(() => {
    const values = extractedFields
      .map(([, field]) => field.confidence)
      .filter((confidence): confidence is number => confidence != null);
    return values.length > 0 ? Math.min(...values) : null;
  }, [extractedFields]);

  // A required field reading as blank doesn't lower lowestConfidence.
  const needsManualReview =
    (lowestConfidence != null && lowestConfidence < EXTRACTION_CONFIDENCE_THRESHOLD) ||
    missingRequiredFields.length > 0;

  // Set once a loan officer has clicked "Save fields": the PATCH endpoint writes.
  // Extraction can report 1.0 too, so an Other document is only verified once it has been saved.
  const areFieldsVerified = isOtherDocument
    ? !otherFieldsUnsaved
    : document?.routing_status === 'accepted' &&
      document.extraction_confidence === 1 &&
      !needsManualReview;
  const canEditFields = !isLocked && !areFieldsVerified;

  // The type can only be corrected once the pipeline has finished with the document. Same rule as
  // approval, so a document that can't be approved yet can always be confirmed.
  const canCorrectType =
    !isLocked &&
    !isBlocked &&
    (document?.status === 'analyzed' || document?.status === 'failed') &&
    !isTypeConfirmed;

  const fieldEditing = useFieldEditing({
    document,
    setDocument,
    fieldDraft,
    setFieldDraft,
    canEditFields,
    extractedFields,
    reviewFields,
    absentRequiredFields,
    needsAttention,
  });
  const { isSaving, saveMessage, saveError, handleFieldChange, handleSaveFields, clearSaveMessages } = fieldEditing;

  const {
    setSelectedType,
    isSavingType,
    typeError,
    setTypeError,
    chosenType,
    isUnconfirmedUnrecognised,
    handleSaveType,
  } = useDocumentTypeEditing({
    document,
    setDocument,
    setFieldDraft,
    extractedFieldsJsonRef,
    clearSaveMessages,
  });

  const {
    detectionReport,
    detectionError,
    isRefreshingDetection,
    refreshDetectionReport,
    isCancellingDetection,
    cancelDetectionError,
    handleCancelDetection,
    isRetryingDetection,
    handleRetryDetection,
    runningCheckId,
    individualCheckError,
    individualCheckResults,
    handleRunIndividualCheck,
  } = useDetection(documentId, document?.status);

  const { langGraphTrace, isRefreshingTrace, refreshLangGraphTrace } = useLangGraphTrace(documentId);

  const contentGuard = useContentGuard(documentId, document?.status);

  // The case-status websocket (useDocumentRecord) stays subscribed while status is 'detecting', so this
  // fires live the moment the server moves it to 'detected' (or back to 'approved' on a failed run) —
  // no manual refresh needed to see the finished report and trace.
  const previousDetectionStatusRef = useRef<string | null>(null);
  useEffect(() => {
    const previousStatus = previousDetectionStatusRef.current;
    previousDetectionStatusRef.current = document?.status ?? null;
    if (previousStatus === 'detecting' && document?.status !== 'detecting') {
      void refreshDetectionReport();
      void refreshLangGraphTrace();
    }
  }, [document?.status, refreshDetectionReport, refreshLangGraphTrace]);

  const {
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
  } = useDocumentActions({ document, setDocument, onDocumentDeleted });

  const hasDetectionReport = detectionReport !== null;
  const detectionStage = document
    ? documentDetectionStage(document.status, hasDetectionReport, document.document_type, document.has_detection_error ?? false)
    : null;
  const detectionSkipped = skipsDetection(document?.document_type);

  const caseHeader = (
    <CaseHeader
      crumbs={[
        caseQueueCrumb,
        caseCrumb(selectedCase),
        // The filename is known once the document has loaded.
        ...(document ? [documentCrumb(selectedCase, documentId, document.filename)] : []),
      ]}
    >
      <CaseTitle caseRecord={selectedCase} />
    </CaseHeader>
  );

  if (isLoading) {
    return (
      <div className="shell shell--upload document-details-page">
        <AppHeader />

        {caseHeader}

        <DocumentBodySkeleton />
      </div>
    );
  }

  if (loadError || !document) {
    return (
      <div className="shell shell--upload document-details-page">
        <AppHeader />

        {caseHeader}

        <div className="document-details-body">
          <div className="document-details-error">
            {loadError ?? 'Document could not be found.'}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="shell shell--upload document-details-page">
      <AppHeader />

      {caseHeader}

      <div
        className={`document-details-body document-details-body--split${
          isPreviewPaneOpen ? '' : ' document-details-body--preview-hidden'
        }`}
      >
        {/* The document on the left, kept in view while the fields on the right scroll, so each value
            can be checked against the page. Hidden, "Show document" in the header brings it back. */}
        <div className="document-details-split">
        {isPreviewPaneOpen && (
          <DocumentPreviewPane
            documentId={document.id}
            filename={document.filename}
            onExpand={() => setIsPreviewOpen(true)}
            onCollapse={() => togglePreviewPane(false)}
          />
        )}

        <div className="document-details-main">
        {deleteDocumentError && (
          <div className="toast show mixed" role="alert">
            ⚠ {deleteDocumentError}
          </div>
        )}

        <ContentGuardWarning contentGuard={contentGuard} />

        {/* Notes: open on a locked document too, since a note never edits the document. */}

        <NotesCard
          caseId={selectedCase.id}
          caseNumber={selectedCase.caseNumber}
          documents={[{ id: document.id, filename: document.filename }]}
          documentId={document.id}
          lockedNotice={
            isLocked
              ? `Document ${isApproved ? 'approved' : 'rejected'} and locked · notes don't change it`
              : undefined
          }
        />

        {/* Document summary */}

        <DocumentSummaryCard
          headerActions={
            <>
            {!isPreviewPaneOpen && <ShowDocumentButton onClick={() => togglePreviewPane(true)} />}
            <KebabMenu
              label={isDeletingDocument ? 'Deleting document' : 'Document actions'}
              disabled={isDeletingDocument}
              items={[
                {
                  id: 'preview',
                  label: 'Full-screen preview',
                  onSelect: () => setIsPreviewOpen(true),
                },
                ...(!hasBeenApproved(document.status) && !isInFlight
                  ? [
                      {
                        id: 'delete',
                        label: isDeletingDocument ? 'Deleting…' : 'Delete document',
                        danger: true,
                        disabled: isDeletingDocument,
                        onSelect: () => setDeleteConfirmOpen(true),
                      },
                    ]
                  : []),
              ]}
            />
            </>
          }
          canCorrectType={canCorrectType}
          caseMatch={caseMatch}
          caseMatchMismatches={caseMatchMismatches}
          chosenType={chosenType}
          detectionStage={detectionStage}
          document={document}
          failureReason={failureReason}
          handleSaveType={handleSaveType}
          hasDetectionReport={hasDetectionReport}
          isSavingType={isSavingType}
          isUnconfirmedUnrecognised={isUnconfirmedUnrecognised}
          lowClassificationConfidence={lowClassificationConfidence}
          setSelectedType={setSelectedType}
          setTypeError={setTypeError}
          typeError={typeError}
        />

        {/* Extracted information */}

        <ExtractedInformationCard
          canAddOrDeleteFields={!isLocked && !isBlocked}
          document={document}
          extractedFields={extractedFields}
          isInFlight={isInFlight}
          lowestConfidence={lowestConfidence}
          setDocument={setDocument}
          setFieldDraft={setFieldDraft}
          editing={fieldEditing}
        />

        {/* Manual review */}

        {/* Not while a run is in flight: its fields are about to be replaced, and a retry of a blocked document
            clears the failure for a moment, which would flash the card up and away again. */}
        {canEditFields && !isBlocked && !isInFlight && (needsManualReview || isUnrecognisedDocument) && (
          <ManualReviewCard
            canEditFields={canEditFields}
            fieldDraft={fieldDraft}
            handleFieldChange={handleFieldChange}
            handleSaveFields={handleSaveFields}
            isInFlight={isInFlight}
            isSaving={isSaving}
            needsAttention={needsAttention}
            reviewFields={reviewFields}
            saveError={saveError}
            saveMessage={saveMessage}
          />
        )}

        {/* LangGraph trace (debug) */}

        <Suspense fallback={null}>
          <LangGraphTracePanel
            langGraphTrace={langGraphTrace}
            isRefreshingTrace={isRefreshingTrace}
            refreshLangGraphTrace={refreshLangGraphTrace}
          />
        </Suspense>

        {/* Detection Agent (dev aid) */}

        <Suspense fallback={null}>
          <DetectionPanel
            status={document.status}
            detectionError={detectionError}
            detectionReport={detectionReport}
            detectionSkipped={detectionSkipped}
            isApproved={isApproved}
            isRetryingDetection={isRetryingDetection}
            isCancellingDetection={isCancellingDetection}
            cancelError={cancelDetectionError}
            isRefreshingDetection={isRefreshingDetection}
            refreshDetectionReport={refreshDetectionReport}
            handleRetryDetection={handleRetryDetection}
            handleCancelDetection={handleCancelDetection}
            runningCheckId={runningCheckId}
            individualCheckError={individualCheckError}
            individualCheckResults={individualCheckResults}
            handleRunIndividualCheck={handleRunIndividualCheck}
          />
        </Suspense>

        {/* Document actions */}

          {approveError && (
            <div className="save-error">{approveError}</div>
          )}
          {cancelError && (
            <div className="save-error">{cancelError}</div>
          )}
          {rejectError && (
            <div className="save-error">{rejectError}</div>
          )}
          {retryError && (
            <div className="save-error">{retryError}</div>
          )}

          {!hasBeenApproved(document.status) &&
          document.status !== 'rejected' && (
            <div className="document-actions">
              {canCancelDocument(document.status) && (
                <button
                  type="button"
                  className="document-action--cancel"
                  disabled={isCancelling}
                  onClick={() => void handleCancelDocument()}
                >
                  {isCancelling ? 'Cancelling…' : '⏹ Cancel Processing'}
                </button>
              )}

              {canRetryDocument(document) && (
                <button
                  type="button"
                  className="document-action--retry"
                  disabled={isRetrying}
                  onClick={() => void handleRetryDocument()}
                >
                  {isRetrying ? 'Retrying…' : '↻ Retry Document'}
                </button>
              )}

              {document.status === 'analyzed' && !isTypeConfirmed && (
                <div className="approval-warning">
                  Cannot approve yet.{' '}
                  {!document.document_type || document.document_type === 'unrecognised'
                    ? 'The document type could not be identified.'
                    : `The classifier is only ${Math.round(
                        (document.classification_confidence ?? 0) * 100,
                      )}% sure this is a ${formatDocumentType(document.document_type, null)}.`}{' '}
                  Choose the correct type in <strong>Document Type</strong> above and save it before
                  approving, or reject the document if it doesn't belong in this case.
                </div>
              )}

              {document.status === 'analyzed' && !hasExtractedInfo && (
                <div className="approval-warning">
                  Cannot approve yet. No information was extracted from this document. Add the
                  fields manually, or reject it if it doesn't belong in this case.
                </div>
              )}

              {document.status === 'analyzed' && missingRequiredFields.length > 0 && (
                <div className="approval-warning">
                  Cannot approve yet. Please complete the following required field
                  {missingRequiredFields.length > 1 ? 's' : ''}:{' '}
                  <strong>
                    {missingRequiredFields
                      .map((fieldName) => humanizeFieldKey(fieldName))
                      .join(', ')}
                  </strong>
                </div>
              )}

              {document.status === 'analyzed' && lowConfidenceFields.length > 0 && (
                <div className="approval-warning">
                  Cannot approve yet. {lowConfidenceFields.length > 1 ? 'These fields have' : 'This field has'}{' '}
                  low confidence:{' '}
                  <strong>
                    {lowConfidenceFields
                      .map((fieldName) => humanizeFieldKey(fieldName))
                      .join(', ')}
                  </strong>
                  . Check {lowConfidenceFields.length > 1 ? 'them' : 'it'} in Manual Review and press{' '}
                  <strong>Save fields</strong> before approving.
                </div>
              )}

              {document.status === 'analyzed' && isTypeConfirmed && otherFieldsUnsaved && (
                <div className="approval-warning">
                  Cannot approve yet. This document is set to <strong>Other</strong>, so every field
                  needs your review. Check them in Manual Review and press <strong>Save fields</strong>{' '}
                  before approving.
                </div>
              )}

              <div className="document-actions-row">
                <button
                  type="button"
                  className="document-action--reject"
                  disabled={isRejecting || isApproving}
                  onClick={() => void handleRejectDocument()}
                >
                  {isRejecting ? 'Rejecting…' : '✕ Reject Document'}
                </button>

                <button
                  type="button"
                  className="document-action--approve"
                  disabled={isApproving || !canApprove}
                  onClick={() => void handleApproveDocument()}
                >
                  {isApproving ? 'Approving…' : '✓ Approve Document'}
                </button>
              </div>
            </div>
          )}
        </div>
        </div>
      </div>

      {deleteConfirmOpen && document && (
        <ConfirmDialog
          title="Delete this document?"
          message={`${document.filename} will be removed from this case and cannot be recovered.`}
          isConfirming={isDeletingDocument}
          onCancel={() => {
            if (!isDeletingDocument) setDeleteConfirmOpen(false);
          }}
          onConfirm={() => void confirmDeleteDocument()}
        />
      )}

      {isPreviewOpen && document && (
        <DocumentPreviewModal
          documentId={document.id}
          filename={document.filename}
          onClose={() => setIsPreviewOpen(false)}
        />
      )}
    </div>
  );
}