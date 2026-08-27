import { useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';

import { uploadDocuments } from '../services/uploadService';
import type { BulkUploadItemResult } from '../services/uploadService';
import { recordUploadedHash, validateFiles } from '../utils/fileValidation';
import type { FileIssue } from '../utils/fileValidation';

function describeIssue(issue: FileIssue): string {
  switch (issue.type) {
    case 'empty':
      return 'File is empty (0 bytes).';
    case 'invalid-signature':
      return 'File content does not match a supported type (PDF, PNG, or JPEG).';
    case 'invalid-filename':
      return 'Filename is invalid or unsafe after removing disallowed characters.';
    case 'duplicate':
      return 'This exact file has already been uploaded for this case.';
  }
}

export default function UploadPage() {
  const [caseId, setCaseId] = useState('');
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [rejections, setRejections] = useState<{ file: File; issue: FileIssue }[]>([]);
  const [results, setResults] = useState<BulkUploadItemResult[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    setSelectedFiles(Array.from(event.target.files ?? []));
    setResults([]);
    setRejections([]);
    setError(null);
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmedCaseId = caseId.trim();
    if (!trimmedCaseId || selectedFiles.length === 0) {
      return;
    }

    setIsSubmitting(true);
    setError(null);
    setResults([]);

    try {
      const { ok, rejected } = await validateFiles(selectedFiles, trimmedCaseId);
      setRejections(rejected);

      if (ok.length === 0) {
        return;
      }

      const response = await uploadDocuments(
        trimmedCaseId,
        ok.map((validated) => validated.uploadFile)
      );
      setResults(response.results);

      response.results.forEach((result, index) => {
        if (result.success) {
          recordUploadedHash(trimmedCaseId, ok[index].hash);
        }
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed.');
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="shell">
      <section className="hero">
        <p className="eyebrow">Document upload</p>
        <h1>Upload documents</h1>
        <p className="lede">
          Upload one or more documents against an existing case. Files are validated locally —
          empty files, spoofed types, unsafe filenames, and duplicates are rejected before
          anything is sent.
        </p>
      </section>

      <form className="card upload-form" onSubmit={handleSubmit}>
        <label className="field">
          <span>Case ID</span>
          <input
            type="text"
            value={caseId}
            onChange={(event) => setCaseId(event.target.value)}
            placeholder="Existing case id"
            required
          />
        </label>

        <label className="field">
          <span>Files</span>
          <input type="file" multiple onChange={handleFileChange} accept=".pdf,.png,.jpg,.jpeg" />
        </label>

        <button type="submit" disabled={isSubmitting || selectedFiles.length === 0 || !caseId.trim()}>
          {isSubmitting ? 'Uploading...' : 'Upload'}
        </button>
      </form>

      {error && <p className="error-banner">{error}</p>}

      {rejections.length > 0 && (
        <section className="card">
          <h2>Skipped before upload</h2>
          <ul className="result-list">
            {rejections.map(({ file, issue }, index) => (
              <li key={index} className="result-item result-item--error">
                <strong>{file.name}</strong> — {describeIssue(issue)}
              </li>
            ))}
          </ul>
        </section>
      )}

      {results.length > 0 && (
        <section className="card">
          <h2>Upload results</h2>
          <ul className="result-list">
            {results.map((result, index) => (
              <li
                key={index}
                className={`result-item ${result.success ? 'result-item--ok' : 'result-item--error'}`}
              >
                <strong>{result.filename}</strong> —{' '}
                {result.success ? `stored (${result.status})` : result.error}
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
