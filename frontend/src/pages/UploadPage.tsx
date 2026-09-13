import { useEffect, useMemo, useRef, useState } from 'react';
import type { CaseRecord } from '../App';
import DropZone from '../components/DropZone';
import FileRow from '../components/FileRow';
import {
  REQUIRED_DOCUMENT_TYPES,
  EXTRACTION_CONFIDENCE_THRESHOLD,
  getCaseRequirementCoverage,
  getDocument,
  updateExtractedFields,
  listDocuments,
  uploadDocuments,
} from '../services/uploadService';
import type { BulkUploadItemResult, DocumentRecord } from '../services/uploadService';

const TERMINAL_STATUSES = new Set(['analyzed', 'failed']);
const POLL_INTERVAL_MS = 2000;
import {
  MAX_BULK_FILES,
  MAX_FILE_SIZE_BYTES,
  checkFileCount,
  recordUploadedHash,
  validateFiles,
} from '../utils/fileValidation';
import type { FileIssue } from '../utils/fileValidation';

const MAX_FILE_SIZE_MB = Math.floor(MAX_FILE_SIZE_BYTES / (1024 * 1024));

const REQUIREMENT_LABELS = new Map(
  REQUIRED_DOCUMENT_TYPES.map((requirement) => [requirement.value, requirement.label]),
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
  const [proofOfAddressCoverage, setProofOfAddressCoverage] = useState({ covered: 0, total: 0 });
  const [fieldDrafts, setFieldDrafts] = useState<Map<string, Record<string, string>>>(new Map());
  const [savingFields, setSavingFields] = useState<Set<string>>(new Set());
  const [saveFieldErrors, setSaveFieldErrors] = useState<Map<string, string>>(new Map());
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

  // Fetched from the backend rather than unioned client-side from each
  // document's own fulfilled_requirements — proof_of_address specifically
  // requires every identified individual (every id_document) to have an
  // address on file, not just one anywhere in the case, and that
  // identity-matching logic lives in case_status.py, not here. See
  // getCaseRequirementCoverage's docstring in uploadService.ts.
  async function refreshRequirementCoverage() {
    try {
      const coverage = await getCaseRequirementCoverage(selectedCase.id);
      if (isMountedRef.current) {
        setFulfilledRequirements(new Set(coverage.fulfilled_requirements));
        setProofOfAddressCoverage(coverage.proof_of_address_coverage);
      }
    } catch {
      // best-effort refresh — leave the last-known checklist state on screen
    }
  }

  async function pollDocument(documentId: string) {
    // No attempt cap — how long classification actually takes depends on
    // how many other documents are ahead of it (the classifier serializes
    // calls, see DIFY_CLASSIFIER_MAX_CONCURRENCY on the backend), so a
    // fixed timeout just goes stale under load instead of ever being
    // "long enough". Keeps polling for as long as this page stays mounted.
    while (isMountedRef.current) {
      let document: DocumentRecord | null = null;
      try {
        document = await getDocument(documentId);
      } catch {
        // transient fetch failure — skip this cycle rather than giving up
        // on the document for the rest of the session
      }

      if (!isMountedRef.current) return;

      if (document) {
        setDocumentDetails((current) => new Map(current).set(documentId, document as DocumentRecord));

        if (TERMINAL_STATUSES.has(document.status)) {
          // Requirement coverage (esp. proof_of_address) can only be known
          // for certain once this document reaches a terminal status.
          void refreshRequirementCoverage();
          return;
        }
      }

      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function loadExistingDocuments() {
      try {
        const documents = await listDocuments(selectedCase.id);
        if (cancelled) return;

        setDocumentDetails(new Map(documents.map((document) => [document.id, document])));

        documents.forEach((document) => {
          if (!TERMINAL_STATUSES.has(document.status)) {
            void pollDocument(document.id);
          }
        });
      } catch {
        // best-effort load — leave the page in its empty state if this fails
      }
    }

    void loadExistingDocuments();
    void refreshRequirementCoverage();

    return () => {
      cancelled = true;
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
      const { ok, rejected } = await validateFiles(selectedFiles, selectedCase.id);
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
        if (result.success && result.document_id) {
          void pollDocument(result.document_id);
        }
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed.');
    } finally {
      setIsSubmitting(false);
    }
  }

  function getFieldDraft(documentId: string, extractedFields: Record<string, unknown>): Record<string, string> {
    return fieldDrafts.get(documentId) ?? fieldsToDraft(extractedFields);
  }

  function setFieldDraftValue(documentId: string, extractedFields: Record<string, unknown>, key: string, value: string) {
    setFieldDrafts((current) => {
      const next = new Map(current);
      const draft = { ...getFieldDraft(documentId, extractedFields), [key]: value };
      next.set(documentId, draft);
      return next;
    });
  }

  async function saveFieldDraft(documentId: string, originalFields: Record<string, unknown>) {
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
      setDocumentDetails((current) => new Map(current).set(documentId, updated));
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
  const failedCount = results.filter((result) => !result.success).length + rejections.length;

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
            Case #{selectedCase.caseNumber} · {selectedCase.type} — {selectedCase.applicant}
          </p>
          <h1>Upload supporting documents</h1>
          <p className="sub">
            Accepted files are logged and assigned a document ID for the investigation record.
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
                  const rejection = rejections.find((item) => item.file === file);
                  return (
                    <FileRow
                      key={`${file.name}-${index}`}
                      file={file}
                      result={resultByFilename.get(file.name)}
                      rejected={rejection ? describeIssue(rejection.issue) : undefined}
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
                    : `${selectedFiles.length} file${selectedFiles.length > 1 ? 's' : ''} selected`}
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
                      : `Upload ${selectedFiles.length} file${selectedFiles.length > 1 ? 's' : ''}`}
                </button>
              </div>
            </>
          )}

          {selectedFiles.length === 0 && !isSubmitting && (
            <div className="empty">
              No files selected yet — drag files above or click browse
            </div>
          )}

          {countError && <div className="toast show mixed">⚠ {countError}</div>}
          {error && <div className="toast show mixed">⚠ {error}</div>}

          {justSubmitted && hasOutcome && !error && !isSubmitting && (
            <div className={`toast show ${failedCount > 0 ? 'mixed' : 'all-ok'}`}>
              <span>{failedCount > 0 ? '⚠' : '✓'}</span>
              <span>
                <b>
                  {successfulCount} of {selectedFiles.length} documents uploaded.
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

                <span className="mock-badge live">LIVE</span>
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
                  .sort((a, b) => a.uploaded_at.localeCompare(b.uploaded_at))
                  .map((document) => {
                    const status = document.status;
                    const isTerminal = TERMINAL_STATUSES.has(status);

                    return (
                      <div key={document.id} className="documents-table-row">
                        <span className="document-filename">
                          {document.filename}
                        </span>

                        <span className="document-type-cell">
                          {document.document_type ?? '—'}
                        </span>

                        <span className="document-status-cell">
                          <span
                            className={`status-dot ${
                              status === 'analyzed'
                                ? 'status-dot--success'
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
                          onClick={() => onOpenDocument(document.id)}
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
                <span className="summary-icon summary-icon--risk">▥</span>
                <h2>Customer Risk Summary</h2>
              </div>
            </div>

            <div className="summary-message summary-message--info">
              <span className="summary-message-icon">ⓘ</span>

              <div>
                <strong>Risk summary not available yet.</strong>
                <p>
                  Risk analysis results will appear here once document extraction
                  and fraud analysis are completed.
                </p>
              </div>
            </div>
          </section>
          
          <section className="summary-card">
          <div className="summary-card-header">
            <div className="summary-card-title">
              <span className="summary-icon summary-icon--warning">△</span>
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
                  If any documents or extracted fields require manual review,
                  they will be displayed here.
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
                  <div key={document.id} className="flagged-item">
                    <div className="flagged-item-main">
                      <strong>{document.filename}</strong>

                      <div className="flagged-item-meta">
                        <span>
                          {document.document_type ?? 'Unknown document type'}
                        </span>

                        {document.extraction_confidence != null && (
                          <span>
                            Extraction confidence:{' '}
                            {document.extraction_confidence.toFixed(2)}
                          </span>
                        )}

                        {document.classification_outcome === 'manual_review' && (
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
                      onClick={() => onOpenDocument(document.id)}
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
              <span className="summary-icon summary-icon--audit">◴</span>
              <h2>Case Audit Trail</h2>
            </div>
          </div>

          <div className="summary-message summary-message--info">
            <span className="summary-message-icon">ⓘ</span>

            <div>
              <strong>No audit events available yet.</strong>
              <p>
                Case activity history will appear here once actions are
                recorded.
              </p>
            </div>
          </div>
        </section>
        </section>

        <aside className="upload-sidebar">
          <div className="section-label">
            <span>Document requirements</span>
            <span>
              {fulfilledRequirements.size} / {REQUIRED_DOCUMENT_TYPES.length}
            </span>
          </div>
          <ul className="requirements-list">
            {REQUIRED_DOCUMENT_TYPES.map((requirement) => {
              const isFulfilled = fulfilledRequirements.has(requirement.value);
              // proof_of_address is the one requirement that isn't just
              // yes/no — it needs an address on file per identified
              // individual (one per ID document), not just once anywhere
              // in the case. Shown as a fraction once there's at least one
              // identity to count; before that there's nothing to divide.
              const isProofOfAddress = requirement.value === 'proof_of_address';
              const showProgress = isProofOfAddress && proofOfAddressCoverage.total > 0;
              return (
                <li
                  key={requirement.value}
                  className={`requirement-row${isFulfilled ? ' fulfilled' : ''}`}
                >
                  <span className="requirement-dot" />
                  {requirement.label}
                  {showProgress && (
                    <span className="requirement-progress">
                      {' '}
                      ({proofOfAddressCoverage.covered}/{proofOfAddressCoverage.total})
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </aside>
      </div>
    </main>
  );
}
