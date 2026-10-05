import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import type { CaseRecord } from '../App';
import AppHeader from '../components/AppHeader';
import ConfirmDialog from '../components/ConfirmDialog';
import DocumentPreviewModal from '../components/DocumentPreviewModal';
import KebabMenu from '../components/KebabMenu';
import { subscribeToCaseStatus } from '../services/caseSocket';
import {
  deleteDocument,
  approveDocument,
  cancelDocument,
  canCancelDocument,
  canRetryDocument,
  rejectDocument,
  retryDocument,
  CLASSIFICATION_CONFIDENCE_THRESHOLD,
  EXTRACTION_CONFIDENCE_THRESHOLD,
  documentDetectionStage,
  formatDocumentType,
  isClassificationConfirmed,
  hasExtractedInformation,
  skipsDetection,
  getDocument,
  isConfirmedOther,
  getDocumentCaseMatch,
  getDetectionReport,
  cancelDetection,
  retryDetection,
  getLangGraphTrace,
  updateDocumentType,
  updateExtractedFields,
  runIndividualDetectionCheck,
} from '../services/uploadService';
import type {
  DetectionReport,
  DocumentCaseMatch,
  DocumentRecord,
  IndividualCheckId,
  LangGraphTrace,
} from '../services/uploadService';
import { ExtractedField, buildFieldDraft, displayableFieldEntries, isPlainObject, valueToString } from './document-details/fieldDisplay';
import { describeFailureReason } from './document-details/formatters';
import { DocumentSummaryCard } from './document-details/DocumentSummaryCard';
import { ExtractedInformationCard } from './document-details/ExtractedInformationCard';
import { ManualReviewCard } from './document-details/ManualReviewCard';

// Developer-only panels, so they load on demand.
const DetectionPanel = lazy(() =>
  import('./document-details/DetectionPanel').then((m) => ({ default: m.DetectionPanel })),
);
const LangGraphTracePanel = lazy(() =>
  import('./document-details/LangGraphTracePanel').then((m) => ({ default: m.LangGraphTracePanel })),
);
import { humanizeFieldKey } from '../utils/fieldLabels';

interface DocumentDetailsPageProps {
  selectedCase: CaseRecord;
  documentId: string;
  onBack: () => void;
  onDocumentDeleted: () => void;
}

export default function DocumentDetailsPage({
  selectedCase,
  documentId,
  onBack,
  onDocumentDeleted,
}: DocumentDetailsPageProps) {
  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [isAddFieldOpen, setIsAddFieldOpen] = useState(false);
  const [newFieldName, setNewFieldName] = useState('');
  const [newFieldValue, setNewFieldValue] = useState('');
  const [isAddingField, setIsAddingField] = useState(false);
  const [addFieldError, setAddFieldError] = useState<string | null>(null);

  const [deletingFieldId, setDeletingFieldId] = useState<string | null>(null);
  const [deleteFieldError, setDeleteFieldError] = useState<string | null>(null);
  
  const [fieldDraft, setFieldDraft] = useState<Record<string, string>>({});
  const [isSaving, setIsSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [selectedType, setSelectedType] = useState('');
  const [isSavingType, setIsSavingType] = useState(false);
  const [typeError, setTypeError] = useState<string | null>(null);

  // Debug aid, see backend/app/langgraph/trace.py.
  const [langGraphTrace, setLangGraphTrace] = useState<LangGraphTrace | null>(null);
  const [isRefreshingTrace, setIsRefreshingTrace] = useState(false);

  const refreshLangGraphTrace = async () => {
    setIsRefreshingTrace(true);
    try {
      const result = await getLangGraphTrace(documentId);
      setLangGraphTrace(result);
    } catch {
      // Dev-only aid — a fetch failure here shouldn't disturb the page.
    } finally {
      setIsRefreshingTrace(false);
    }
  };

  // Detection Agent: the stored report for this document.
  const [detectionReport, setDetectionReport] = useState<DetectionReport | null>(null);
  // Set when a run crashed or was cancelled.
  const [detectionError, setDetectionError] = useState<string | null>(null);

  const [isRefreshingDetection, setIsRefreshingDetection] = useState(false);

  const refreshDetectionReport = async () => {
    setIsRefreshingDetection(true);
    try {
      const report = await getDetectionReport(documentId);
      setDetectionReport(report);
      setDetectionError(null);
    } catch (err) {
      setDetectionError(err instanceof Error ? err.message : 'Detection failed.');
    } finally {
      setIsRefreshingDetection(false);
    }
  };

  const [isCancellingDetection, setIsCancellingDetection] = useState(false);

  const handleCancelDetection = async () => {
    setIsCancellingDetection(true);
    try {
      await cancelDetection(documentId);
      await refreshDetectionReport();
    } catch (err) {
      setDetectionError(err instanceof Error ? err.message : 'Failed to cancel detection.');
    } finally {
      setIsCancellingDetection(false);
    }
  };

  const [isRetryingDetection, setIsRetryingDetection] = useState(false);

  const handleRetryDetection = async () => {
    setIsRetryingDetection(true);
    try {
      await retryDetection(documentId);
      await refreshDetectionReport();
    } catch (err) {
      setDetectionError(err instanceof Error ? err.message : 'Failed to retry detection.');
    } finally {
      setIsRetryingDetection(false);
    }
  };

  // Individual checks: re-run one check, only once a full detection report exists.
  const [runningCheckId, setRunningCheckId] = useState<IndividualCheckId | null>(null);
  const [individualCheckError, setIndividualCheckError] = useState<string | null>(null);
  const [individualCheckResults, setIndividualCheckResults] = useState<
    Partial<Record<IndividualCheckId, unknown>>
  >({});

  const handleRunIndividualCheck = async (checkId: IndividualCheckId) => {
    setRunningCheckId(checkId);
    setIndividualCheckError(null);
    try {
      const result = await runIndividualDetectionCheck(documentId, checkId);
      setIndividualCheckResults((prev) => ({ ...prev, [checkId]: result }));
    } catch (err) {
      setIndividualCheckError(err instanceof Error ? err.message : 'Check failed.');
    } finally {
      setRunningCheckId(null);
    }
  };

  const [isApproving, setIsApproving] = useState(false);
  const [approveError, setApproveError] = useState<string | null>(null);

  const [isRejecting, setIsRejecting] = useState(false);
  const [rejectError, setRejectError] = useState<string | null>(null);

  const [isCancelling, setIsCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  const [isRetrying, setIsRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);

  // Forces a re-render every so often while PROCESSING so the "Stuck" badge/Retry button can appear.
  const [, forceStuckRecheck] = useState(0);
  const [isDeletingDocument, setIsDeletingDocument] = useState(false);
  const [deleteDocumentError, setDeleteDocumentError] = useState<string | null>(null);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  // Last extracted_fields the draft was built from.
  const extractedFieldsJsonRef = useRef('');

  useEffect(() => {
    let cancelled = false;

    async function loadDocument() {
      setIsLoading(true);
      setLoadError(null);

      try {
        const result = await getDocument(documentId);

        if (cancelled) {
          return;
        }

        setDocument(result);
        extractedFieldsJsonRef.current = JSON.stringify(
          result.extracted_fields ?? null,
        );
        setFieldDraft(buildFieldDraft(result.extracted_fields));
      } catch (error) {
        if (cancelled) {
          return;
        }

        setLoadError(
          error instanceof Error
            ? error.message
            : 'Failed to load document.',
        );
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    void loadDocument();

    return () => {
      cancelled = true;
    };
  }, [documentId]);

  // Whether this document belongs to the case's company and individuals (see backend/app/case_match.py).
  const [caseMatch, setCaseMatch] = useState<DocumentCaseMatch | null>(null);
  const hasCase = Boolean(document?.case_id);
  const documentStatus = document?.status;
  const isApproved = document?.status === 'approved';
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

  const canApprove =
    document?.status === 'analyzed' &&
    isTypeConfirmed &&
    hasExtractedInfo &&
    (document.missing_required_fields?.length ?? 0) === 0 &&
    lowConfidenceFields.length === 0;

  const missingRequiredFields = useMemo(
    () => document?.missing_required_fields ?? [],
    [document?.missing_required_fields],
  );
  const extractedFieldsJson = JSON.stringify(document?.extracted_fields ?? null);

  useEffect(() => {
    if (!hasCase || documentStatus !== 'analyzed') {
      setCaseMatch(null);
      return;
    }

    let cancelled = false;

    getDocumentCaseMatch(documentId)
      .then((result) => {
        if (!cancelled) setCaseMatch(result);
      })
      .catch(() => {
        // Informational only — the rest of the page works without it.
        if (!cancelled) setCaseMatch(null);
      });

    return () => {
      cancelled = true;
    };
  }, [documentId, hasCase, documentStatus, extractedFieldsJson]);

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

  const failureReason =
    document?.status === 'failed' && document.failure_reason
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
  const areFieldsVerified =
    document?.routing_status === 'accepted' &&
    document.extraction_confidence === 1 &&
    !needsManualReview;
  const canEditFields = !isLocked && !areFieldsVerified;

  // The type can only be corrected once the pipeline has finished with the document, and only while.
  const canCorrectType =
    !isLocked &&
    (document?.status === 'analyzed' || document?.status === 'failed') &&
    // Same rule as approval, so a document that can't be approved yet can always be confirmed.
    !isTypeConfirmed;

  // What the dropdown currently shows: the officer's pick, else the existing type.
  const isUnconfirmedUnrecognised =
    document?.document_type === 'unrecognised' && !isConfirmedOther(document);
  const chosenType =
    selectedType || (isUnconfirmedUnrecognised ? '' : document?.document_type) || '';

  // Saving a corrected type re-runs extraction in the background.
  const caseId = document?.case_id ?? null;
  const isInFlight =
    document?.status === 'uploaded' ||
    document?.status === 'queued' ||
    document?.status === 'processing' ||
    document?.status === 'extracting';

  const hasDetectionReport = detectionReport !== null;
  const detectionStage = document
    ? documentDetectionStage(document.status, hasDetectionReport, document.document_type)
    : null;
  const detectionSkipped = skipsDetection(document?.document_type);

  useEffect(() => {
    if (document?.status !== 'processing') {
      return;
    }
    const interval = window.setInterval(() => forceStuckRecheck((n) => n + 1), 15000);
    return () => window.clearInterval(interval);
  }, [document?.status]);

  useEffect(() => {
    if (!caseId || !isInFlight) {
      return;
    }

    const subscription = subscribeToCaseStatus(caseId, (snapshot) => {
      const updated = snapshot.documents.find(
        (candidate) => candidate.id === documentId,
      );

      if (!updated) {
        return;
      }

      const nextJson = JSON.stringify(updated.extracted_fields ?? null);

      if (nextJson !== extractedFieldsJsonRef.current) {
        extractedFieldsJsonRef.current = nextJson;
        setFieldDraft(buildFieldDraft(updated.extracted_fields));
      }

      setDocument(updated);
    });

    return () => subscription.unsubscribe();
  }, [caseId, isInFlight, documentId]);

  useEffect(() => {
    let cancelled = false;
    getDetectionReport(documentId)
      .then((report) => {
        if (!cancelled) setDetectionReport(report);
      })
      .catch((err) => {
        if (!cancelled) {
          setDetectionError(err instanceof Error ? err.message : 'Detection failed.');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  // Fetched once on load.
  useEffect(() => {
    let cancelled = false;
    getLangGraphTrace(documentId)
      .then((result) => {
        if (!cancelled) setLangGraphTrace(result);
      })
      .catch(() => {
        // Dev-only aid — a fetch failure here shouldn't disturb the page.
      });
    return () => {
      cancelled = true;
    };
  }, [documentId]);

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

  async function handleSaveType() {
    if (!document || !chosenType) {
      return;
    }

    setIsSavingType(true);
    setTypeError(null);

    try {
      const updated = await updateDocumentType(document.id, chosenType);

      extractedFieldsJsonRef.current = JSON.stringify(
        updated.extracted_fields ?? null,
      );
      setDocument(updated);
      setFieldDraft(buildFieldDraft(updated.extracted_fields));
      setSelectedType('');
      setSaveMessage(null);
      setSaveError(null);
    } catch (error) {
      setTypeError(
        error instanceof Error
          ? error.message
          : 'Failed to update the document type.',
      );
    } finally {
      setIsSavingType(false);
    }
  }

  function handleFieldChange(key: string, value: string) {
    setFieldDraft((current) => ({
      ...current,
      [key]: value,
    }));

    setSaveMessage(null);
    setSaveError(null);
  }

  async function handleSaveFields() {
    if (!document || !canEditFields) {
      return;
    }

    // Only fields that need attention must have a value.
    const hasEmptyField = reviewFields.some(
      ([key, field]) => needsAttention(key, field) && !fieldDraft[key]?.trim(),
    );

    if (hasEmptyField) {
      setSaveMessage(null);
      setSaveError('Please complete all fields before saving.');
      return;
    }

    setIsSaving(true);
    setSaveMessage(null);
    setSaveError(null);

    try {
      // fieldDraft is Record<string, string>.
      const originalValueByKey = new Map(
        extractedFields.map(([key, field]) => [key, field.value]),
      );
      const payload: Record<string, unknown> = {};
      const draftWithAbsent: Record<string, string> = {
        ...Object.fromEntries(absentRequiredFields.map((name) => [name, ''])),
        ...fieldDraft,
      };
      for (const [key, draftValue] of Object.entries(draftWithAbsent)) {
        if (draftValue.trim() === '') {
          payload[key] = null;
          continue;
        }
        const original = originalValueByKey.get(key);
        if (Array.isArray(original) || isPlainObject(original)) {
          try {
            payload[key] = JSON.parse(draftValue);
            continue;
          } catch {
            // Malformed edit.
          }
        }
        payload[key] = draftValue;
      }

      const updated = await updateExtractedFields(
        document.id,
        payload,
      );

      setDocument(updated);

      const nextDraft: Record<string, string> = {};

      for (const [key, field] of displayableFieldEntries(
        updated.extracted_fields,
      )) {
        nextDraft[key] = valueToString(field.value);
      }

      setFieldDraft(nextDraft);

      setSaveMessage(
        'Changes saved. The extracted fields are now verified and can no longer be edited.',
      );
    } catch (error) {
      setSaveError(
        error instanceof Error
          ? error.message
          : 'Failed to save extracted fields.',
      );
    } finally {
      setIsSaving(false);
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

  if (isLoading) {
    return (
      <div className="shell shell--upload">
        <AppHeader />

        <button type="button" className="back-link" onClick={onBack}>
          ← Back to case
        </button>

        <div className="document-details-loading">
          Loading document details…
        </div>
      </div>
    );
  }

  if (loadError || !document) {
    return (
      <div className="shell shell--upload">
        <AppHeader />

        <button type="button" className="back-link" onClick={onBack}>
          ← Back to case
        </button>

        <div className="document-details-error">
          {loadError ?? 'Document could not be found.'}
        </div>
      </div>
    );
  }

  return (
    <div className="shell shell--upload document-details-page">
      <AppHeader />

      <button type="button" className="back-link" onClick={onBack}>
        ← Back to case
      </button>

      <div className="eyebrow">
        Case #{selectedCase.caseNumber} · Document · {document.filename}
      </div>

      <div className="document-details-intro">
        <div className="document-details-title-row">
          <h1>Document Details</h1>
          <div className="document-details-title-actions">
            <KebabMenu
              label={isDeletingDocument ? 'Deleting document' : 'Document actions'}
              disabled={isDeletingDocument}
              items={[
                {
                  id: 'preview',
                  label: 'Preview document',
                  onSelect: () => setIsPreviewOpen(true),
                },
                ...(document.status !== 'approved' && !isInFlight
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
          </div>
        </div>
        <p className="sub">
          View extracted data and review any fields that require
          manual correction.
        </p>
        {deleteDocumentError && (
          <div className="toast show mixed" role="alert">
            ⚠ {deleteDocumentError}
          </div>
        )}
      </div>

      {/* Document summary */}

      <DocumentSummaryCard
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
        addFieldError={addFieldError}
        canEditFields={canEditFields}
        deleteFieldError={deleteFieldError}
        deletingFieldId={deletingFieldId}
        document={document}
        extractedFields={extractedFields}
        isAddFieldOpen={isAddFieldOpen}
        isAddingField={isAddingField}
        isInFlight={isInFlight}
        lowestConfidence={lowestConfidence}
        newFieldName={newFieldName}
        newFieldValue={newFieldValue}
        setAddFieldError={setAddFieldError}
        setDeleteFieldError={setDeleteFieldError}
        setDeletingFieldId={setDeletingFieldId}
        setDocument={setDocument}
        setFieldDraft={setFieldDraft}
        setIsAddFieldOpen={setIsAddFieldOpen}
        setIsAddingField={setIsAddingField}
        setNewFieldName={setNewFieldName}
        setNewFieldValue={setNewFieldValue}
      />

      {/* Manual review */}

      {canEditFields && (needsManualReview || isUnrecognisedDocument) && (
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

        {document.status !== 'approved' &&
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