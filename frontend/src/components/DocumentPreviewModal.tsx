import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { getDocumentDownloadUrl } from '../services/uploadService';
import DocumentPreviewBody from './DocumentPreviewBody';
import { useDocumentPreviewUrl } from './useDocumentPreviewUrl';

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
  const { previewKind, previewUrl, error, setError } = useDocumentPreviewUrl(documentId, filename);
  const [isDownloading, setIsDownloading] = useState(false);

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

        <DocumentPreviewBody filename={filename} previewKind={previewKind} previewUrl={previewUrl} error={error} />
      </div>
    </div>,
    document.body,
  );
}
