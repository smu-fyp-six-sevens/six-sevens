import { useState } from 'react';
import AppHeader from '../components/AppHeader';
import DropZone from '../components/DropZone';
import { testMetadataAnomaly, getMetadataAnomaly } from '../services/metadataAnomalyService';
import type { MetadataAnalysisResult, MetadataAnomaly } from '../services/metadataAnomalyService';
import { testNumericalValidation, getNumericalValidation } from '../services/numericalValidationService';
import type { NumericalFinding, NumericalValidationResult } from '../services/numericalValidationService';
import { testDateAnomalies, getDateAnomalies } from '../services/dateAnomalyService';
import type { DateFinding, DateAnomalyResult } from '../services/dateAnomalyService';
import { testSpellingAnomaly, getSpellingAnomaly } from '../services/spellingAnomalyService';
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

// impossible = the document cannot be genuine, or wasn't extracted
// correctly, as it stands. inconsistent = two values contradict each
// other. implausible = possible but very unlikely. signal = weak,
// non-conclusive evidence.
const NUMERICAL_SEVERITY_BADGE_CLASS: Record<NumericalFinding['severity'], string> = {
  impossible: 'err',
  inconsistent: 'err',
  implausible: 'warn',
  signal: 'pending',
};

const DATE_SEVERITY_BADGE_CLASS: Record<DateFinding['severity'], string> = {
  impossible: 'err',
  inconsistent: 'err',
  implausible: 'warn',
  signal: 'pending',
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
  // Dev-only escape hatch: when set, numerical-validation and
  // date-anomalies are run against this existing document's real
  // extracted_fields (GET .../detection/...) instead of an ad-hoc upload,
  // which always has no extracted fields behind it. Not part of the
  // delivered flow — just here so the check logic can be exercised against
  // real data without wiring these into DocumentDetailsPage.
  const [documentId, setDocumentId] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [spellingResult, setSpellingResult] = useState<SpellingAnomalyResult | null>(null);
  const [spellingError, setSpellingError] = useState<string | null>(null);
  const [metadataResult, setMetadataResult] = useState<MetadataAnalysisResult | null>(null);
  const [metadataError, setMetadataError] = useState<string | null>(null);
  const [numericalResult, setNumericalResult] = useState<NumericalValidationResult | null>(null);
  const [numericalError, setNumericalError] = useState<string | null>(null);
  const [dateResult, setDateResult] = useState<DateAnomalyResult | null>(null);
  const [dateError, setDateError] = useState<string | null>(null);

  function resetResults() {
    setSpellingResult(null);
    setSpellingError(null);
    setMetadataResult(null);
    setMetadataError(null);
    setNumericalResult(null);
    setNumericalError(null);
    setDateResult(null);
    setDateError(null);
  }

  function handleFiles(files: File[]) {
    if (files.length === 0) return;
    setFile(files[files.length - 1]);
    resetResults();
  }

  async function handleSubmit() {
    const trimmedId = documentId.trim();
    if (!file && !trimmedId) return;

    setIsSubmitting(true);
    resetResults();

    // Each check runs independently — one failing (e.g. exiftool missing
    // in this environment) shouldn't hide a result the other could still
    // produce. All four prefer a real document ID (real stored file +
    // extracted_fields) when one's given, falling back to the file-upload
    // test endpoint otherwise.
    const tasks: Promise<void>[] = [];

    const spellingPromise = trimmedId
      ? getSpellingAnomaly(trimmedId)
      : file
        ? testSpellingAnomaly(file)
        : null;
    if (spellingPromise) {
      tasks.push(
        spellingPromise
          .then(setSpellingResult)
          .catch((reason) =>
            setSpellingError(reason instanceof Error ? reason.message : 'Spelling anomaly check failed.'),
          ),
      );
    }

    const metadataPromise = trimmedId
      ? getMetadataAnomaly(trimmedId)
      : file
        ? testMetadataAnomaly(file, documentType || undefined)
        : null;
    if (metadataPromise) {
      tasks.push(
        metadataPromise
          .then(setMetadataResult)
          .catch((reason) =>
            setMetadataError(reason instanceof Error ? reason.message : 'Metadata anomaly check failed.'),
          ),
      );
    }

    const numericalPromise = trimmedId
      ? getNumericalValidation(trimmedId)
      : file
        ? testNumericalValidation(documentType || undefined)
        : null;
    if (numericalPromise) {
      tasks.push(
        numericalPromise
          .then(setNumericalResult)
          .catch((reason) =>
            setNumericalError(reason instanceof Error ? reason.message : 'Numerical validation failed.'),
          ),
      );
    }

    const datePromise = trimmedId
      ? getDateAnomalies(trimmedId)
      : file
        ? testDateAnomalies(documentType || undefined)
        : null;
    if (datePromise) {
      tasks.push(
        datePromise
          .then(setDateResult)
          .catch((reason) =>
            setDateError(reason instanceof Error ? reason.message : 'Date anomaly check failed.'),
          ),
      );
    }

    await Promise.allSettled(tasks);
    setIsSubmitting(false);
  }

  const hasAnyResult =
    spellingResult !== null ||
    metadataResult !== null ||
    numericalResult !== null ||
    dateResult !== null;

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
          </>
        )}

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
          legitimately has. Left unset, only the type-independent rules run. Ignored below when
          a document ID is set — that path reads the real document's own stored type instead.
        </p>

        <div className="section-label anomaly-subhead">
          <span>Test against an existing document ID (dev only, optional)</span>
        </div>

        <input
          type="text"
          className="type-select"
          placeholder="e.g. 922c5711-a281-4de8-abbb-882e5305a3c7"
          value={documentId}
          disabled={isSubmitting}
          onChange={(event) => setDocumentId(event.target.value)}
        />

        <p className="anomaly-empty">
          When set, all four checks run against this document's real, already-stored file and
          extracted fields (GET /documents/&#123;id&#125;/detection/...) instead of the file
          above — the only way to see numerical validation and date anomalies produce anything
          other than "unable to assess", since an ad-hoc upload has no extracted fields behind
          it. Leave it blank to test against an uploaded file instead.
        </p>

        <div className="action-bar">
          <span className="count">
            {isSubmitting ? 'Checking…' : hasAnyResult ? 'Check complete' : 'Ready'}
          </span>
          <button
            type="button"
            className="upload-btn"
            disabled={isSubmitting || (!file && !documentId.trim())}
            onClick={() => {
              void handleSubmit();
            }}
          >
            {isSubmitting ? 'Checking…' : hasAnyResult ? 'Run again' : 'Run checks'}
          </button>
        </div>

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
                  <div key={`${anomaly.word}-${index}`} className="kv-row">
                    <span className="kv-key">{anomaly.word}</span>
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

        {(numericalResult || numericalError) && (
          <section className="details-card">
            <div className="anomaly-card-head">
              <h2>Numerical validation</h2>
              {numericalResult && (
                <span className={`badge ${numericalResult.unable_to_assess ? 'err' : 'ok'}`}>
                  {numericalResult.unable_to_assess
                    ? 'Unable to assess'
                    : `${(numericalResult.findings ?? []).length} flagged`}
                </span>
              )}
            </div>

            {numericalError ? (
              <p className="mock-note warning">⚠ {numericalError}</p>
            ) : !numericalResult ? null : numericalResult.unable_to_assess ? (
              <p className="mock-note warning">
                {numericalResult.unable_to_assess_reason ??
                  'No numerical values could be read from this document.'}
              </p>
            ) : (
              <>
                <p className="anomaly-empty">
                  {numericalResult.values_analysed} numerical value
                  {numericalResult.values_analysed === 1 ? '' : 's'} read from this document's
                  extracted fields. Without document_type set above (or without a real
                  classified document behind this upload), only the universal GEN.*/ID.* checks
                  run — the rest report as skipped below, not as passed.
                </p>

                {(numericalResult.findings ?? []).length === 0 ? (
                  <p className="anomaly-empty">No numerical anomalies flagged.</p>
                ) : (
                  <div className="kv-grid kv-grid--flush">
                    {numericalResult.findings.map((finding, index) => (
                      <div key={`${finding.check_code}-${index}`} className="kv-row">
                        <span className="kv-key">
                          <span
                            className={`badge severity-chip ${NUMERICAL_SEVERITY_BADGE_CLASS[finding.severity]}`}
                          >
                            {finding.severity}
                          </span>
                          {finding.check_code}
                          {finding.needs_review && ' · needs review'}
                        </span>
                        <span className="kv-value">
                          {finding.summary}
                          {finding.stated !== null && finding.expected !== null && (
                            <>
                              {' '}
                              <strong>
                                (stated {finding.stated}, expected {finding.expected})
                              </strong>
                            </>
                          )}
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                {(numericalResult.skipped_checks ?? []).length > 0 && (
                  <>
                    <div className="section-label anomaly-subhead">
                      <span>Skipped checks ({numericalResult.skipped_checks.length})</span>
                    </div>
                    <div className="kv-grid kv-grid--flush">
                      {numericalResult.skipped_checks.map((skip) => (
                        <div key={skip.check_code} className="kv-row">
                          <span className="kv-key">{skip.check_code}</span>
                          <span className="kv-value">{skip.reason}</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}

                {numericalResult.benford && (
                  <>
                    <div className="section-label anomaly-subhead">
                      <span>Benford&apos;s Law — leading digit distribution</span>
                    </div>

                    {numericalResult.benford.unable_to_assess ? (
                      <p className="anomaly-empty">
                        {numericalResult.benford.unable_to_assess_reason}
                      </p>
                    ) : (
                      <>
                        <p className="anomaly-empty">
                          {numericalResult.benford.sample_size} values ·{' '}
                          chi-square {numericalResult.benford.chi_square} against a p&lt;0.01
                          critical value of {numericalResult.benford.chi_square_critical} ·{' '}
                          {numericalResult.benford.conforms
                            ? 'consistent with Benford'
                            : 'differs by more than chance'}
                        </p>
                        <div className="kv-grid kv-grid--flush">
                          {(numericalResult.benford.digits ?? []).map((row) => (
                            <div key={row.digit} className="kv-row">
                              <span className="kv-key">Leading digit {row.digit}</span>
                              <span className="kv-value">
                                {row.count} occurrence{row.count === 1 ? '' : 's'} ·{' '}
                                {row.observed_percentage.toFixed(1)}% observed vs{' '}
                                {row.expected_percentage.toFixed(1)}% expected
                              </span>
                            </div>
                          ))}
                        </div>
                      </>
                    )}
                  </>
                )}
              </>
            )}

            {numericalResult && <RawJson value={numericalResult} />}
          </section>
        )}

        {(dateResult || dateError) && (
          <section className="details-card">
            <div className="anomaly-card-head">
              <h2>Date anomalies</h2>
              {dateResult && (
                <span className={`badge ${dateResult.unable_to_assess ? 'err' : 'ok'}`}>
                  {dateResult.unable_to_assess
                    ? 'Unable to assess'
                    : `${(dateResult.findings ?? []).length} flagged`}
                </span>
              )}
            </div>

            {dateError ? (
              <p className="mock-note warning">⚠ {dateError}</p>
            ) : !dateResult ? null : dateResult.unable_to_assess ? (
              <p className="mock-note warning">
                {dateResult.unable_to_assess_reason ??
                  'No dates could be read from this document.'}
              </p>
            ) : (
              <>
                <p className="anomaly-empty">
                  {(dateResult.dates_extracted ?? []).length} date
                  {(dateResult.dates_extracted ?? []).length === 1 ? '' : 's'} extracted from this
                  document's extracted fields. Without document_type set above (or without a real
                  classified document behind this upload), only the universal GEN.*/ID.* checks
                  run — the rest report as skipped below, not as passed.
                </p>

                {(dateResult.findings ?? []).length === 0 ? (
                  <p className="anomaly-empty">No date anomalies flagged.</p>
                ) : (
                  <div className="kv-grid kv-grid--flush">
                    {dateResult.findings.map((finding, index) => (
                      <div key={`${finding.check_code}-${index}`} className="kv-row">
                        <span className="kv-key">
                          <span
                            className={`badge severity-chip ${DATE_SEVERITY_BADGE_CLASS[finding.severity]}`}
                          >
                            {finding.severity}
                          </span>
                          {finding.check_code}
                          {finding.needs_review && ' · needs review'}
                        </span>
                        <span className="kv-value">
                          {finding.summary}
                          {finding.stated !== null && finding.expected !== null && (
                            <>
                              {' '}
                              <strong>
                                (stated {finding.stated}, expected {finding.expected})
                              </strong>
                            </>
                          )}
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                {(dateResult.skipped_checks ?? []).length > 0 && (
                  <>
                    <div className="section-label anomaly-subhead">
                      <span>Skipped checks ({dateResult.skipped_checks.length})</span>
                    </div>
                    <div className="kv-grid kv-grid--flush">
                      {dateResult.skipped_checks.map((skip) => (
                        <div key={skip.check_code} className="kv-row">
                          <span className="kv-key">{skip.check_code}</span>
                          <span className="kv-value">{skip.reason}</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}

                {(dateResult.dates_extracted ?? []).length > 0 && (
                  <>
                    <div className="section-label anomaly-subhead">
                      <span>Extracted dates ({dateResult.dates_extracted.length})</span>
                    </div>
                    <div className="kv-grid kv-grid--flush">
                      {dateResult.dates_extracted.map((extracted, index) => (
                        <div key={`${extracted.field}-${index}`} className="kv-row">
                          <span className="kv-key">{extracted.label}</span>
                          <span className="kv-value">{extracted.value}</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </>
            )}

            {dateResult && <RawJson value={dateResult} />}
          </section>
        )}
      </section>
    </main>
  );
}
