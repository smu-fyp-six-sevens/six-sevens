import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import XrayView from '../../components/XrayView';
import { highlightsFromReport } from '../../components/findingHighlights';
import type { FindingHighlight } from '../../components/findingHighlights';
import { useDocumentPreviewUrl } from '../../components/useDocumentPreviewUrl';
import { getDetectionReport } from '../../services/uploadService';

// What the findings panel needs to tie its rows to the boxes on the document.
export interface ReviewLink {
  // The report finding id ("layout_anomalies_tool-1") selected, from a row or a box, and which of its areas.
  activeId: string | null;
  activeArea: number;
  // Report finding ids with a box on the document.
  located: Map<string, FindingHighlight>;
  // Select a finding (at one of its areas, its first by default) and scroll the document to it.
  showOnDocument: (id: string, area?: number) => void;
}

interface DocumentReviewModalProps {
  documentId: string;
  filename: string;
  heading: ReactNode;
  onClose: () => void;
  // The report finding ids of each row of the findings list, in its order, so the document numbers and steps
  // through them the same way.
  order: string[][];
  // The document's findings, with their review ticks and Save, beside the document.
  children: (link: ReviewLink) => ReactNode;
}

// Reviewing one document's findings with the document open beside them, in X-ray: the page shaded but for
// each finding the checks could place, which glows, so the officer can see it before ticking it as a real issue.
export default function DocumentReviewModal({
  documentId,
  filename,
  heading,
  onClose,
  order,
  children,
}: DocumentReviewModalProps) {
  const { previewKind, previewUrl, error } = useDocumentPreviewUrl(documentId, filename);
  const [highlights, setHighlights] = useState<FindingHighlight[]>([]);
  const [active, setActive] = useState<{ id: string; area: number } | null>(null);
  const activeId = active?.id ?? null;
  const activeArea = active?.area ?? 0;
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

  // The X-ray view brings the selected place into view itself.
  const showOnDocument = (id: string, area = 0) => setActive({ id, area });

  // A place clicked or stepped to on the document: its finding is picked out in the list.
  const selectFromDocument = (id: string | null, area = 0) => {
    setActive(id ? { id, area } : null);
    if (!id) return;
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
          <div className="review-modal-document">
            <XrayView
              filename={filename}
              previewKind={previewKind}
              previewUrl={previewUrl}
              error={error}
              highlights={highlights}
              order={order}
              activeId={activeId}
              activeArea={activeArea}
              onSelect={selectFromDocument}
            />
          </div>
          <div ref={findingsRef} className="review-modal-findings">
            {children({ activeId, activeArea, located, showOnDocument })}
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
