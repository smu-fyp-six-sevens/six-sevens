import { useState } from 'react';
import HighlightLayer from './HighlightLayer';
import type { ActivePlace, HighlightOptions } from './HighlightLayer';
import PdfPagesView from './PdfPagesView';
import type { FindingHighlight } from './findingHighlights';
import type { PreviewKind } from '../services/uploadService';

interface HighlightedDocumentProps extends HighlightOptions {
  filename: string;
  previewKind: PreviewKind;
  previewUrl: string | null;
  error: string | null;
  highlights: FindingHighlight[];
  activeId: string | null;
  activePlace?: ActivePlace;
  onSelect: (id: string, area: number) => void;
  // Once the document has drawn (a PDF's first page, or the image).
  onReady?: () => void;
}

// The document under its findings' boxes: a PDF drawn page by page (the browser's own viewer can't be drawn
// over), an image at the pane's width. The messages match the document page's preview.
export default function HighlightedDocument({
  filename,
  previewKind,
  previewUrl,
  error,
  highlights,
  activeId,
  activePlace,
  onSelect,
  onReady,
  shade,
  detail,
}: HighlightedDocumentProps) {
  return (
    <div className="preview-modal-body">
      {error && <div className="preview-modal-error">{error}</div>}

      {!error && previewKind === 'unsupported' && (
        <div className="preview-modal-message">No inline preview is available for this file type.</div>
      )}

      {!error && previewKind !== 'unsupported' && !previewUrl && (
        <div className="preview-modal-message">Loading preview…</div>
      )}

      {!error && previewKind === 'pdf' && previewUrl && (
        <PdfPagesView
          url={previewUrl}
          filename={filename}
          highlights={highlights}
          activeId={activeId}
          activePlace={activePlace}
          onSelect={onSelect}
          onReady={onReady}
          shade={shade}
          detail={detail}
        />
      )}

      {!error && previewKind === 'image' && previewUrl && (
        <HighlightedImage
          url={previewUrl}
          filename={filename}
          highlights={highlights}
          activeId={activeId}
          activePlace={activePlace}
          onSelect={onSelect}
          onReady={onReady}
          shade={shade}
          detail={detail}
        />
      )}
    </div>
  );
}

// The image at the pane's width with the boxes over it; the checks measure an upload in its pixels.
function HighlightedImage({
  url,
  filename,
  highlights,
  activeId,
  activePlace,
  onSelect,
  onReady,
  shade,
  detail,
}: HighlightOptions & {
  url: string;
  filename: string;
  highlights: FindingHighlight[];
  activeId: string | null;
  activePlace?: ActivePlace;
  onSelect: (id: string, area: number) => void;
  onReady?: () => void;
}) {
  const [natural, setNatural] = useState<{ width: number; height: number } | null>(null);

  return (
    <div className="preview-image-scroll">
      <div className="preview-image-stage">
        <img
          className="preview-image-highlighted"
          src={url}
          alt={`Preview of ${filename}`}
          onLoad={(event) => {
            setNatural({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight });
            onReady?.();
          }}
        />
        {natural && (
          <HighlightLayer
            highlights={highlights}
            pageNumber={1}
            pageWidth={natural.width}
            pageHeight={natural.height}
            units="px"
            activeId={activeId}
            activePlace={activePlace}
            onSelect={onSelect}
            shade={shade}
            detail={detail}
          />
        )}
      </div>
    </div>
  );
}
