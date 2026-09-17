import { useState } from 'react';
import AppHeader from '../components/AppHeader';
import DropZone from '../components/DropZone';
import { testSpellingAnomaly } from '../services/spellingAnomalyService';
import type { SpellingAnomalyResult } from '../services/spellingAnomalyService';

interface SpellingAnomalyTestPageProps {
  onBack: () => void;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

export default function SpellingAnomalyTestPage({ onBack }: SpellingAnomalyTestPageProps) {
  const [file, setFile] = useState<File | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SpellingAnomalyResult | null>(null);

  function handleFiles(files: File[]) {
    if (files.length === 0) return;
    setFile(files[files.length - 1]);
    setResult(null);
    setError(null);
  }

  async function handleSubmit() {
    if (!file) return;

    setIsSubmitting(true);
    setError(null);
    setResult(null);

    try {
      const response = await testSpellingAnomaly(file);
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Spelling anomaly check failed.');
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="shell">
      <AppHeader />

      <section className="upload-view">
        <button type="button" className="back-link" onClick={onBack}>
          ← Back to case queue
        </button>

        <p className="eyebrow">Dev tool · Not tied to a case</p>
        <h1>Test spelling anomaly detection</h1>
        <p className="sub">
          Runs a single file straight through the spelling-anomaly check (tokenize → SymSpell →
          domain-dictionary filter → proper-noun heuristic — entirely local, no LLM) and shows
          the result — no case, no Supabase, nothing written to the flags table. Note: this
          dev-tool run has no extracted fields to cross-reference against (no real document
          exists here), so it'll flag more proper nouns than the real per-document check would.
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
                {result && (
                  <span className={`badge ${result.unable_to_assess ? 'err' : 'ok'}`}>
                    {result.unable_to_assess ? 'Unable to assess' : `${result.anomalies.length} flagged`}
                  </span>
                )}
              </div>
            </div>

            <div className="action-bar">
              <span className="count">
                {isSubmitting ? 'Checking…' : result ? 'Check complete' : 'Ready'}
              </span>
              <button
                type="button"
                className="upload-btn"
                disabled={isSubmitting}
                onClick={() => {
                  void handleSubmit();
                }}
              >
                {isSubmitting ? 'Checking…' : result ? 'Run again' : 'Run check'}
              </button>
            </div>
          </>
        )}

        {!file && !isSubmitting && (
          <div className="empty">No file selected yet — drag a PDF, PNG, or JPG above</div>
        )}

        {error && <div className="toast show mixed">⚠ {error}</div>}

        {result && (
          <section className="pipeline-note">
            <div className="section-label">
              <span>Spelling anomaly result</span>
              <span className={`badge ${result.unable_to_assess ? 'err' : 'ok'}`}>
                {result.unable_to_assess ? 'Unable to assess' : 'Assessed'}
              </span>
            </div>

            {result.unable_to_assess ? (
              <p className="mock-note warning">
                {result.unable_to_assess_reason ?? 'No extractable text found in this document.'}
              </p>
            ) : result.anomalies.length === 0 ? (
              <p className="mock-note">No spelling anomalies flagged.</p>
            ) : (
              <>
                <p className="mock-note">
                  This dev tool has no extracted fields to cross-reference against, so a
                  capitalised name with no close spelling match that would normally be
                  suppressed as a probable proper noun may still show up here more than it
                  would via the real per-document check.
                </p>
                <div className="kv-grid" style={{ marginTop: 10 }}>
                  {result.anomalies.map((anomaly, index) => (
                    <div key={`${anomaly.page}-${anomaly.section}-${anomaly.word}-${index}`} className="kv-row">
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
              </>
            )}
          </section>
        )}
      </section>
    </main>
  );
}
