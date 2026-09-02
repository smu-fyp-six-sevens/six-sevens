import { useMemo, useState } from 'react';
import type { CaseRecord } from '../App';
import DropZone from '../components/DropZone';
import FileRow from '../components/FileRow';
import { uploadDocuments } from '../services/uploadService';
import type { BulkUploadItemResult } from '../services/uploadService';
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
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const hasOutcome = results.length > 0 || rejections.length > 0;

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
  }

  async function handleSubmit() {
    if (!selectedFiles.length || countError) return;

    setIsSubmitting(true);
    setError(null);
    setResults([]);
    setRejections([]);

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
          Case #{selectedCase.id} · {selectedCase.type} — {selectedCase.applicant}
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

        {hasOutcome && !error && !isSubmitting && (
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
      </section>
    </main>
  );
}
