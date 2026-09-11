import { useEffect, useMemo, useRef, useState } from 'react';
import type { CaseRecord } from '../App';
import DropZone from '../components/DropZone';
import FileRow from '../components/FileRow';
import { getDocument, listDocuments, uploadDocuments } from '../services/uploadService';
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

interface UploadPageProps {
  selectedCase: CaseRecord;
  onBack: () => void;
}

function isExtractionNotConfiguredFailure(reason: string | null | undefined): boolean {
  return Boolean(reason?.startsWith('Extraction invocation failed'));
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
        setResults(
          documents.map((document) => ({
            filename: document.filename,
            success: true,
            document_id: document.id,
            status: document.status,
          })),
        );

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
    setDocumentDetails(new Map());
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
    <main className="shell">
      <header className="app-header">
        <div className="brand">
          GLDB <span>Fraud Review</span>
        </div>
        <div className="officer-tag">Loan Officer · J. Tan</div>
      </header>

      <section className="upload-view">
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

        {results.some((result) => result.success) && (
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

        {results.some((result) => result.success) && (
          <section className="pipeline-note">
            <div className="section-label">
              <span>Classification result</span>
              <span className="mock-badge live">LIVE</span>
            </div>

            {results
              .filter((result) => result.success && result.document_id)
              .map((result) => {
                const documentId = result.document_id as string;
                const document = documentDetails.get(documentId);
                const status = document?.status ?? result.status ?? 'queued';
                const isTerminal = TERMINAL_STATUSES.has(status);
                const extractionNotConfigured =
                  status === 'failed' && isExtractionNotConfiguredFailure(document?.failure_reason);

                return (
                  <div key={result.filename} className="mock-classification-row">
                    <span className="mock-filename">{result.filename}</span>
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
                        <span className="mock-value">{document?.document_type ?? '—'}</span>
                      </div>
                      <div>
                        <span className="mock-label">Confidence</span>
                        <span className="mock-value">
                          {document?.confidence != null ? document.confidence.toFixed(2) : '—'}
                        </span>
                      </div>
                      <div>
                        <span className="mock-label">Routing outcome</span>
                        <span className="mock-value">
                          {document?.classification_outcome ?? '—'}
                        </span>
                      </div>
                    </div>

                    {document?.evidence && document.evidence.length > 0 && (
                      <ul className="mock-evidence">
                        {document.evidence.map((item, i) => (
                          <li key={i}>{item}</li>
                        ))}
                      </ul>
                    )}

                    {document?.document_type === 'unrecognised' && (
                      <p className="mock-note warning">
                        This document doesn't match any known KYC category and has been
                        flagged for manual review.
                      </p>
                    )}

                    {extractionNotConfigured && document?.document_type !== 'unrecognised' && (
                      <p className="mock-note">
                        Classification succeeded — this failed because the extraction workflow
                        hasn't been set up in Dify yet, not because of the classifier.
                      </p>
                    )}

                    {status === 'failed' && document?.failure_reason && (
                      <p className="mock-failure-reason">{document.failure_reason}</p>
                    )}
                  </div>
                );
              })}
          </section>
        )}
      </section>
    </main>
  );
}
