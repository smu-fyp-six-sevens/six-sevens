import { useMemo, useState } from 'react';
import AppHeader from '../components/AppHeader';
import DropZone from '../components/DropZone';
import { testExtraction } from '../services/extractionService';
import type { ExtractionTestResult } from '../services/extractionService';

interface ExtractionTestPageProps {
  onBack: () => void;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

export default function ExtractionTestPage({ onBack }: ExtractionTestPageProps) {
  const [file, setFile] = useState<File | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ExtractionTestResult | null>(null);
  const [activePage, setActivePage] = useState(0);

  const metadataEntries = useMemo(() => {
    if (!result) return [];
    return Object.entries(result.metadata).filter(([, value]) => value !== null && value !== '');
  }, [result]);

  function handleFiles(files: File[]) {
    if (files.length === 0) return;
    setFile(files[files.length - 1]);
    setResult(null);
    setError(null);
    setActivePage(0);
  }

  async function handleSubmit() {
    if (!file) return;

    setIsSubmitting(true);
    setError(null);
    setResult(null);

    try {
      const response = await testExtraction(file);
      setResult(response);
      setActivePage(0);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Extraction failed.');
    } finally {
      setIsSubmitting(false);
    }
  }

  const activePageData = result?.pages[activePage] ?? null;

  return (
    <main className="shell">
      <AppHeader />

      <section className="upload-view">
        <button type="button" className="back-link" onClick={onBack}>
          ← Back to case queue
        </button>

        <p className="eyebrow">Dev tool · Not tied to a case</p>
        <h1>Test extraction logic</h1>
        <p className="sub">
          Runs a single file straight through the raw-text extraction step (native PDF text, or
          OCR for scans and images) and shows the result — no case, no Supabase, no
          classification, no Dify.
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
                {result && <span className="badge ok">{result.extraction_method}</span>}
              </div>
            </div>

            <div className="action-bar">
              <span className="count">
                {isSubmitting ? 'Extracting…' : result ? 'Extraction complete' : 'Ready'}
              </span>
              <button
                type="button"
                className="upload-btn"
                disabled={isSubmitting}
                onClick={() => {
                  void handleSubmit();
                }}
              >
                {isSubmitting ? 'Extracting…' : result ? 'Run again' : 'Run extraction'}
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
              <span>Extraction result</span>
              <span className={`badge ${result.needs_review ? 'err' : 'ok'}`}>
                {result.needs_review ? 'Needs review' : 'Looks good'}
              </span>
            </div>

            <div className="mock-classification">
              <div>
                <span className="mock-label">Method</span>
                <span className="mock-value">{result.extraction_method}</span>
              </div>
              <div>
                <span className="mock-label">Pages</span>
                <span className="mock-value">{result.pages.length}</span>
              </div>
              <div>
                <span className="mock-label">Status</span>
                <span className="mock-value">{result.status}</span>
              </div>
              <div>
                <span className="mock-label">Text length</span>
                <span className="mock-value">{result.raw_text.length.toLocaleString()}</span>
              </div>
            </div>

            {result.pages.length > 1 && (
              <div className="page-tabs">
                {result.pages.map((page, index) => (
                  <button
                    key={page.page_number}
                    type="button"
                    className={`page-tab${index === activePage ? ' page-tab--active' : ''}`}
                    onClick={() => setActivePage(index)}
                  >
                    Page {page.page_number}
                  </button>
                ))}
              </div>
            )}

            <div className="section-label" style={{ marginTop: 18 }}>
              <span>Raw text{result.pages.length > 1 ? ` — page ${activePage + 1}` : ''}</span>
              <span>
                {(activePageData ?? { elements: [] }).elements.length} element
                {(activePageData ?? { elements: [] }).elements.length === 1 ? '' : 's'}
              </span>
            </div>
            <pre className="extraction-text-panel">
              {activePageData ? activePageData.text || '(no text extracted)' : result.raw_text}
            </pre>

            {metadataEntries.length > 0 && (
              <>
                <div className="section-label" style={{ marginTop: 18 }}>
                  <span>File metadata</span>
                </div>
                <div className="kv-grid">
                  {metadataEntries.map(([key, value]) => (
                    <div key={key} className="kv-row">
                      <span className="kv-key">{key}</span>
                      <span className="kv-value">{String(value)}</span>
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
