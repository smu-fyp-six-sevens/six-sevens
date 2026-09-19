import { useEffect, useMemo, useRef, useState } from 'react';
import type { CaseRecord } from '../App';
import DropZone from '../components/DropZone';
import FileRow from '../components/FileRow';
import { subscribeToCaseStatus } from '../services/caseSocket';
import type { CaseStatusSubscription } from '../services/caseSocket';
import {
  OPTIONAL_DOCUMENT_TYPES,
  REQUIRED_DOCUMENT_TYPES,
  EXTRACTION_CONFIDENCE_THRESHOLD,
  updateExtractedFields,
  uploadDocuments,
} from '../services/uploadService';
import type { BulkUploadItemResult, DocumentRecord } from '../services/uploadService';


const TERMINAL_STATUSES = new Set(['analyzed', 'failed']);
import {
  MAX_BULK_FILES,
  MAX_FILE_SIZE_BYTES,
  checkFileCount,
  recordUploadedHash,
  validateFiles,
} from '../utils/fileValidation';
import type { FileIssue } from '../utils/fileValidation';

const formatDocumentType = (documentType?: string | null) => {
  if (!documentType) return "—";

  const formatted = documentType
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());

  return formatted.replace(/\bId\b/g, "ID").replace(/\bNric\b/g, "NRIC");
};

const MAX_FILE_SIZE_MB = Math.floor(MAX_FILE_SIZE_BYTES / (1024 * 1024));

const REQUIREMENT_LABELS = new Map(
  [...REQUIRED_DOCUMENT_TYPES, ...OPTIONAL_DOCUMENT_TYPES].map((requirement) => [
    requirement.value,
    requirement.label,
  ]),
);

interface UploadPageProps {
  selectedCase: CaseRecord;
  onBack: () => void;
  onOpenDocument: (documentId: string) => void;
}

function isExtractionNotConfiguredFailure(reason: string | null | undefined): boolean {
  return Boolean(reason?.startsWith('Extraction invocation failed'));
}

function formatRoutingOutcome(outcome: string | null | undefined): string {
  switch (outcome) {
    case 'accepted':
      return 'Proceed to fraud detection';
    case 'manual_review':
      return 'Flagged for manual review';
    default:
      return '—';
  }
}

// Extracted field keys are snake_case identifiers (e.g. "bank_name",
// "id_number") since they're also used as object keys sent back on save —
// this only affects how they're displayed, not the underlying data.
const FIELD_KEY_ACRONYMS = new Set(['id', 'uen', 'tin', 'nric']);

// Fields that carry structured data (arrays/objects) rather than a single
// display-able value — shown nowhere in this UI (no natural single-line or
// text-input representation), but still round-tripped untouched on save so
// editing an unrelated field never wipes them out (see saveFieldDraft).
const NON_DISPLAYED_FIELD_KEYS = new Set(['transactions']);

function displayableFieldEntries(fields: Record<string, unknown>): [string, unknown][] {
  return Object.entries(fields).filter(([key]) => !NON_DISPLAYED_FIELD_KEYS.has(key));
}

// True for a value the LLM returned as an array or nested object rather
// than a single scalar — e.g. multiple individual_name entries on a
// document with co-borrowers. String(value) on these renders as
// "[object Object]" (or a comma-joined run of them), so they need their
// own formatting (see formatFieldValue) and can't be a plain text input.
function isStructuredValue(value: unknown): boolean {
  return Array.isArray(value) || (typeof value === 'object' && value !== null);
}

// Renders any field value — including an array of scalars, an array of
// objects, or a single nested object — as a readable string instead of
// relying on String(value), which stringifies each object element via its
// default toString() ("[object Object]"). Recurses so an array of objects
// (e.g. multiple {name, id_number} entries) reads as
// "name: John Doe, id_number: S1234567A; name: Jane Doe, id_number: S7654321B".
function formatFieldValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) {
    return value.map((item) => formatFieldValue(item)).join('; ');
  }
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, nested]) => `${humanizeFieldKey(key)}: ${formatFieldValue(nested)}`)
      .join(', ');
  }
  return String(value);
}

// Displayable fields whose value is structured (array/object) — shown
// read-only even when the rest of the document's fields are editable,
// since flattening them into a single text input would be lossy on save.
function structuredFieldEntries(fields: Record<string, unknown>): [string, unknown][] {
  return displayableFieldEntries(fields).filter(([, value]) => isStructuredValue(value));
}

function humanizeFieldKey(key: string): string {
  return key
    .split('_')
    .filter(Boolean)
    .map((word) =>
      FIELD_KEY_ACRONYMS.has(word.toLowerCase())
        ? word.toUpperCase()
        : word.charAt(0).toUpperCase() + word.slice(1),
    )
    .join(' ');
}

function fieldsToDraft(fields: Record<string, unknown>): Record<string, string> {
  const draft: Record<string, string> = {};
  for (const [key, value] of displayableFieldEntries(fields)) {
    if (isStructuredValue(value)) continue;
    draft[key] = value === null || value === undefined ? '' : String(value);
  }
  return draft;
}

function describeIssue(issue: FileIssue): string {
  switch (issue.type) {
    case 'empty':
      return 'File is empty (0 bytes).';
    case 'too-large':
      return `File exceeds the ${MAX_FILE_SIZE_MB} MB limit.`;
    case 'invalid-signature':
      return 'File content does not match a supported type.';
    case 'invalid-filename':
      return 'Filename is invalid or unsafe.';
    case 'duplicate':
      return 'This exact file has already been uploaded for this case.';
  }
}

export default function UploadPage({
  selectedCase,
  onBack,
  onOpenDocument,
}: UploadPageProps) {
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

  const flaggedDocuments = Array.from(documentDetails.values()).filter(
    (document) =>
      document.classification_outcome === 'manual_review' ||
      (document.extraction_confidence != null &&
        document.extraction_confidence < EXTRACTION_CONFIDENCE_THRESHOLD),
  );

  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const hasOutcome = results.length > 0 || rejections.length > 0;

  // One WebSocket per case (see caseSocket.ts / backend routers/ws.py)
  // replaces the old per-document poll loop: the backend pushes every
  // document's status plus the case-level requirement checklist together
  // whenever anything changes, including documents uploaded after this
  // subscription opened — no separate per-upload poll kickoff needed.
  // Kept in a ref (rather than just returned from the effect) so
  // handleSubmit can call .refresh() after a successful upload — see its
  // docstring in caseSocket.ts for why that matters once a case goes idle.
  const caseSocketRef = useRef<CaseStatusSubscription | null>(null);
  useEffect(() => {
    setWsConnected(false);

    const subscription = subscribeToCaseStatus(
      selectedCase.id,
      ({ documents, caseSummary }) => {
        if (!isMountedRef.current) return;

        setDocumentDetails(new Map(documents.map((document) => [document.id, document])));
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

      // Wakes the backend's poll loop immediately instead of waiting out
      // its idle interval (up to 30s once a case has nothing in flight —
      // see IDLE_POLL_INTERVAL_SECONDS in routers/ws.py) — otherwise the
      // moment someone's most likely to be watching for feedback could be
      // the one moment it's slowest to arrive.
      if (response.results.some((result) => result.success)) {
        caseSocketRef.current?.refresh();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed.');
    } finally {
      setIsSubmitting(false);
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
    // "transactions") round-trip untouched instead of being dropped by a
    // save that only ever saw the displayable subset. Empty string means
    // "cleared by the officer" — send null rather than "" so it reads as
    // "not present" the same way an LLM-omitted field does.
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

  const completedCount = results.length + rejections.length;
  const successfulCount = results.filter((result) => result.success).length;
  const failedCount =
    results.filter((result) => !result.success).length + rejections.length;

  return (
    <main className="shell shell--upload">
      <header className="app-header">
        <div className="brand">
          GLDB <span>Fraud Review</span>
        </div>

        <div className="officer-tag">Loan Officer · J. Tan</div>
      </header>

      <div className="upload-view">
        <section className="upload-main">
          <button type="button" className="back-link" onClick={onBack}>
            ← Back to case queue
          </button>

          <p className="eyebrow">
            Case #{selectedCase.caseNumber} · {selectedCase.type} —{' '}
            {selectedCase.applicant}
          </p>

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

          {documentDetails.size > 0 && (
            <section className="documents-card">
              <div className="documents-card-header">
                <div className="documents-card-title">
                  <span className="documents-card-icon">▣</span>
                  <span>Extracted Files</span>
                </div>

                <div className="documents-card-actions">
                  <button
                    type="button"
                    className="approve-all-btn"
                    onClick={() => {
                      // Frontend-only for now.
                      // Backend approval logic can be connected later.
                    }}
                  >
                    Approve all eligible documents
                  </button>

                  <span
                    className={`mock-badge${wsConnected ? ' live' : ''}`}
                    title={
                      wsConnected
                        ? 'Connected — updates push in real time'
                        : 'Disconnected — reconnecting…'
                    }
                  >
                    {wsConnected ? 'LIVE' : 'OFFLINE'}
                  </span>
                </div>
              </div>

              <div className="documents-table">
                <div className="documents-table-header">
                  <span>Filename</span>
                  <span>Document Type</span>
                  <span>Status</span>
                  <span>Classification Confidence</span>
                  <span>Routing Outcome</span>
                  <span>Action</span>
                </div>

                {Array.from(documentDetails.values())
                  .sort((a, b) =>
                    a.uploaded_at.localeCompare(b.uploaded_at),
                  )
                  .map((document) => {
                    const status = document.status;
                    const isTerminal = TERMINAL_STATUSES.has(status);

                    return (
                      <div
                        key={document.id}
                        className="documents-table-row"
                      >
                        <span className="document-filename">
                          {document.classification_outcome ===
                            'manual_review' && (
                            <span
                              className="document-manual-review-icon"
                              title="Requires manual review"
                              aria-label="Requires manual review"
                            >
                              ⚠
                            </span>
                          )}

                          {document.filename}
                        </span>

                        <span className="document-type-cell">
                          {formatDocumentType(document.document_type)}
                        </span>

                        <span className="document-status-cell">
                          <span
                            className={`status-dot ${
                              status === 'analyzed'
                                ? 'status-dot--success'
                                : status === 'failed'
                                  ? 'status-dot--failed'
                                  : ''
                            }`}
                          />

                          <span>
                            {status}
                            {!isTerminal && '…'}
                          </span>
                        </span>

                        <span className="document-confidence">
                          {document.confidence != null
                            ? document.confidence.toFixed(2)
                            : '—'}
                        </span>

                        <span className="document-routing">
                          {document.classification_outcome ?? '—'}
                        </span>

                        <button
                          type="button"
                          className="document-view-link"
                          onClick={() =>
                            onOpenDocument(document.id)
                          }
                        >
                          View
                        </button>
                      </div>
                    );
                  })}
              </div>
            </section>
          )}

          <section className="summary-card">
            <div className="summary-card-header">
              <div className="summary-card-title">
                <span className="summary-icon summary-icon--risk">
                  ▥
                </span>
                <h2>Customer Risk Summary</h2>
              </div>
            </div>

            <div className="summary-message summary-message--info">
              <span className="summary-message-icon">ⓘ</span>

              <div>
                <strong>Risk summary not available yet.</strong>

                <p>
                  Risk analysis results will appear here once document
                  extraction and fraud analysis are completed.
                </p>
              </div>
            </div>
          </section>

          <section className="summary-card">
            <div className="summary-card-header">
              <div className="summary-card-title">
                <span className="summary-icon summary-icon--warning">
                  △
                </span>
                <h2>Flagged Items for Manual Review</h2>
              </div>

              {flaggedDocuments.length > 0 && (
                <span className="summary-count">
                  {flaggedDocuments.length}{' '}
                  {flaggedDocuments.length === 1 ? 'item' : 'items'}
                </span>
              )}
            </div>

            {flaggedDocuments.length === 0 ? (
              <div className="summary-message summary-message--warning">
                <span className="summary-message-icon">ⓘ</span>

                <div>
                  <strong>No flagged items at the moment.</strong>

                  <p>
                    If any documents or extracted fields require manual
                    review, they will be displayed here.
                  </p>
                </div>
              </div>
            ) : (
              <div className="flagged-list">
                {flaggedDocuments.map((document) => {
                  const lowExtractionConfidence =
                    document.extraction_confidence != null &&
                    document.extraction_confidence <
                      EXTRACTION_CONFIDENCE_THRESHOLD;

                  const reason =
                    document.classification_outcome === 'manual_review'
                      ? document.escalation_reason ??
                        (document.document_type === 'unrecognised'
                          ? 'Document type could not be confidently recognised.'
                          : lowExtractionConfidence
                            ? 'Extracted fields have low confidence and require review.'
                            : 'Document requires manual review.')
                      : 'Extracted fields have low confidence and require review.';

                  return (
                    <div
                      key={document.id}
                      className="flagged-item"
                    >
                      <div className="flagged-item-main">
                        <strong>{document.filename}</strong>

                        <div className="flagged-item-meta">
                          <span>
                            {document.document_type ??
                              'Unknown document type'}
                          </span>

                          {document.extraction_confidence != null && (
                            <span>
                              Extraction confidence:{' '}
                              {document.extraction_confidence.toFixed(
                                2,
                              )}
                            </span>
                          )}

                          {document.classification_outcome ===
                            'manual_review' && (
                            <span className="flagged-status">
                              Manual review
                            </span>
                          )}
                        </div>

                        <p>{reason}</p>
                      </div>

                      <button
                        type="button"
                        className="flagged-view-button"
                        onClick={() =>
                          onOpenDocument(document.id)
                        }
                      >
                        View
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </section>

          <section className="summary-card">
            <div className="summary-card-header">
              <div className="summary-card-title">
                <span className="summary-icon summary-icon--audit">
                  ◴
                </span>
                <h2>Case Audit Trail</h2>
              </div>
            </div>

            <div className="summary-message summary-message--info">
              <span className="summary-message-icon">ⓘ</span>

              <div>
                <strong>No audit events available yet.</strong>

                <p>
                  Case activity history will appear here once actions
                  are recorded.
                </p>
              </div>
            </div>
          </section>
        </section>

        <aside className="upload-sidebar">
          <div className="section-label">
            <span>Document requirements</span>

            <span>
              {
                REQUIRED_DOCUMENT_TYPES.filter((requirement) =>
                  fulfilledRequirements.has(requirement.value),
                ).length
              }{' '}
              / {REQUIRED_DOCUMENT_TYPES.length}
            </span>
          </div>

          <ul className="requirements-list">
            {REQUIRED_DOCUMENT_TYPES.map((requirement) => {
              const isFulfilled =
                fulfilledRequirements.has(requirement.value);

              // proof_of_address is the one requirement that isn't just
              // yes/no — it needs an address on file per identified
              // individual (one per ID document), not just once anywhere
              // in the case. Shown as a fraction once there's at least one
              // identity to count; before that there's nothing to divide.
              const isProofOfAddress =
                requirement.value === 'proof_of_address';

              const showProgress =
                isProofOfAddress &&
                proofOfAddressCoverage.total > 0;

              const unmatchedNames = isProofOfAddress
                ? proofOfAddressCoverage.unmatched_names
                : [];

              // id_document needs both a front and a back capture (see
              // IdDocumentCoverage/compute_id_document_side_coverage) — an
              // NRIC's back alone has no name or photo, so without this a
              // back-only upload just silently sat unfulfilled with no
              // indication why. Shown as a fraction once at least one side
              // has been seen; the specific missing side is called out
              // separately so it reads as "waiting on the front", not just
              // "incomplete".
              const isIdDocument = requirement.value === 'id_document';

              const showIdProgress =
                isIdDocument && idDocumentCoverage.total > 0;

              // missing_side is only populated when there's exactly one
              // identified individual in the case (see
              // mapper.compute_id_document_missing_side) — with more than
              // one identity, a single hint can't describe each person's
              // own gap, so it falls back to just the fraction above.
              const idDocumentHint =
                isIdDocument && idDocumentCoverage.missing_side
                  ? idDocumentCoverage.missing_side === 'front'
                    ? 'Back photo present, front missing.'
                    : 'Front photo present, back missing.'
                  : null;

              return (
                <li key={requirement.value}>
                  <div
                    className={`requirement-row${
                      isFulfilled ? ' fulfilled' : ''
                    }`}
                  >
                    <span className="requirement-dot" />

                    {requirement.label}

                    {showProgress && (
                      <span className="requirement-progress">
                        {' '}
                        ({proofOfAddressCoverage.covered}/
                        {proofOfAddressCoverage.total})
                      </span>
                    )}

                    {showIdProgress && (
                      <span className="requirement-progress">
                        {' '}
                        ({idDocumentCoverage.covered}/
                        {idDocumentCoverage.total})
                      </span>
                    )}
                  </div>

                  {unmatchedNames.length > 0 && (
                    <div className="requirement-unmatched">
                      Address on file but no matching ID uploaded:{' '}
                      {unmatchedNames.join(', ')}
                    </div>
                  )}

                  {idDocumentHint && (
                    <div className="requirement-unmatched">{idDocumentHint}</div>
                  )}
                </li>
              );
            })}

            {OPTIONAL_DOCUMENT_TYPES.map((requirement) => (
              <li key={requirement.value}>
                <div
                  className={`requirement-row${
                    fulfilledRequirements.has(requirement.value)
                      ? ' fulfilled'
                      : ''
                  }`}
                >
                  <span className="requirement-dot" />

                  {requirement.label}

                  <span className="requirement-optional-tag">Optional</span>
                </div>
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </main>
  );
}