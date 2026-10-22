import type { PreviewKind } from '../services/uploadService';

interface DocumentPreviewBodyProps {
  filename: string;
  previewKind: PreviewKind;
  previewUrl: string | null;
  error: string | null;
}

// The rendered document: PDF in the browser's own viewer, image as <img>. Used by the preview modal
// and the document page's preview pane.
export default function DocumentPreviewBody({ filename, previewKind, previewUrl, error }: DocumentPreviewBodyProps) {
  return (
    <div className="preview-modal-body">
      {error && <div className="preview-modal-error">{error}</div>}

      {!error && previewKind === 'unsupported' && (
        <div className="preview-modal-message">
          No inline preview is available for this file type.
          Use Download instead.
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
  );
}
