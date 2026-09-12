import { useEffect, useMemo, useRef, useState } from 'react';
import type { CaseRecord } from '../App';
import DropZone from '../components/DropZone';
import FileRow from '../components/FileRow';
import {
  REQUIRED_DOCUMENT_TYPES,
  EXTRACTION_CONFIDENCE_THRESHOLD,
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
}

function isExtractionNotConfiguredFailure(reason: string | null | undefined): boolean {
  return Boolean(reason?.startsWith('Extraction invocation failed'));
}

function formatRoutingOutcome(outcome: string | null | undefined): string {
  switch (outcome) {
    case 'accepted':
    case 'manual_review':
      return 'Proceed with extractor agent';
    default:
      return '—';
  }
}

// Extracted field keys are snake_case identifiers (e.g. "bank_name",
// "id_number") since they're also used as object keys sent back on save —
// this only affects how they're displayed, not the underlying data.
const FIELD_KEY_ACRONYMS = new Set(['id', 'uen', 'tin', 'nric']);

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
  for (const [key, value] of Object.entries(fields)) {
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

export default function UploadPage({ selectedCase, onBack }: UploadPageProps) {
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [countError, setCountError] = useState<string | null>(null);
  const [rejections, setRejections] = useState<{ file: File; issue: FileIssue }[]>([]);
  const [results, setResults] = useState<BulkUploadItemResult[]>([]);
  const [justSubmitted, setJustSubmitted] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [documentDetails, setDocumentDetails] = useState<Map<string, DocumentRecord>>(new Map());
  const [fieldDrafts, setFieldDrafts] = useState<Map<string, Record<string, string>>>(new Map());
  const [savingFields, setSavingFields] = useState<Set<string>>(new Set());
  const [saveFieldErrors, setSaveFieldErrors] = useState<Map<string, string>>(new Map());

  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const hasOutcome = results.length > 0 || rejections.length > 0;

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

        if (TERMINAL_STATUSES.has(document.status)) return;
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

    return () => {
      cancelled = true;
    };
  }, [selectedCase.id]);

  const resultByFilename = useMemo(
    () => new Map(results.map((result) => [result.filename, result])),
    [results],
  );

  // A requirement counts as met only once a document's classification was
  // actually accepted — one still in manual review hasn't been confirmed to
  // be what it claims. A single document can satisfy more than one
  // requirement (e.g. an NRIC also covers Proof of Address), so this is a
  // union across every document for this case, not a 1:1 count.
  const fulfilledRequirements = useMemo(() => {
    const fulfilled = new Set<string>();
    for (const document of documentDetails.values()) {
      if (document.classification_outcome === 'accepted') {
        document.fulfilled_requirements.forEach((requirement) => fulfilled.add(requirement));
      }
    }
    return fulfilled;
  }, [documentDetails]);

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

  async function saveFieldDraft(documentId: string) {
    const draft = fieldDrafts.get(documentId);
    if (!draft) return;

    setSavingFields((current) => new Set(current).add(documentId));
    setSaveFieldErrors((current) => {
      const next = new Map(current);
      next.delete(documentId);
      return next;
    });

    // Empty string means "cleared by the officer" — send null rather than ""
    // so it reads as "not present" the same way an LLM-omitted field does.
    const payload: Record<string, unknown> = {};
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
            <section className="pipeline-note">
              <div className="section-label">
                <span>Next step</span>
                <span>Document processing</span>
              </div>
              <div className="pipeline">
                <span className="pstage done">Uploaded</span>
                <span className="pconnector" />
                <span className="pstage pending">Extracting</span>
                <span className="pconnector" />
                <span className="pstage pending">Quality check</span>
                <span className="pconnector" />
                <span className="pstage pending">Result</span>
              </div>
              <p>
                Upload succeeded. The extraction and quality-check details are not returned by
                the current upload API, so this UI leaves those stages pending rather than
                inventing processing results.
              </p>
            </section>
          )}

          {documentDetails.size > 0 && (
            <section className="pipeline-note">
              <div className="section-label">
                <span>Classification result</span>
                <span className="mock-badge live">LIVE</span>
              </div>

              {Array.from(documentDetails.values())
                .sort((a, b) => a.uploaded_at.localeCompare(b.uploaded_at))
                .map((document) => {
                  const status = document.status;
                  const isTerminal = TERMINAL_STATUSES.has(status);
                  const extractionNotConfigured =
                    status === 'failed' && isExtractionNotConfiguredFailure(document.failure_reason);

                  return (
                    <div key={document.id} className="mock-classification-row">
                      <span className="mock-filename">{document.filename}</span>
                      <div className="mock-classification">
                        <div>
                          <span className="mock-label">Status</span>
                          <span className="mock-value">
                            {status}
                            {!isTerminal && '…'}
                          </span>
                        </div>
                        <div>
                          <span className="mock-label">Document type</span>
                          <span className="mock-value">{document.document_type ?? '—'}</span>
                        </div>
                        <div>
                          <span className="mock-label">Confidence</span>
                          <span className="mock-value">
                            {document.confidence != null ? document.confidence.toFixed(2) : '—'}
                          </span>
                        </div>
                        <div>
                          <span className="mock-label">Routing outcome</span>
                          <span className="mock-value">
                            {formatRoutingOutcome(document.classification_outcome)}
                          </span>
                        </div>
                      </div>

                      {document.classification_outcome === 'accepted' &&
                        document.fulfilled_requirements.length > 0 && (
                          <div className="fulfils-row">
                            <span className="mock-label">Fulfils requirements</span>
                            <div className="fulfils-tags">
                              {document.fulfilled_requirements.map((requirement) => (
                                <span key={requirement} className="fulfils-tag">
                                  {REQUIREMENT_LABELS.get(requirement) ?? requirement}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}

                      {document.evidence && document.evidence.length > 0 && (
                        <ul className="mock-evidence">
                          {document.evidence.map((item, i) => (
                            <li key={i}>{item}</li>
                          ))}
                        </ul>
                      )}

                      {document.escalation_reason && (
                        <p className="mock-note warning">
                          Classification was re-run — Extraction flagged that this didn't look
                          like the initial guess: "{document.escalation_reason}"
                        </p>
                      )}

                      {status === 'analyzed' && document.extracted_fields && (
                        <>
                          <div className="section-label" style={{ marginTop: 10 }}>
                            <span>Extracted fields</span>
                            <span>
                              Extraction confidence:{' '}
                              {document.extraction_confidence != null
                                ? document.extraction_confidence.toFixed(2)
                                : '—'}
                            </span>
                          </div>

                          {document.extraction_confidence != null &&
                          document.extraction_confidence < EXTRACTION_CONFIDENCE_THRESHOLD ? (
                            <>
                              <p className="mock-note warning">
                                Low extraction confidence — review and correct these fields before
                                relying on them.
                              </p>
                              <div className="kv-grid kv-grid--editable">
                                {Object.entries(
                                  getFieldDraft(document.id, document.extracted_fields),
                                ).map(([key, value]) => (
                                  <div key={key} className="kv-row">
                                    <span className="kv-key">{humanizeFieldKey(key)}</span>
                                    <input
                                      className="kv-input"
                                      value={value}
                                      disabled={savingFields.has(document.id)}
                                      onChange={(event) =>
                                        setFieldDraftValue(
                                          document.id,
                                          document.extracted_fields as Record<string, unknown>,
                                          key,
                                          event.target.value,
                                        )
                                      }
                                    />
                                  </div>
                                ))}
                              </div>
                              <div className="action-bar" style={{ marginTop: 10 }}>
                                <span className="count">
                                  {saveFieldErrors.get(document.id) ?? 'Edits are saved to Supabase.'}
                                </span>
                                <button
                                  type="button"
                                  className="upload-btn"
                                  disabled={savingFields.has(document.id)}
                                  onClick={() => {
                                    void saveFieldDraft(document.id);
                                  }}
                                >
                                  {savingFields.has(document.id) ? 'Saving…' : 'Save fields'}
                                </button>
                              </div>
                            </>
                          ) : (
                            <div className="kv-grid">
                              {Object.entries(document.extracted_fields).map(([key, value]) => (
                                <div key={key} className="kv-row">
                                  <span className="kv-key">{humanizeFieldKey(key)}</span>
                                  <span className="kv-value">
                                    {value === null || value === undefined ? '—' : String(value)}
                                  </span>
                                </div>
                              ))}
                            </div>
                          )}
                        </>
                      )}

                      {document.document_type === 'unrecognised' && (
                        <p className="mock-note warning">
                          This document doesn't match any known KYC category and has been
                          flagged for manual review.
                        </p>
                      )}

                      {extractionNotConfigured && document.document_type !== 'unrecognised' && (
                        <p className="mock-note">
                          Classification succeeded — this failed because the extraction workflow
                          hasn't been set up in Dify yet, not because of the classifier.
                        </p>
                      )}

                      {status === 'failed' && document.failure_reason && (
                        <p className="mock-failure-reason">{document.failure_reason}</p>
                      )}
                    </div>
                  );
                })}
            </section>
          )}
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
              return (
                <li
                  key={requirement.value}
                  className={`requirement-row${isFulfilled ? ' fulfilled' : ''}`}
                >
                  <span className="requirement-dot" />
                  {requirement.label}
                </li>
              );
            })}
          </ul>
        </aside>
      </div>
    </main>
  );
}
