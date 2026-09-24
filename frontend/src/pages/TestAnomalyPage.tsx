import { useState } from 'react';
import AppHeader from '../components/AppHeader';
import DropZone from '../components/DropZone';
import { testMetadataAnomaly } from '../services/metadataAnomalyService';
import type { MetadataAnalysisResult, MetadataAnomaly } from '../services/metadataAnomalyService';
import { testSpellingAnomaly } from '../services/spellingAnomalyService';
import type { SpellingAnomalyResult } from '../services/spellingAnomalyService';
import { DOCUMENT_TYPE_OPTIONS } from '../services/uploadService';

interface TestAnomalyPageProps {
  onBack: () => void;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

function humanizeMetadataField(field: string): string {
  return field
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

// high = no innocent explanation in a KYC workflow, low = weak
// corroboration, so they shouldn't all read with the same urgency.
const SEVERITY_BADGE_CLASS: Record<MetadataAnomaly['severity'], string> = {
  high: 'err',
  medium: 'warn',
  low: 'pending',
};

function RawJson({ value }: { value: unknown }) {
  return (
    <details className="raw-json">
      <summary>Raw JSON response</summary>
      <pre className="extraction-text-panel">{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

export default function TestAnomalyPage({ onBack }: TestAnomalyPageProps) {
  const [file, setFile] = useState<File | null>(null);
  const [documentType, setDocumentType] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [spellingResult, setSpellingResult] = useState<SpellingAnomalyResult | null>(null);
  const [spellingError, setSpellingError] = useState<string | null>(null);
  const [metadataResult, setMetadataResult] = useState<MetadataAnalysisResult | null>(null);
  const [metadataError, setMetadataError] = useState<string | null>(null);

  function resetResults() {
    setSpellingResult(null);
    setSpellingError(null);
    setMetadataResult(null);
    setMetadataError(null);
  }

  function handleFiles(files: File[]) {
    if (files.length === 0) return;
    setFile(files[files.length - 1]);
    resetResults();
  }

  async function handleSubmit() {
    if (!file) return;

    setIsSubmitting(true);
    resetResults();

    // Each check runs independently — one failing (e.g. exiftool missing
    // in this environment) shouldn't hide a result the other could still
    // produce.
    const [spelling, metadata] = await Promise.allSettled([
      testSpellingAnomaly(file),
      testMetadataAnomaly(file, documentType || undefined),
    ]);

    if (spelling.status === 'fulfilled') {
      setSpellingResult(spelling.value);
    } else {
      setSpellingError(
        spelling.reason instanceof Error ? spelling.reason.message : 'Spelling anomaly check failed.',
      );
    }

    if (metadata.status === 'fulfilled') {
      setMetadataResult(metadata.value);
    } else {
      setMetadataError(
        metadata.reason instanceof Error ? metadata.reason.message : 'Metadata anomaly check failed.',
      );
    }

    setIsSubmitting(false);
  }

  const hasAnyResult = spellingResult !== null || metadataResult !== null;

  return (
    <main className="shell">
      <AppHeader />

      <section className="tool-view">
        <button type="button" className="back-link" onClick={onBack}>
          ← Back to case queue
        </button>

        <p className="eyebrow">Dev tool · Not tied to a case</p>
        <h1>Test anomaly detection</h1>
        <p className="sub">
          Runs one file through the spelling and metadata checks and shows both results. Nothing
          is written to the database, and with no real document behind it there are no extracted
          fields to cross-reference — so results can differ from the same checks run against an
          uploaded document.
        </p>

        <DropZone onFiles={handleFiles} disabled={isSubmitting} />

        {file && (
          <>
            <div className="section-label">
              <span>Selected file</span>
            </div>

            <div className="file-list">
              <div className="file-row">
                <div className="f-icon">{file.name.split('.').pop()?.slice(0, 4).toUpperCase()}</div>
                <div className="f-main">
                  <div className="f-name">{file.name}</div>
                  <div className="f-meta">{formatBytes(file.size)}</div>
                </div>
              </div>
            </div>

            <div className="section-label anomaly-subhead">
              <span>Document type (optional)</span>
            </div>

            <select
              className="type-select"
              value={documentType}
              disabled={isSubmitting}
              onChange={(event) => setDocumentType(event.target.value)}
            >
              <option value="">Not specified</option>
              {DOCUMENT_TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>

            <p className="anomaly-empty">
              Some metadata rules only apply to documents an institution issued — a bank statement
              that has been through a PDF editor is worth a look; a form the applicant filled in
              legitimately has. Left unset, only the type-independent rules run.
            </p>

            <div className="action-bar">
              <span className="count">
                {isSubmitting ? 'Checking…' : hasAnyResult ? 'Check complete' : 'Ready'}
              </span>
              <button
                type="button"
                className="upload-btn"
                disabled={isSubmitting}
                onClick={() => {
                  void handleSubmit();
                }}
              >
                {isSubmitting ? 'Checking…' : hasAnyResult ? 'Run again' : 'Run checks'}
              </button>
            </div>
          </>
        )}

        {(spellingResult || spellingError) && (
          <section className="details-card">
            <div className="anomaly-card-head">
              <h2>Spelling anomalies</h2>
              {spellingResult && (
                <span className={`badge ${spellingResult.unable_to_assess ? 'err' : 'ok'}`}>
                  {spellingResult.unable_to_assess
                    ? 'Unable to assess'
                    : `${spellingResult.anomalies.length} flagged`}
                </span>
              )}
            </div>

            {spellingError ? (
              <p className="mock-note warning">⚠ {spellingError}</p>
            ) : spellingResult!.unable_to_assess ? (
              <p className="mock-note warning">
                {spellingResult!.unable_to_assess_reason ??
                  'No extractable text found in this document.'}
              </p>
            ) : spellingResult!.anomalies.length === 0 ? (
              <p className="anomaly-empty">No spelling anomalies flagged.</p>
            ) : (
              <div className="kv-grid kv-grid--flush">
                {spellingResult!.anomalies.map((anomaly, index) => (
                  <div
                    key={`${anomaly.page}-${anomaly.section}-${anomaly.word}-${index}`}
                    className="kv-row"
                  >
                    <span className="kv-key">
                      p{anomaly.page}·s{anomaly.section} — {anomaly.word}
                    </span>
                    <span className="kv-value">
                      {anomaly.suggested_correction
                        ? `Suggested: "${anomaly.suggested_correction}" — `
                        : '(no suggestion) — '}
                      {anomaly.surrounding_text}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {spellingResult && <RawJson value={spellingResult} />}
          </section>
        )}

        {(metadataResult || metadataError) && (
          <section className="details-card">
            <div className="anomaly-card-head">
              <h2>Metadata anomalies</h2>
              {metadataResult && (
                <span className={`badge ${metadataResult.unable_to_assess ? 'err' : 'ok'}`}>
                  {metadataResult.unable_to_assess
                    ? 'Unable to assess'
                    : `${metadataResult.anomalies.length} flagged`}
                </span>
              )}
            </div>

            {metadataError ? (
              <p className="mock-note warning">⚠ {metadataError}</p>
            ) : metadataResult!.unable_to_assess ? (
              <p className="mock-note warning">
                {metadataResult!.unable_to_assess_reason ?? 'No metadata found in this document.'}
              </p>
            ) : (
              <>
                {metadataResult!.anomalies.length === 0 ? (
                  <p className="anomaly-empty">No metadata anomalies flagged.</p>
                ) : (
                  <div className="kv-grid kv-grid--flush">
                    {metadataResult!.anomalies.map((anomaly, index) => (
                      <div key={`${anomaly.field}-${index}`} className="kv-row">
                        <span className="kv-key">
                          <span
                            className={`badge severity-chip ${SEVERITY_BADGE_CLASS[anomaly.severity]}`}
                          >
                            {anomaly.severity}
                          </span>
                          {humanizeMetadataField(anomaly.field)}
                        </span>
                        <span className="kv-value">
                          {anomaly.value ? `"${anomaly.value}" — ` : ''}
                          {anomaly.reason}
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                {metadataResult!.ai_generation_indicators.length > 0 && (
                  <p className="mock-note warning">
                    ⚠ {metadataResult!.ai_generation_indicators.length} AI-generation indicator
                    {metadataResult!.ai_generation_indicators.length === 1 ? '' : 's'} recorded.
                  </p>
                )}

                <div className="section-label anomaly-subhead">
                  <span>Extracted metadata</span>
                </div>
                <div className="kv-grid kv-grid--flush">
                  {Object.entries(metadataResult!.extracted_metadata).map(([field, value]) => (
                    <div key={field} className="kv-row">
                      <span className="kv-key">{humanizeMetadataField(field)}</span>
                      <span className="kv-value">{value ?? '—'}</span>
                    </div>
                  ))}
                </div>
              </>
            )}

            {metadataResult && <RawJson value={metadataResult} />}
          </section>
        )}
      </section>
    </main>
  );
}
