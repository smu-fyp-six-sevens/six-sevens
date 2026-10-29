import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import HighlightLayer from '../../components/HighlightLayer';
import PdfPagesView from '../../components/PdfPagesView';
import { highlightsFromReport } from '../../components/findingHighlights';
import type { FindingHighlight } from '../../components/findingHighlights';
import { useDocumentPreviewUrl } from '../../components/useDocumentPreviewUrl';
import { getDetectionReport } from '../../services/uploadService';
import type { PreviewKind } from '../../services/uploadService';

// What the findings panel needs to tie its rows to the boxes on the document.
export interface ReviewLink {
  // The report finding id ("layout_anomalies_tool-1") selected, from a row or a box.
  activeId: string | null;
  // Report finding ids with a box on the document.
  located: Map<string, FindingHighlight>;
  // Select a finding and scroll the document to its box.
  showOnDocument: (id: string) => void;
}

interface DocumentReviewModalProps {
  documentId: string;
  filename: string;
  heading: ReactNode;
  onClose: () => void;
  // The document's findings, with their review ticks and Save, beside the document.
  children: (link: ReviewLink) => ReactNode;
}

// Reviewing one document's findings with the document open beside them: each finding the checks could
// place is boxed on the page, so the officer can see it before ticking it as a real issue.
export default function DocumentReviewModal({
  documentId,
  filename,
  heading,
  onClose,
  children,
}: DocumentReviewModalProps) {
  const { previewKind, previewUrl, error } = useDocumentPreviewUrl(documentId, filename);
  const [highlights, setHighlights] = useState<FindingHighlight[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const documentRef = useRef<HTMLDivElement>(null);
  const findingsRef = useRef<HTMLDivElement>(null);

  // The boxes come from the document's detection report; the findings list doesn't carry them.
  useEffect(() => {
    let cancelled = false;
    getDetectionReport(documentId)
      .then((report) => {
        if (!cancelled) setHighlights(highlightsFromReport(report).highlights);
      })
      .catch(() => {
        // No boxes; the findings are still there to review.
      });
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const located = useMemo(() => new Map(highlights.map((h) => [h.id, h])), [highlights]);

  const showOnDocument = (id: string) => {
    setActiveId(id);
    requestAnimationFrame(() => {
      documentRef.current
        ?.querySelector(`[data-highlight-id="${CSS.escape(id)}"]`)
        ?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    });
  };

  // A box clicked: its finding is picked out in the list.
  const selectFromDocument = (id: string) => {
    setActiveId(id);
    requestAnimationFrame(() => {
      findingsRef.current
        ?.querySelector(`[data-finding-ids~="${CSS.escape(id)}"]`)
        ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });
  };

  return createPortal(
    <div className="preview-modal-backdrop" onClick={onClose}>
      <div
        className="review-modal"
        role="dialog"
        aria-modal="true"
        aria-label={`Review findings on ${filename}`}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="preview-modal-header">
          <div className="review-modal-heading">{heading}</div>
          <button type="button" className="preview-modal-close" onClick={onClose} aria-label="Close review" autoFocus>
            ×
          </button>
        </div>
        <div className="review-modal-body">
          <div ref={documentRef} className="review-modal-document">
            <HighlightedDocument
              filename={filename}
              previewKind={previewKind}
              previewUrl={previewUrl}
              error={error}
              highlights={highlights}
              activeId={activeId}
              onSelect={selectFromDocument}
            />
          </div>
          <div ref={findingsRef} className="review-modal-findings">
            {children({ activeId, located, showOnDocument })}
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

interface HighlightedDocumentProps {
  filename: string;
  previewKind: PreviewKind;
  previewUrl: string | null;
  error: string | null;
  highlights: FindingHighlight[];
  activeId: string | null;
  onSelect: (id: string) => void;
}

// The document under its findings' boxes: a PDF drawn page by page (the browser's own viewer can't be drawn
// over), an image at the pane's width. The messages match the document page's preview.
function HighlightedDocument({
  filename,
  previewKind,
  previewUrl,
  error,
  highlights,
  activeId,
  onSelect,
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
          onSelect={onSelect}
        />
      )}

      {!error && previewKind === 'image' && previewUrl && (
        <HighlightedImage
          url={previewUrl}
          filename={filename}
          highlights={highlights}
          activeId={activeId}
          onSelect={onSelect}
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
  onSelect,
}: {
  url: string;
  filename: string;
  highlights: FindingHighlight[];
  activeId: string | null;
  onSelect: (id: string) => void;
}) {
  const [natural, setNatural] = useState<{ width: number; height: number } | null>(null);

  return (
    <div className="preview-image-scroll">
      <div className="preview-image-stage">
        <img
          className="preview-image-highlighted"
          src={url}
          alt={`Preview of ${filename}`}
          onLoad={(event) =>
            setNatural({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })
          }
        />
        {natural && (
          <HighlightLayer
            highlights={highlights}
            pageNumber={1}
            pageWidth={natural.width}
            pageHeight={natural.height}
            units="px"
            activeId={activeId}
            onSelect={onSelect}
          />
        )}
      </div>
    </div>
  );
}
