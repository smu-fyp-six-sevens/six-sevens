import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  getDocumentDownloadUrl,
  previewKindForFilename,
} from '../services/uploadService';

interface DocumentPreviewModalProps {
  documentId: string;
  filename: string;
  onClose: () => void;
}

export default function DocumentPreviewModal({
  documentId,
  filename,
  onClose,
}: DocumentPreviewModalProps) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isDownloading, setIsDownloading] = useState(false);

  const previewKind = previewKindForFilename(filename);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setError(null);
      setPreviewUrl(null);

      if (previewKind === 'unsupported') {
        return;
      }

      try {
        const result = await getDocumentDownloadUrl(documentId, { inline: true });
        if (!cancelled) {
          setPreviewUrl(result.signed_url);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load preview.');
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
    // previewKind is derived from filename, which doesn't change per document.
  }, [documentId, previewKind]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        onClose();
      }
    }

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  async function handleDownload() {
    setIsDownloading(true);
    setError(null);

    try {
      // Deliberately a separate (non-inline) request, not the preview URL:
      // only this one has Storage's `download` option set, which is what
      // makes the browser save the file instead of trying to render it.
      const result = await getDocumentDownloadUrl(documentId);
      window.open(result.signed_url, '_blank', 'noopener,noreferrer');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to download document.');
    } finally {
      setIsDownloading(false);
    }
  }

  return createPortal(
    <div className="preview-modal-backdrop" onClick={onClose}>
      <div
        className="preview-modal"
        role="dialog"
        aria-modal="true"
        aria-label={`Preview of ${filename}`}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="preview-modal-header">
          <span className="preview-modal-filename">{filename}</span>
          <div className="preview-modal-header-actions">
            <button
              type="button"
              className="preview-modal-download"
              onClick={() => void handleDownload()}
              disabled={isDownloading}
            >
              {isDownloading ? 'Preparing…' : '⭳ Download'}
            </button>
            <button
              type="button"
              className="preview-modal-close"
              onClick={onClose}
              aria-label="Close preview"
              autoFocus
            >
              ×
            </button>
          </div>
        </div>

        <div className="preview-modal-body">
          {error && <div className="preview-modal-error">{error}</div>}

          {!error && previewKind === 'unsupported' && (
            <div className="preview-modal-message">
              No inline preview is available for this file type.
              Use Download above instead.
            </div>
          )}

          {!error && previewKind !== 'unsupported' && !previewUrl && (
            <div className="preview-modal-message">Loading preview…</div>
          )}

          {!error && previewKind === 'pdf' && previewUrl && (
            <iframe
              className="preview-modal-frame"
              src={previewUrl}
              title={`Preview of ${filename}`}
            />
          )}

          {!error && previewKind === 'image' && previewUrl && (
            <img
              className="preview-modal-image"
              src={previewUrl}
              alt={`Preview of ${filename}`}
            />
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
